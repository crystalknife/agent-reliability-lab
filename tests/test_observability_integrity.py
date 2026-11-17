"""Observability integrity: a failing dashboard sink can never change a run,
and a recording with failed writes is durably marked incomplete.

All runs are SYNTHETIC (offline stub model, temporary files).
"""

import contextlib
import io
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.request
import warnings
from pathlib import Path

from unittest import mock

from minilab.live import sanitize
from minilab.live.recorder import EventSink, LiveRecorder
from minilab.live.sanitize import UnsafePayload
from minilab.live.server import LiveServer, public_experiment
from minilab.live.store import RECORDING_STATES, RunStore, recording_integrity
from minilab.runner import _make_live_sink, _SinkGuard, run_experiment
from minilab.runner import main as runner_main

CLI_ENV = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
           "PYTHONDONTWRITEBYTECODE": "1"}

TASK_IDS = ["waiver-c101", "retrieval-bal-a103", "calc-total-c101"]
VOLATILE = ("run_id", "experiment_id", "timestamp")


def run(sink=None, model="stub"):
    with tempfile.TemporaryDirectory() as td:
        rows = run_experiment(TASK_IDS, 1, 0, model, 0.0, f"{td}/r.jsonl", f"{td}/t.jsonl", event_sink=sink)
        results = Path(td, "r.jsonl").read_text().splitlines()
        trajs = Path(td, "t.jsonl").read_text().splitlines()
    return rows, results, trajs


def stable(rows):
    return [{k: v for k, v in r.items() if k not in VOLATILE} for r in rows]


def stable_lines(lines):
    out = []
    for line in lines:
        d = json.loads(line)
        for k in VOLATILE:
            d.pop(k, None)
        out.append(d)
    return out


class FlakyStore(RunStore):
    """A RunStore whose named methods raise for their first `times` calls
    (times=None: always)."""

    def __init__(self, path, fail=None):
        super().__init__(path)
        self.fail = dict(fail or {})
        self.calls = {}

    def _maybe_fail(self, name):
        self.calls[name] = self.calls.get(name, 0) + 1
        if name in self.fail and (self.fail[name] is None or self.calls[name] <= self.fail[name]):
            raise sqlite3.OperationalError("database is locked")     # the realistic store fault


for _name in ("start_experiment", "create_experiment", "trial_started", "trial_finished",
              "finish_experiment", "note_recording_failure", "close_recording", "ensure_experiment"):
    def _make(name):
        def method(self, *a, **k):
            self._maybe_fail(name)
            return getattr(RunStore, name)(self, *a, **k)
        return method
    setattr(FlakyStore, _name, _make(_name))


