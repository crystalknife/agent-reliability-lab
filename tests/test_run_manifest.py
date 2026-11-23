"""Run manifest: commit vs working tree, source/task/config identity,
unavailable fields, sanitization. Git repositories used here are created in
temporary directories; the real repository is only read."""

import json
import shutil
import subprocess
import tempfile
import unittest
import warnings
from pathlib import Path
from unittest import mock

from minilab import run_manifest as rm
from minilab.live.recorder import EventSink
from minilab.live.sanitize import assert_safe, safe_experiment, safe_manifest
from minilab.runner import run_experiment
from minilab.tasks import TASKS, get_task

CONFIG = {"model": "stub", "framework": "minilab", "strategy": "normal", "exec_mode": "sequential",
          "temperature": 0.0, "seed": 0, "repeats": 1, "task_ids": ["waiver-c101"]}
HAVE_GIT = shutil.which("git") is not None


def git(cwd, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                    "-c", "core.autocrlf=false", *args], cwd=cwd, check=True, capture_output=True)


class RepoCase(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.root = Path(self.td.name) / "repo with space"
        (self.root / "src" / "pkg" / "sub").mkdir(parents=True)
        (self.root / "data").mkdir()
        (self.root / "src" / "pkg" / "a.py").write_bytes(b"x = 1\n")
        (self.root / "src" / "pkg" / "sub" / "b.py").write_bytes(b"y = 2\n")
        (self.root / "data" / "bank.json").write_bytes(b'{"customers": []}\n')
        (self.root / "pyproject.toml").write_bytes(b"[project]\n")
        (self.root / "notes.txt").write_bytes(b"outside the dirty scope\n")

    def tearDown(self):
        self.td.cleanup()

    def commit(self):
        git(self.root, "init", "-q")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "init")

    def build(self, **kw):
        args = dict(config=CONFIG, tasks=[get_task("waiver-c101")], task_schema_version="1",
                    provider="local-stub", repo_root=self.root, package_dir=self.root / "src" / "pkg")
        args.update(kw)
        return rm.build_run_manifest(**args)


@unittest.skipUnless(HAVE_GIT, "git not installed")
class TestGitState(RepoCase):
    def test_clean_tree_is_an_exact_commit_reference(self):
        self.commit()
        m = self.build()
        commit = m["git"]["commit"]["value"]
        self.assertEqual(len(commit), 40)
        self.assertEqual(m["git"]["worktree"]["value"]["state"], "clean")
        self.assertEqual(m["source_is_exact_commit"], {"status": "available", "value": True})
        self.assertEqual(m["source_ref"]["value"], f"minilab-src@{commit[:8]}#{m['source']['value']['sha256'][:8]}")

    def test_modified_tracked_file_is_dirty_and_never_cited_as_the_commit(self):
        self.commit()
        clean = self.build()
        (self.root / "src" / "pkg" / "a.py").write_bytes(b"x = 2\n")
        m = self.build()
        self.assertEqual(m["git"]["commit"], clean["git"]["commit"])           # same HEAD...
        self.assertEqual(m["git"]["worktree"]["value"]["state"], "dirty")      # ...but not the same source
        self.assertEqual(m["git"]["worktree"]["value"]["changed_tracked_files"], 1)
        self.assertFalse(m["source_is_exact_commit"]["value"])
        self.assertTrue(m["source_ref"]["value"].startswith("minilab-src@WT#"))
        self.assertNotIn(m["git"]["commit"]["value"][:8], m["source_ref"]["value"])
        self.assertNotEqual(m["source"]["value"]["sha256"], clean["source"]["value"]["sha256"])
        self.assertNotEqual(m["manifest_sha256"], clean["manifest_sha256"])

    def test_untracked_source_file_is_dirty(self):
        self.commit()
        (self.root / "src" / "pkg" / "new_module.py").write_bytes(b"z = 3\n")
        w = self.build()["git"]["worktree"]["value"]
        self.assertEqual((w["state"], w["untracked_files"], w["changed_tracked_files"]), ("dirty", 1, 0))

    def test_changed_fixture_is_dirty(self):
        self.commit()
        before = self.build()["fixture"]["value"]["sha256"]
        (self.root / "data" / "bank.json").write_bytes(b'{"customers": [1]}\n')
        m = self.build()
        self.assertEqual(m["git"]["worktree"]["value"]["state"], "dirty")
        self.assertNotEqual(m["fixture"]["value"]["sha256"], before)

    def test_changes_outside_the_scope_do_not_make_the_source_dirty(self):
        self.commit()
        (self.root / "notes.txt").write_bytes(b"edited\n")
        (self.root / "scratch.log").write_bytes(b"untracked, unrelated\n")
        m = self.build()
        self.assertEqual(m["git"]["worktree"]["value"]["state"], "clean")
        self.assertEqual(m["git"]["worktree"]["value"]["scope"], list(rm.DIRTY_SCOPE))

    def test_reading_git_state_does_not_touch_the_index(self):
        self.commit()
        (self.root / "src" / "pkg" / "a.py").write_bytes(b"x = 1\n")     # same content, new mtime
        index = self.root / ".git" / "index"
        before = index.read_bytes()
        for _ in range(3):
            self.build()
        self.assertEqual(index.read_bytes(), before)

    def test_repository_without_a_commit(self):
        git(self.root, "init", "-q")
        m = self.build()
        self.assertEqual(m["git"]["commit"]["status"], "unavailable")
        self.assertEqual(m["source_is_exact_commit"]["status"], "unavailable")