class Base(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.db = Path(self.td.name) / "runs.sqlite"
        self.baseline = run(None)

    def tearDown(self):
        self.td.cleanup()

    def record(self, fail):
        store = FlakyStore(self.db, fail)
        rec = LiveRecorder(store)
        with warnings.catch_warnings():
            warnings.simplefilter("error")          # any warning would raise: the run must still finish
            out = run(rec)
        # canonical evidence is identical to a run with no dashboard at all
        self.assertEqual(stable(out[0]), stable(self.baseline[0]))
        self.assertEqual(stable_lines(out[1]), stable_lines(self.baseline[1]))
        self.assertEqual(stable_lines(out[2]), stable_lines(self.baseline[2]))
        exps = RunStore(self.db).list_experiments()
        return rec, (exps[0] if exps else None)


class TestRecordingIntegrity(Base):
    def test_normal_run_is_complete(self):
        rec, exp = self.record({})
        integ = recording_integrity(exp)
        self.assertEqual(rec.failures, 0)
        self.assertEqual((integ["status"], integ["write_failures"], integ["reasons"]), ("complete", 0, []))
        self.assertEqual(exp["status"], "completed")

    def test_experiment_start_failure(self):
        rec, exp = self.record({"start_experiment": 1})
        integ = recording_integrity(exp)
        self.assertEqual(integ["status"], "incomplete")
        self.assertEqual(integ["failures_by_hook"], {"experiment_started": 1})
        self.assertEqual(integ["failure_types"], {"OperationalError": 1})
        self.assertEqual(exp["trials_finished"], 3)       # later records were still kept

    def test_trial_start_failure(self):
        rec, exp = self.record({"trial_started": 1})
        integ = recording_integrity(exp)
        self.assertEqual(integ["status"], "incomplete")
        self.assertEqual(integ["failures_by_hook"], {"trial_started": 1})
        self.assertEqual(exp["trials_finished"], 3)       # the finish was kept, not silently dropped

    def test_trial_finish_failure(self):
        rec, exp = self.record({"trial_finished": 1})
        integ = recording_integrity(exp)
        self.assertEqual(integ["status"], "incomplete")
        self.assertEqual(integ["failures_by_hook"], {"trial_finished": 1})
        self.assertEqual((exp["trials_started"], exp["trials_finished"]), (3, 2))
        self.assertTrue(any("no finish record" in r for r in integ["reasons"]))
        self.assertTrue(any("2 of 3 expected" in r for r in integ["reasons"]))

    def test_experiment_finish_failure_is_not_a_failed_run(self):
        rec, exp = self.record({"finish_experiment": 1})
        integ = recording_integrity(exp)
        self.assertEqual(integ["status"], "incomplete")
        self.assertEqual(integ["failures_by_hook"], {"experiment_finished": 1})
        self.assertNotEqual(exp["status"], "failed")      # an observability fault is not a run failure
        self.assertIsNone(exp["error"])

    def test_multiple_failures_are_all_counted(self):
        rec, exp = self.record({"trial_started": 2, "trial_finished": 1})
        integ = recording_integrity(exp)
        self.assertEqual(integ["status"], "incomplete")
        self.assertEqual(integ["write_failures"], 3)
        self.assertEqual(rec.failures, 3)
        self.assertEqual(integ["failures_by_hook"], {"trial_finished": 1, "trial_started": 2})

    def test_failure_note_that_cannot_be_written_is_retried_later(self):
        rec, exp = self.record({"trial_started": 1, "note_recording_failure": 1})
        integ = recording_integrity(exp)
        self.assertEqual(integ["status"], "incomplete")
        self.assertEqual(integ["failures_by_hook"], {"trial_started": 1})
        self.assertEqual(rec._unnoted, [])

    def test_start_failure_with_no_note_ever_written_is_never_complete(self):
        # M12: a trial-start write failed once, no failure note could ever be
        # written, and the finish succeeded. The record must not read complete.
        rec, exp = self.record({"trial_started": 1, "note_recording_failure": None})
        self.assertEqual(recording_integrity(exp)["status"], "unavailable")

    def test_cli_reports_an_incomplete_live_recording(self):
        # D8: the CLI's LIVE RECORDING INCOMPLETE line, with a sink whose hook fails.
        class Broken(EventSink):
            def trial_finished(self, row, trajectory):
                raise sqlite3.OperationalError("database is locked")

        res = Path(self.td.name) / "r.jsonl"
        argv = ["minilab.runner", "--model", "stub", "--tasks", "waiver-c101", "--results", str(res),
                "--trajectories", str(Path(self.td.name) / "t.jsonl"), "--live-db", str(self.db)]
        err = io.StringIO()
        with mock.patch.object(sys, "argv", argv), \
                mock.patch("minilab.runner._make_live_sink", return_value=(Broken(), None)), \
                contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()), \
                warnings.catch_warnings():
            warnings.simplefilter("error")
            runner_main()
        self.assertIn("LIVE RECORDING INCOMPLETE: 1 dashboard write(s) failed", err.getvalue())
        self.assertEqual(len(res.read_text().splitlines()), 1)      # the run itself completed

    def test_unclosed_record_is_unavailable_not_complete(self):
        rec, exp = self.record({"close_recording": None})
        # the close failed, which is itself a noted failure -> incomplete
        self.assertEqual(recording_integrity(exp)["status"], "incomplete")
        # and a finished row with no integrity record at all is `unavailable`
        legacy = dict(exp, recording=None)
        integ = recording_integrity(legacy)
        self.assertEqual(integ["status"], "unavailable")
        self.assertIn("completeness is not established", integ["reasons"][0])

    def test_total_store_failure_leaves_the_run_untouched_and_nothing_claimed(self):
        always = {n: None for n in ("start_experiment", "create_experiment", "trial_started",
                                    "trial_finished", "finish_experiment", "note_recording_failure",
                                    "close_recording", "ensure_experiment")}
        rec, exp = self.record(always)
        self.assertIsNone(exp)                             # nothing recorded: the API has no such run
        self.assertEqual(rec.failures, 8)                  # 1 start + 3 + 3 + 1 finish
        self.assertEqual(len(rec._unnoted), 8)             # and none could be noted durably

    def test_running_run_with_a_known_failure_is_already_incomplete(self):
        store = RunStore(self.db)
        store.create_experiment({"experiment_id": "x", "kind": "run", "total_trials": 2}, "running")
        self.assertEqual(recording_integrity(store.get_experiment("x"))["status"], "in_progress")
        store.note_recording_failure("x", "trial_finished", "OperationalError")
        self.assertEqual(recording_integrity(store.get_experiment("x"))["status"], "incomplete")
        types = [e["type"] for e in store.events_after(0)]
        self.assertIn("recording_problem", types)

    def test_states_vocabulary(self):
        self.assertEqual(RECORDING_STATES, ("complete", "incomplete", "unavailable", "in_progress"))