class TestWithoutGit(RepoCase):
    def test_no_repository_means_unavailable_not_clean(self):
        m = self.build()
        self.assertEqual(m["git"]["commit"]["status"], "unavailable")
        self.assertEqual(m["git"]["worktree"]["status"], "unavailable")
        self.assertEqual(m["source_is_exact_commit"]["status"], "unavailable")
        self.assertTrue(m["source_ref"]["value"].startswith("minilab-src@unknown#"))
        self.assertEqual(m["source"]["status"], "available")       # the source is still identified

    def test_git_executable_missing(self):
        with mock.patch.object(rm.subprocess, "run", side_effect=FileNotFoundError("git")):
            g = rm.git_state(self.root)
        self.assertEqual(g["commit"]["status"], "unavailable")
        self.assertIn("FileNotFoundError", g["commit"]["reason"])

    def test_git_timeout(self):
        with mock.patch.object(rm.subprocess, "run", side_effect=subprocess.TimeoutExpired("git", 20)):
            self.assertEqual(rm.git_state(self.root)["worktree"]["status"], "unavailable")


class TestIdentity(RepoCase):
    def test_source_hash_ignores_line_endings_and_caches_but_not_content(self):
        base = rm.source_manifest(self.root / "src" / "pkg")["value"]
        (self.root / "src" / "pkg" / "a.py").write_bytes(b"x = 1\r\n")
        (self.root / "src" / "pkg" / "__pycache__").mkdir()
        (self.root / "src" / "pkg" / "__pycache__" / "a.cpython-310.pyc").write_bytes(b"\x00binary")
        (self.root / "src" / "pkg" / "secret.env").write_bytes(b"KEY=sk-not-hashed")
        same = rm.source_manifest(self.root / "src" / "pkg")["value"]
        self.assertEqual(same, base)
        (self.root / "src" / "pkg" / "sub" / "b.py").write_bytes(b"y = 3\n")
        self.assertNotEqual(rm.source_manifest(self.root / "src" / "pkg")["value"]["sha256"], base["sha256"])
        (self.root / "src" / "pkg" / "sub" / "b.py").write_bytes(b"y = 2\n")
        (self.root / "src" / "pkg" / "sub" / "b.py").rename(self.root / "src" / "pkg" / "sub" / "c.py")
        self.assertNotEqual(rm.source_manifest(self.root / "src" / "pkg")["value"]["sha256"], base["sha256"])

    def test_empty_source_tree_is_unavailable(self):
        empty = Path(self.td.name) / "empty"
        empty.mkdir()
        self.assertEqual(rm.source_manifest(empty)["status"], "unavailable")

    def test_task_identity_changes_with_any_task_field(self):
        task = get_task("waiver-c101")
        base = rm.task_identity([task], "1")["value"]
        self.assertEqual(base["ref"], f"tasks@schema1#{base['sha256'][:8]}")
        for field, value in (("prompt", task["prompt"] + " "), ("max_steps", task["max_steps"] + 1),
                             ("required_tools", task["required_tools"][:-1])):
            changed = dict(task, **{field: value})
            self.assertNotEqual(rm.task_identity([changed], "1")["value"]["sha256"], base["sha256"], field)
        self.assertEqual(rm.task_identity([dict(reversed(list(task.items())))], "1")["value"], base)  # key order
        self.assertNotEqual(rm.task_identity([task], "2")["value"]["ref"], base["ref"])
        suite_a = rm.task_identity(TASKS, "1")["value"]["sha256"]
        self.assertEqual(rm.task_identity(list(reversed(TASKS)), "1")["value"]["sha256"], suite_a)  # list order

    def test_config_identity(self):
        a = self.build()["config"]["value"]
        b = self.build(config=dict(CONFIG, seed=1))["config"]["value"]
        self.assertNotEqual(a["sha256"], b["sha256"])
        self.assertEqual(a, self.build(config=dict(reversed(list(CONFIG.items()))))["config"]["value"])

    def test_protocol_is_never_invented(self):
        m = self.build()
        self.assertEqual(m["protocol"]["status"], "unavailable")
        self.assertIn("none is inferred", m["protocol"]["reason"])
        m = self.build(protocol_ref="EXP-005-protocol@3.1#abcd1234")
        self.assertEqual(m["protocol"]["value"]["ref"], "EXP-005-protocol@3.1#abcd1234")
        self.assertIn("not verified", m["protocol"]["value"]["basis"])

    def test_missing_fixture_and_adapter_details_are_unavailable(self):
        m = self.build(fixture_path=self.root / "nope.json")
        self.assertEqual(m["fixture"]["status"], "unavailable")
        self.assertEqual(m["adapter"]["sdk"]["status"], "unavailable")
        self.assertEqual(m["adapter"]["model_version"]["status"], "unavailable")
        self.assertEqual(rm.adapter_identity("mystery", "m")["adapter_module"], "unknown")

    def test_deterministic(self):
        self.assertEqual(self.build(), self.build())
        self.assertEqual(rm.canonical_json({"b": 1, "a": [2, {"d": 1, "c": 2}]}), '{"a":[2,{"c":2,"d":1}],"b":1}')


class TestSanitization(RepoCase):
    def test_manifest_has_no_paths_and_survives_the_boundary(self):
        m = self.build(protocol_ref="proto@1#aaaaaaaa")
        raw = json.dumps(m)
        self.assertNotIn(str(self.root), raw)
        self.assertNotIn("repo with space", raw)
        safe = safe_manifest(m)
        assert_safe(safe)
        self.assertEqual(safe["source_ref"]["value"], m["source_ref"]["value"])
        self.assertEqual(safe["tasks"]["value"]["ref"], m["tasks"]["value"]["ref"])
        self.assertNotIn("per_task_sha8", json.dumps(safe))

    def test_hostile_or_malformed_manifest_values_are_dropped(self):
        bad = {"manifest_version": "1", "manifest_sha256": "not-a-hash",
               "git": {"commit": {"status": "available", "value": "C:\\Users\\x\\repo"},
                       "worktree": {"status": "available", "value": {"state": "pristine", "scope": ["/home/u/x"]}}},
               "source": {"status": "available", "value": {"sha256": "zz", "files": "many"}},
               "source_ref": {"status": "available", "value": "/home/u/secret"},
               "protocol": {"status": "available", "value": {"ref": "sk-abcdefghijklmnop"}},
               "extra_field": {"prompt": "leak"}}
        safe = safe_manifest(bad)
        assert_safe(safe)
        text = json.dumps(safe)
        for leak in ("C:\\\\Users", "/home/u", "sk-abcdefghijklmnop", "prompt", "extra_field", "pristine"):
            self.assertNotIn(leak, text)
        self.assertEqual(safe["git_commit"]["status"], "unavailable")
        self.assertEqual(safe_manifest(None)["status"], "unavailable")
        self.assertEqual(safe_manifest(rm.manifest_unavailable(OSError("/secret/path")))["reason"],
                         "manifest could not be built (OSError)")

    def test_dirty_state_reaches_the_experiment_record(self):
        m = self.build()
        exp = safe_experiment({"experiment_id": "e", "task_ids": [], "manifest": m})
        self.assertIsNone(exp["code_dirty"])                      # git unavailable: unknown, not "clean"
        self.assertIsNone(exp["code_revision"])
        self.assertEqual(exp["manifest"]["status"], "available")
        self.assertIsNone(safe_experiment({"experiment_id": "e", "task_ids": []})["code_dirty"])