class TestRunnerIsolation(Base):
    def test_dashboard_disabled_entirely(self):
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            out = run(None)
        self.assertEqual(stable(out[0]), stable(self.baseline[0]))
        self.assertEqual(RunStore(self.db).list_experiments(), [])

    def test_every_hook_raising_under_warnings_as_errors(self):
        class AllBroken(EventSink):
            def __getattribute__(self, name):
                if name in ("experiment_started", "trial_started", "trial_finished",
                            "experiment_finished", "experiment_failed", "observability_report"):
                    def boom(*a, **k):
                        raise RuntimeError("sink bug with secret /home/u/key")
                    return boom
                return object.__getattribute__(self, name)

        sink = AllBroken()
        with warnings.catch_warnings():
            warnings.simplefilter("error")                  # warnings.warn() itself would raise
            out = run(sink)
        self.assertEqual(stable(out[0]), stable(self.baseline[0]))
        self.assertEqual(stable_lines(out[2]), stable_lines(self.baseline[2]))
        hooks = [f["hook"] for f in sink.runner_sink_failures]
        self.assertEqual(hooks, ["experiment_started"] + ["trial_started", "trial_finished"] * 3
                         + ["experiment_finished"])
        self.assertTrue(all(f["error_type"] == "RuntimeError" for f in sink.runner_sink_failures))

    def test_default_warning_configuration_still_warns(self):
        class Broken(EventSink):
            def trial_started(self, info):
                raise ValueError("x")

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            run(Broken())
        self.assertEqual(sum("event sink trial_started failed: ValueError" in str(x.message) for x in w), 3)

    def test_runner_seen_failures_reach_the_store_through_the_report_hook(self):
        store = RunStore(self.db)

        class RaisingRecorder(LiveRecorder):
            def trial_finished(self, row, trajectory):     # a bug outside the recorder's own guard
                raise TypeError("subclass bug")

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            out = run(RaisingRecorder(store))
        self.assertEqual(stable(out[0]), stable(self.baseline[0]))
        integ = recording_integrity(store.list_experiments()[0])
        self.assertEqual(integ["status"], "incomplete")
        self.assertEqual(integ["failures_by_hook"], {"trial_finished": 3})
        self.assertEqual(integ["failure_types"], {"TypeError": 3})

    def test_interrupts_are_not_swallowed(self):
        class Interrupting(EventSink):
            def trial_started(self, info):
                raise KeyboardInterrupt()

        with self.assertRaises(KeyboardInterrupt):
            run(Interrupting())

    def test_guard_accepts_a_missing_sink_and_missing_hooks(self):
        self.assertTrue(_SinkGuard(None).call("trial_started", {}))
        g = _SinkGuard(object())
        self.assertTrue(g.call("trial_started", {}))
        g.report("e")
        self.assertEqual(g.failures, [])