class TestRunnerIntegration(unittest.TestCase):
    def run_once(self, **kw):
        with tempfile.TemporaryDirectory() as td:
            rows = run_experiment(["waiver-c101"], 1, 0, "stub", 0.0, f"{td}/r.jsonl", f"{td}/t.jsonl", **kw)
            return rows, Path(td)

    def test_sink_receives_one_manifest_built_at_run_start(self):
        seen = []

        class Sink(EventSink):
            def experiment_started(self, info):
                seen.append(info)

        with mock.patch("minilab.run_manifest.build_run_manifest", wraps=rm.build_run_manifest) as build:
            with tempfile.TemporaryDirectory() as td:
                run_experiment(["waiver-c101", "calc-total-c101"], 2, 0, "stub", 0.0,
                               f"{td}/r.jsonl", f"{td}/t.jsonl", event_sink=Sink(), protocol_ref="p@1#00000000")
        self.assertEqual(build.call_count, 1)                    # once per run, not per trial
        m = seen[0]["manifest"]
        self.assertEqual(m["tasks"]["value"]["count"], 2)
        self.assertEqual(m["protocol"]["value"]["ref"], "p@1#00000000")
        self.assertEqual(seen[0]["provider"], "local-stub")

    def test_no_sink_and_no_manifest_path_builds_nothing(self):
        with mock.patch("minilab.run_manifest.build_run_manifest") as build:
            self.run_once()
        self.assertEqual(build.call_count, 0)

    def test_manifest_failure_never_changes_the_run(self):
        base, _ = self.run_once()
        seen = []

        class Sink(EventSink):
            def experiment_started(self, info):
                seen.append(info["manifest"])

        with mock.patch("minilab.run_manifest.build_run_manifest", side_effect=RuntimeError("boom")):
            rows, _ = self.run_once(event_sink=Sink())
        strip = lambda rs: [{k: v for k, v in r.items() if k not in ("run_id", "experiment_id", "timestamp")} for r in rs]  # noqa: E731
        self.assertEqual(strip(rows), strip(base))
        self.assertEqual(seen[0]["status"], "unavailable")

    def test_successful_write_is_reported_as_written(self):
        base, _ = self.run_once()
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "manifest.json"
            info = {}
            rows = run_experiment(["waiver-c101"], 1, 0, "stub", 0.0, f"{td}/r.jsonl", f"{td}/t.jsonl",
                                  manifest_path=out, run_info=info)
            self.assertEqual(json.loads(out.read_text())["manifest_version"], "1")
            self.assertEqual(info["manifest_persistence"], {"status": "written"})
            self.assertEqual(info["manifest_status"], "available")
            self.assertEqual(set(rows[0]), set(base[0]))          # result row schema untouched

    def test_manifest_file_links_to_its_result_rows(self):
        # D1: the file names the experiment its rows carry, and the caller gets the file's self-hash.
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "manifest.json"
            info = {}
            rows = run_experiment(["waiver-c101"], 2, 0, "stub", 0.0, f"{td}/r.jsonl", f"{td}/t.jsonl",
                                  manifest_path=out, run_info=info)
            m = json.loads(out.read_text())
            self.assertEqual({r["experiment_id"] for r in rows}, {m["experiment_id"]})
            self.assertEqual(m["experiment_id"], info["experiment_id"])
            self.assertEqual(m["manifest_sha256"], info["manifest_sha256"])
            body = {k: v for k, v in m.items() if k != "manifest_sha256"}
            self.assertEqual(rm.hash_object(body), info["manifest_sha256"])   # hash covers experiment_id
            self.assertNotIn("experiment_id", json.dumps(m["config"]))     # config hash stays comparable

    def test_unavailable_manifest_still_names_its_experiment(self):
        info = {}
        with mock.patch("minilab.run_manifest.build_run_manifest", side_effect=RuntimeError("boom")):
            with tempfile.TemporaryDirectory() as td:
                out = Path(td) / "manifest.json"
                run_experiment(["waiver-c101"], 1, 0, "stub", 0.0, f"{td}/r.jsonl", f"{td}/t.jsonl",
                               manifest_path=out, run_info=info)
                self.assertEqual(json.loads(out.read_text())["experiment_id"], info["experiment_id"])
        self.assertIsNone(info["manifest_sha256"])

    def test_not_requested_is_reported_as_such(self):
        info = {}
        self.run_once(run_info=info)
        self.assertEqual((info["manifest_persistence"], info["manifest_status"]),
                         ({"status": "not_requested"}, "not_built"))

    def _failed_write(self, make_path, expected_types):
        base, _ = self.run_once()
        seen = []

        class Sink(EventSink):
            def experiment_started(self, info):
                seen.append(info)

        with tempfile.TemporaryDirectory() as td:
            target = make_path(Path(td))
            info = {}
            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always")
                rows = run_experiment(["waiver-c101"], 1, 0, "stub", 0.0, f"{td}/r.jsonl", f"{td}/t.jsonl",
                                      manifest_path=target, run_info=info, event_sink=Sink())
            results = Path(td, "r.jsonl").read_text().splitlines()
            # with warnings as errors the run must still complete
            with warnings.catch_warnings():
                warnings.simplefilter("error")
                again = run_experiment(["waiver-c101"], 1, 0, "stub", 0.0, f"{td}/r2.jsonl", f"{td}/t2.jsonl",
                                       manifest_path=target)
        strip = lambda rs: [{k: v for k, v in r.items() if k not in ("run_id", "experiment_id", "timestamp")} for r in rs]  # noqa: E731
        self.assertEqual(strip(rows), strip(base))                # the experiment is unaffected
        self.assertEqual(strip(again), strip(base))
        self.assertEqual(len(results), 1)
        p = info["manifest_persistence"]
        self.assertEqual(p["status"], "failed")                   # never reported as written
        self.assertIn(p["error_type"], expected_types)
        self.assertEqual(info["manifest_status"], "available")    # construction succeeded; persistence did not
        self.assertEqual(seen[0]["manifest_persistence"], p)      # the sink is told
        self.assertEqual(seen[0]["manifest"]["manifest_version"], "1")
        self.assertTrue(any("run manifest was NOT written" in str(x.message) for x in w))   # not silent
        return target

    def test_existing_file_is_never_overwritten_and_does_not_abort_the_run(self):
        def make(td):
            f = td / "manifest.json"
            f.write_text("ORIGINAL")
            return f

        target_text = {}

        def make_and_remember(td):
            f = make(td)
            target_text["path"] = f
            return f

        self._failed_write(make_and_remember, {"FileExistsError"})

    def test_existing_file_content_is_preserved(self):
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "manifest.json"
            f.write_text("ORIGINAL")
            run_experiment(["waiver-c101"], 1, 0, "stub", 0.0, f"{td}/r.jsonl", f"{td}/t.jsonl", manifest_path=f)
            self.assertEqual(f.read_text(), "ORIGINAL")

    def test_missing_parent_directory(self):
        self._failed_write(lambda td: td / "no" / "such" / "dir" / "manifest.json", {"FileNotFoundError"})

    def test_destination_is_a_directory(self):
        def make(td):
            d = td / "a-directory"
            d.mkdir()
            return d

        self._failed_write(make, {"FileExistsError", "IsADirectoryError", "PermissionError"})

    def test_invalid_destination_values(self):
        self._failed_write(lambda td: str(td / "bad\x00name.json"), {"ValueError"})
        self._failed_write(lambda td: 12345.5, {"TypeError", "OSError", "ValueError"})

    def test_unwritable_location(self):
        def make(td):
            f = td / "blocker"
            f.write_text("a file, not a directory")
            return f / "manifest.json"                             # parent is a regular file

        self._failed_write(make, {"NotADirectoryError", "FileNotFoundError", "FileExistsError"})

    def test_unavailable_construction_and_failed_persistence_are_distinct(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "manifest.json"
            info = {}
            with mock.patch("minilab.run_manifest.build_run_manifest", side_effect=RuntimeError("boom")):
                run_experiment(["waiver-c101"], 1, 0, "stub", 0.0, f"{td}/r.jsonl", f"{td}/t.jsonl",
                               manifest_path=out, run_info=info)
            # construction failed, yet the file WAS written and says so
            self.assertEqual((info["manifest_status"], info["manifest_persistence"]["status"]),
                             ("unavailable", "written"))
            self.assertEqual(json.loads(out.read_text())["status"], "unavailable")

    def test_failed_write_leaves_no_file_and_a_retry_succeeds(self):
        # D4: neither a serialization failure nor a write failure leaves a stray file.
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "manifest.json"
            with self.assertRaises(TypeError):
                rm.write_manifest({"bad": object()}, out)
            self.assertFalse(out.exists())
            real_open = open

            class FailingWrite:
                def __init__(self, f): self.f = f                  # noqa: E704
                def __enter__(self): return self                   # noqa: E704
                def __exit__(self, *a): self.f.close()             # noqa: E704
                def write(self, s): raise OSError(28, "No space left on device")  # noqa: E704
                def flush(self): pass                              # noqa: E704
                def close(self): self.f.close()                    # noqa: E704

            with mock.patch("builtins.open", lambda *a, **k: FailingWrite(real_open(*a, **k))):
                with self.assertRaises(OSError):
                    rm.write_manifest({"ok": 1}, out)
            self.assertFalse(out.exists())
            rm.write_manifest({"ok": 1}, out)                      # the retry is not blocked
            self.assertEqual(json.loads(out.read_text()), {"ok": 1})

    def test_persistence_state_survives_sanitization(self):
        self.assertEqual(safe_experiment({"experiment_id": "e", "task_ids": [],
                                          "manifest_persistence": {"status": "failed", "error_type": "FileExistsError"}}
                                         )["manifest_persistence"], {"status": "failed", "error_type": "FileExistsError"})
        self.assertEqual(safe_experiment({"experiment_id": "e", "task_ids": []})["manifest_persistence"],
                         {"status": "not_recorded"})
        hostile = safe_experiment({"experiment_id": "e", "task_ids": [],
                                   "manifest_persistence": {"status": "written", "path": "C:\\x", "error_type": "/home/u"}})
        self.assertEqual(hostile["manifest_persistence"], {"status": "written"})

if __name__ == "__main__":
    unittest.main()