class TestFailureModes(Base):
    """Named failure modes of the optional dashboard. In every case the run's
    own outputs are compared with a run that had no dashboard."""

    def test_sqlite_unavailable_before_the_run(self):
        blocker = Path(self.td.name) / "a-file"
        blocker.write_text("not a directory")
        sink, error = _make_live_sink(str(blocker / "runs.sqlite"))
        self.assertIsNone(sink)
        self.assertIsInstance(error, str)                   # reported, not raised
        self.assertEqual(_make_live_sink(None), (None, None))
        good, error = _make_live_sink(str(self.db))
        self.assertIsNone(error)
        self.assertIsInstance(good, LiveRecorder)

    def test_cli_runs_when_the_live_database_cannot_be_opened(self):
        blocker = Path(self.td.name) / "a-file"
        blocker.write_text("x")
        res = Path(self.td.name) / "r.jsonl"
        out = subprocess.run(
            [sys.executable, "-W", "error", "-m", "minilab.runner", "--model", "stub", "--tasks", "waiver-c101",
             "--results", str(res), "--trajectories", str(Path(self.td.name) / "t.jsonl"),
             "--live-db", str(blocker / "runs.sqlite")],
            env=CLI_ENV, capture_output=True, text=True, timeout=120)
        self.assertEqual(out.returncode, 0, out.stderr[-400:])
        self.assertIn("LIVE RECORDING DISABLED", out.stderr)
        self.assertEqual(len(res.read_text().splitlines()), 1)      # the experiment ran and wrote its row

    def test_cli_reports_a_manifest_that_was_not_written(self):
        existing = Path(self.td.name) / "manifest.json"
        existing.write_text("KEEP")
        res = Path(self.td.name) / "r.jsonl"
        out = subprocess.run(
            [sys.executable, "-m", "minilab.runner", "--model", "stub", "--tasks", "waiver-c101",
             "--results", str(res), "--trajectories", str(Path(self.td.name) / "t.jsonl"),
             "--manifest-out", str(existing)],
            env=CLI_ENV, capture_output=True, text=True, timeout=120)
        self.assertEqual(out.returncode, 0, out.stderr[-400:])
        self.assertIn("MANIFEST NOT WRITTEN (FileExistsError)", out.stderr)
        self.assertNotIn("wrote run manifest", out.stdout)
        self.assertEqual(existing.read_text(), "KEEP")
        self.assertEqual(len(res.read_text().splitlines()), 1)

    def test_sqlite_becomes_unavailable_during_the_run(self):
        store = RunStore(self.db)
        dead = Path(self.td.name) / "gone"
        dead.mkdir()

        class Recorder(LiveRecorder):
            def trial_finished(self, row, trajectory):
                super().trial_finished(row, trajectory)
                self.store.path = str(dead)              # from now on every write hits a real sqlite error

        rec = Recorder(store)
        info = {}
        with tempfile.TemporaryDirectory() as td, warnings.catch_warnings():
            warnings.simplefilter("error")
            rows = run_experiment(TASK_IDS, 1, 0, "stub", 0.0, f"{td}/r.jsonl", f"{td}/t.jsonl",
                                  event_sink=rec, run_info=info)
        self.assertEqual(stable(rows), stable(self.baseline[0]))
        self.assertEqual(rec.failures, 5)                # 2 starts, 2 finishes, 1 experiment finish
        self.assertEqual({f["error_type"] for f in rec.failure_log}, {"OperationalError"})
        self.assertEqual(len(rec._unnoted), 5)           # NOT recovered: nothing could be noted durably
        self.assertEqual(len(info["sink_failures"]), 5)  # but the caller is told
        exp = RunStore(self.db).list_experiments()[0]    # what the dashboard would show
        integ = recording_integrity(exp)
        self.assertEqual((exp["status"], exp["trials_finished"]), ("running", 1))
        self.assertNotEqual(integ["status"], "complete")
        self.assertEqual(integ["status"], "in_progress")
        late = public_experiment(exp, exp["updated_at"] + 10_000)
        self.assertTrue(late["stale"])                   # and it ages into a visible "no update" state
        self.assertNotEqual(late["recording"]["status"], "complete")

    def test_sanitizer_rejecting_one_event(self):
        store = RunStore(self.db)
        real = sanitize.safe_trial
        calls = {"n": 0}

        def picky(row, trajectory, **kw):
            calls["n"] += 1
            if calls["n"] == 2:
                raise UnsafePayload("trial: forbidden field 'prompt'")
            return real(row, trajectory, **kw)

        with mock.patch("minilab.live.recorder.safe_trial", side_effect=picky), warnings.catch_warnings():
            warnings.simplefilter("error")
            out = run(LiveRecorder(store))
        self.assertEqual(stable(out[0]), stable(self.baseline[0]))
        exp = store.list_experiments()[0]
        integ = recording_integrity(exp)
        self.assertEqual(integ["status"], "incomplete")
        self.assertEqual(integ["failures_by_hook"], {"trial_finished": 1})
        self.assertEqual(integ["failure_types"], {"UnsafePayload": 1})
        self.assertEqual((exp["trials_started"], exp["trials_finished"]), (3, 2))
        self.assertNotIn("prompt", json.dumps(store.events_after(0)))       # the rejected content is not stored

    def test_interrupt_inside_the_run_is_recorded_as_a_failed_run_never_completed(self):
        store = RunStore(self.db)
        with mock.patch("minilab.runner.run_agent", side_effect=KeyboardInterrupt()):
            with self.assertRaises(KeyboardInterrupt), tempfile.TemporaryDirectory() as td:
                run_experiment(TASK_IDS, 1, 0, "stub", 0.0, f"{td}/r.jsonl", f"{td}/t.jsonl",
                               event_sink=LiveRecorder(store))
        exp = store.list_experiments()[0]
        self.assertEqual((exp["status"], exp["error"]), ("failed", {"error_type": "KeyboardInterrupt"}))
        self.assertEqual(exp["trials_finished"], 0)
        # one trial was started and never finished: the record itself says so
        self.assertEqual(recording_integrity(exp)["status"], "incomplete")

    def test_process_killed_mid_run_is_never_shown_as_complete(self):
        code = (
            "import sys, time, tempfile\n"
            "from minilab.live.recorder import LiveRecorder\n"
            "from minilab.live.store import RunStore\n"
            "from minilab.runner import run_experiment\n"
            "class Slow(LiveRecorder):\n"
            "    def trial_started(self, info):\n"
            "        super().trial_started(info)\n"
            "        print('started', flush=True)\n"
            "        time.sleep(60)\n"
            "td = tempfile.mkdtemp()\n"
            "run_experiment(['waiver-c101', 'calc-total-c101'], 1, 0, 'stub', 0.0, td + '/r.jsonl', td + '/t.jsonl',\n"
            "               event_sink=Slow(RunStore(sys.argv[1])))\n")
        proc = subprocess.Popen([sys.executable, "-c", code, str(self.db)], env=CLI_ENV,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.assertEqual(proc.stdout.readline().strip(), "started")
        finally:
            proc.kill()                                  # no handler runs: nothing gets closed
            proc.wait(15)
            proc.stdout.close()
            proc.stderr.close()
        exp = RunStore(self.db).list_experiments()[0]
        integ = recording_integrity(exp)
        self.assertEqual((exp["status"], exp["trials_started"], exp["trials_finished"]), ("running", 1, 0))
        self.assertEqual(integ["status"], "in_progress")             # unknown from the record alone...
        late = public_experiment(exp, exp["updated_at"] + 10_000)
        self.assertTrue(late["stale"])                               # ...and flagged once it stops updating
        self.assertIn("may have been interrupted", late["stale_reason"])
        self.assertNotEqual(late["recording"]["status"], "complete")
        self.assertEqual(late["status"], "running")                  # never auto-marked failed or completed


class TestApiAndMigration(Base):
    def test_api_exposes_integrity_and_unrecorded_trials(self):
        self.record({"trial_finished": 1})
        server = LiveServer(("127.0.0.1", 0), RunStore(self.db))
        th = threading.Thread(target=server.serve_forever, daemon=True)
        th.start()
        try:
            base = f"http://127.0.0.1:{server.server_address[1]}"
            listing = json.loads(urllib.request.urlopen(base + "/api/experiments").read())["experiments"][0]
            self.assertEqual(listing["recording"]["status"], "incomplete")
            self.assertEqual(listing["recording"]["authoritative_evidence"], "JSONL result and trajectory files")
            self.assertEqual(listing["status"], "completed")
            d = json.loads(urllib.request.urlopen(base + f"/api/experiments/{listing['experiment_id']}").read())
            states = sorted(t["state"] for t in d["trials"])
            self.assertEqual(states, ["finished", "finished", "unrecorded"])
            self.assertEqual(d["analytics"]["counts"]["trials_unrecorded"], 1)
            self.assertEqual(d["analytics"]["counts"]["trials_running"], 0)
            self.assertEqual(d["analytics"]["counts"]["valid"], 2)      # the unrecorded trial is not scored
        finally:
            server.shutdown()
            server.server_close()
            th.join(5)

    def test_schema_one_database_is_upgraded_in_place(self):
        conn = sqlite3.connect(self.db)
        conn.executescript("""
            CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO meta VALUES ('schema_version', '1');
            CREATE TABLE experiments (experiment_id TEXT PRIMARY KEY, status TEXT NOT NULL, kind TEXT NOT NULL,
              created_at REAL NOT NULL, started_at REAL, finished_at REAL, updated_at REAL NOT NULL,
              total_trials INTEGER, current_task TEXT, config TEXT NOT NULL, error TEXT);
            INSERT INTO experiments VALUES ('old', 'completed', 'run', 1, 1, 2, 2, 0, NULL, '{}', NULL);
        """)
        conn.commit()
        conn.close()
        store = RunStore(self.db)
        old = store.get_experiment("old")
        self.assertIsNone(old["recording"])
        self.assertEqual(recording_integrity(old)["status"], "unavailable")
        RunStore(self.db)                                   # opening twice must not fail
        run(LiveRecorder(store))
        self.assertEqual(len(store.list_experiments()), 2)


if __name__ == "__main__":
    unittest.main()
