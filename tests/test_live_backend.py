"""Live path: runner -> event sink -> sanitized store -> HTTP/SSE API.

All runs here are SYNTHETIC (offline 'scripted'/'stub' models, temporary
files). No provider is called; nothing is written to results/.
"""

import json
import socket
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import warnings
from pathlib import Path

from minilab.live import analytics
from minilab.live.recorder import EventSink, LiveRecorder
from minilab.live.sanitize import (
    UnsafePayload, assert_safe, label, reason_categories, safe_error, safe_experiment, safe_trial,
)
from minilab.live.server import BODY_DEADLINE_S, MAX_BODY, LiveServer, public_experiment
from minilab.live.store import RunStore
from minilab.live.synthetic import start_synthetic_run
from minilab.runner import run_experiment
from minilab.tasks import TASKS, get_task

TASK_IDS = ["waiver-c101", "retrieval-bal-a103", "calc-total-c101"]
# Strings that exist only in raw evidence. None may appear in anything served or stored.
RAW_MARKERS = (
    "Determine whether customer",            # task prompt text
    "banking assistant",                     # system prompt text
    '"arguments"', '"observation"', '"raw_history"', '"final_answer"', '"prompt"', '"steps"',
    "qualifies for the fee waiver",          # oracle final answer text
    "The customer qualifies",                # stub final answer text
    "customer_id", "account_id",             # tool-argument / observation field names
    "A101", "A102", "A103",                  # account identifiers (never part of a task id)
    "missing evidence:", "ungrounded numbers (",   # evaluator reason strings quoting values
)


class Capture(EventSink):
    def __init__(self):
        self.calls = []

    def experiment_started(self, info):
        self.calls.append(("experiment_started", info))

    def trial_started(self, info):
        self.calls.append(("trial_started", info))

    def trial_finished(self, row, trajectory):
        self.calls.append(("trial_finished", row, trajectory))

    def experiment_finished(self, experiment_id, n):
        self.calls.append(("experiment_finished", experiment_id, n))

    def experiment_failed(self, experiment_id, exc):
        self.calls.append(("experiment_failed", experiment_id, exc))


def run(model="scripted", sink=None, task_ids=TASK_IDS, **kw):
    with tempfile.TemporaryDirectory() as td:
        return run_experiment(task_ids, 1, 0, model, 0.0, f"{td}/r.jsonl", f"{td}/t.jsonl",
                              event_sink=sink, **kw)


def assert_no_raw(testcase, text, where):
    for marker in RAW_MARKERS:
        testcase.assertNotIn(marker, text, f"raw marker {marker!r} leaked into {where}")


class TestRunnerEventSink(unittest.TestCase):
    def test_event_order_and_counts(self):
        cap = Capture()
        rows = run(sink=cap)
        kinds = [c[0] for c in cap.calls]
        self.assertEqual(kinds, ["experiment_started"] + ["trial_started", "trial_finished"] * 3
                         + ["experiment_finished"])
        self.assertEqual(cap.calls[0][1]["total_trials"], 3)
        self.assertEqual(cap.calls[-1][2], len(rows))

    def test_results_are_identical_with_and_without_a_sink(self):
        strip = lambda rows: [{k: v for k, v in r.items()           # noqa: E731
                               if k not in ("run_id", "experiment_id", "timestamp")} for r in rows]
        for model in ("scripted", "stub"):
            self.assertEqual(strip(run(model)), strip(run(model, Capture())))

    def test_a_failing_sink_never_breaks_or_changes_a_run(self):
        class Broken(EventSink):
            def trial_finished(self, row, trajectory):
                raise RuntimeError("sink bug")

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            rows = run(sink=Broken())
        self.assertEqual(len(rows), 3)
        self.assertTrue(any("event sink trial_finished failed" in str(x.message) for x in w))

    def test_run_failure_is_reported_and_reraised(self):
        cap = Capture()
        with self.assertRaises(KeyError):
            run(sink=cap, task_ids=["waiver-c101", "no-such-task"])
        self.assertEqual(cap.calls[-1][0], "experiment_failed")


class TestSanitization(unittest.TestCase):
    def trial_pairs(self, model):
        cap = Capture()
        run(model, cap, task_ids=[t["task_id"] for t in TASKS])
        return [(c[1], c[2]) for c in cap.calls if c[0] == "trial_finished"]

    def test_safe_trial_contains_no_raw_evidence(self):
        for model in ("scripted", "stub"):
            for row, traj in self.trial_pairs(model):
                raw = json.dumps(traj.to_dict())
                self.assertIn(get_task(row["task_id"])["prompt"], raw)      # the raw side does hold it
                safe = safe_trial(row, traj, seq=1)
                assert_no_raw(self, json.dumps(safe), f"safe_trial({row['task_id']}, {model})")
                self.assertEqual(safe["task_id"], row["task_id"])
                self.assertEqual(safe["passed"], row["passed"])

    def test_reasons_are_reduced_to_categories(self):
        self.assertEqual(reason_categories(["missing evidence: ['a101', '1800']", "verdict mismatch",
                                            "ungrounded numbers (absent from data observations): [100]",
                                            "something new: 42"]),
                         ["missing_evidence", "verdict_mismatch", "ungrounded_numbers", "other"])

    def test_tool_activity_has_names_and_outcomes_only(self):
        row, traj = self.trial_pairs("scripted")[0]
        act = safe_trial(row, traj)["tool_activity"]
        self.assertTrue(act)
        for a in act:
            self.assertEqual(set(a), {"iteration", "tool", "outcome"})

    def test_provider_error_text_is_reduced_to_a_flag(self):
        row, traj = self.trial_pairs("stub")[0]
        row = dict(row, trial_status="provider_error", provider_error="401 key sk-abcdefghijkl at C:\\Users\\x")
        safe = safe_trial(row, traj)
        self.assertTrue(safe["provider_error_present"])
        self.assertIsNone(safe["passed"])          # no pass/fail for an invalid trial
        self.assertNotIn("sk-abcdefghijkl", json.dumps(safe))

    def test_assert_safe_rejects_raw_shapes(self):
        for bad in ({"prompt": "x"}, {"a": {"observation": 1}}, {"a": [{"arguments": {}}]},
                    {"note": "x" * 500}, {"m": "C:\\Users\\bala\\keys.txt"}, {"m": "/home/u/secret"},
                    {"k": "sk-abcdefghijklmnop"}, {"k": "Bearer abc.def"}, {"x": object()},
                    {"reasons": []}, {"final_answer": "y"}, {"Token": "1"}):
            with self.assertRaises(UnsafePayload, msg=bad):
                assert_safe(bad)
        self.assertTrue(assert_safe({"a": 1, "b": [None, True, 0.5, "ok"]}))

    def test_labels_and_errors(self):
        self.assertEqual(label("local:qwen3-4b"), "local:qwen3-4b")
        self.assertEqual(label("C:\\models\\x.gguf"), "[redacted]")
        self.assertEqual(label("key sk-abcdefghijkl"), "[redacted]")
        self.assertEqual(safe_error(FileNotFoundError("/home/u/secret/bank.json")),
                         {"error_type": "FileNotFoundError"})
        exp = safe_experiment({"experiment_id": "e", "model": "/home/u/m.gguf", "task_ids": ["t"],
                               "prompt": "leak", "data_path": "/x"})
        self.assertEqual(exp["model"], "[redacted]")
        self.assertNotIn("prompt", exp)
        self.assertNotIn("data_path", exp)


class TestStoreAndRecorder(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.db = Path(self.td.name) / "runs.sqlite"
        self.store = RunStore(self.db)

    def tearDown(self):
        self.td.cleanup()

    def test_empty_store(self):
        self.assertEqual(self.store.list_experiments(), [])
        self.assertEqual(self.store.events_after(0), [])
        self.assertEqual(self.store.last_event_id(), 0)

    def test_full_lifecycle_is_recorded(self):
        rows = run("stub", LiveRecorder(self.store))
        exps = self.store.list_experiments()
        self.assertEqual(len(exps), 1)
        e = exps[0]
        self.assertEqual((e["status"], e["trials_finished"], e["total_trials"]), ("completed", 3, 3))
        self.assertEqual(e["experiment_id"], rows[0]["experiment_id"])
        types = [ev["type"] for ev in self.store.events_after(0)]
        self.assertEqual(types, ["experiment_running"] + ["trial_started", "trial_finished"] * 3
                         + ["experiment_completed"])
        trials = self.store.list_trials(e["experiment_id"])
        self.assertEqual([t["seq"] for t in trials], [1, 2, 3])
        self.assertTrue(all(t["state"] == "finished" for t in trials))

    def test_events_after_is_a_resumable_cursor(self):
        run("stub", LiveRecorder(self.store))
        allev = self.store.events_after(0)
        ids = [e["id"] for e in allev]
        self.assertEqual(ids, sorted(ids))
        self.assertEqual([e["id"] for e in self.store.events_after(ids[3])], ids[4:])

    def test_database_file_holds_no_raw_evidence(self):
        for model in ("scripted", "stub"):
            run(model, LiveRecorder(self.store), task_ids=[t["task_id"] for t in TASKS])
        blob = b"".join(p.read_bytes() for p in Path(self.td.name).iterdir()).decode("latin-1")
        assert_no_raw(self, blob, "the SQLite files")

    def test_store_refuses_unsafe_writes(self):
        with self.assertRaises(UnsafePayload):
            self.store.create_experiment({"experiment_id": "x", "prompt": "leak"})
        self.store.create_experiment({"experiment_id": "x", "kind": "run", "total_trials": 1})
        self.store.trial_started("x", "r1", "t", 1)
        with self.assertRaises(UnsafePayload):
            self.store.trial_finished("x", {"run_id": "r1", "task_id": "t", "observation": {"a": 1}})
        self.assertEqual(self.store.list_trials("x")[0]["state"], "running")   # nothing was written

    def test_failed_run_is_recorded_with_error_type_only(self):
        with self.assertRaises(KeyError):
            run("stub", LiveRecorder(self.store), task_ids=["waiver-c101", "no-such-task"])
        e = self.store.list_experiments()[0]
        self.assertEqual(e["status"], "failed")
        self.assertEqual(e["error"], {"error_type": "KeyError"})
        self.assertEqual(e["trials_finished"], 1)


class TestAnalyticsRules(unittest.TestCase):
    @staticmethod
    def t(task, passed, status="valid", failure="none", state="finished"):
        return {"task_id": task, "state": state, "trial_status": status,
                "passed": passed if status == "valid" else None, "failure": failure,
                "reason_categories": [], "difficulty": "easy", "tool_calls": 1, "model_requests": 2}

    def test_no_valid_trials_means_no_rate(self):
        a = analytics.experiment_analytics([self.t("waiver-c101", None, status="provider_error")])
        self.assertIsNone(a["pass_rate"]["rate"])
        self.assertIsNone(a["pass_rate"]["interval"])
        self.assertEqual(a["counts"]["excluded"], 1)
        self.assertEqual(a["counts"]["by_trial_status"], {"provider_error": 1})

    def test_empty_and_running_only(self):
        a = analytics.experiment_analytics([])
        self.assertEqual(a["counts"]["trials_started"], 0)
        self.assertIsNone(a["pass_rate"]["rate"])
        a = analytics.experiment_analytics([{"task_id": "waiver-c101", "state": "running"}])
        self.assertEqual((a["counts"]["trials_running"], a["counts"]["valid"]), (1, 0))

    def test_small_sample_has_rate_but_no_interval(self):
        a = analytics.experiment_analytics([self.t("waiver-c101", True), self.t("waiver-c102", False)])
        self.assertEqual(a["pass_rate"]["rate"], 0.5)
        self.assertIsNone(a["pass_rate"]["interval"])
        self.assertTrue(a["pass_rate"]["insufficient_sample"])

    def test_interval_with_cluster_caveat_when_tasks_repeat(self):
        trials = [self.t("waiver-c101", i % 2 == 0) for i in range(6)]
        pr = analytics.experiment_analytics(trials)["pass_rate"]
        self.assertEqual(pr["rate"], 0.5)                          # the rate is still shown
        self.assertIsNone(pr["interval"])                          # the interval is suppressed
        self.assertEqual(pr["interval_suppressed"], "clustered_by_task")
        self.assertTrue(any("clustered" in n for n in pr["notes"]))
        distinct = [self.t(t["task_id"], True) for t in TASKS[:6]]
        pr = analytics.experiment_analytics(distinct)["pass_rate"]
        self.assertIsNotNone(pr["interval"])
        self.assertIsNone(pr["interval_suppressed"])
        self.assertFalse(any("clustered" in n for n in pr["notes"]))

    def test_invalid_trials_never_enter_the_denominator(self):
        trials = [self.t("waiver-c101", True)] * 5 + [self.t("waiver-c101", None, status="provider_error")] * 5
        a = analytics.experiment_analytics(trials)
        self.assertEqual((a["pass_rate"]["valid"], a["pass_rate"]["rate"]), (5, 1.0))

    def test_depth_and_family_are_not_invented(self):
        a = analytics.experiment_analytics([self.t("waiver-c101", True)])
        self.assertFalse(a["by_depth"]["available"])
        self.assertFalse(a["by_family"]["available"])
        info = analytics.task_info("waiver-c101")
        self.assertIsNone(info["declared_depth"])
        self.assertEqual(info["candidate_depths"], {"working_tree_2026_11_13": 2, "protocol_3_4_draft": 1})
        self.assertIsNone(analytics.task_info("not-a-task")["candidate_depths"])

    def test_comparison_is_descriptive_and_flags_shared_tasks(self):
        a = [self.t(t["task_id"], True) for t in TASKS[:6]]
        b = [self.t(t["task_id"], False, failure="evidence_gap") for t in TASKS[:6]]
        c = analytics.compare_experiments(a, b)
        self.assertEqual((c["kind"], c["difference"]), ("descriptive", 1.0))
        self.assertIsNone(c["interval"])                           # paired by task: no independent-samples interval
        self.assertEqual(c["interval_suppressed"], "shared_tasks")
        self.assertTrue(any("share tasks" in n for n in c["notes"]))
        self.assertNotIn("significant", json.dumps(c).replace("not a significance test", ""))
        self.assertIsNone(analytics.compare_experiments(a, [])["difference"])
        # disjoint tasks, one trial per task, enough trials: the only case with an interval
        d = analytics.compare_experiments([self.t(t["task_id"], True) for t in TASKS[:5]],
                                          [self.t(t["task_id"], False) for t in TASKS[5:]])
        self.assertEqual(d["shared_tasks"], 0)
        self.assertEqual(d["interval"]["method"], "newcombe-wilson-method-10")
        self.assertTrue(any("assumes independent" in n for n in d["notes"]))
        # disjoint but repeated tasks: suppressed as clustered
        e = analytics.compare_experiments([self.t("waiver-c101", True)] * 6,
                                          [self.t("waiver-c102", False)] * 6)
        self.assertEqual((e["interval"], e["interval_suppressed"]), (None, "clustered_by_task"))
        # disjoint, too few: suppressed as a display convention, labelled as such
        f = analytics.compare_experiments([self.t("waiver-c101", True)], [self.t("waiver-c102", False)])
        self.assertEqual(f["interval_suppressed"], "insufficient_sample")
        self.assertTrue(any("display convention" in n for n in f["notes"]))
        self.assertEqual(f["difference"], 1.0)

    def test_figures_from_a_record_not_known_complete_carry_a_caveat(self):
        # D3: a rate or comparison from an incomplete/unavailable recording must say so.
        trials = [self.t(t["task_id"], True) for t in TASKS[:6]]
        ok = analytics.experiment_analytics(trials, {"status": "complete"})
        self.assertIsNone(ok["recording_caveat"])
        self.assertFalse(any("recording" in n for n in ok["pass_rate"]["notes"]))
        for status in ("incomplete", "unavailable", "in_progress", None):
            a = analytics.experiment_analytics(trials, {"status": status} if status else None)
            self.assertIsNotNone(a["recording_caveat"], status)
            self.assertIn(a["recording_caveat"], a["pass_rate"]["notes"])
            self.assertEqual(a["pass_rate"]["rate"], 1.0)          # the number itself is unchanged
        c = analytics.compare_experiments(trials, trials, {"status": "complete"}, {"status": "incomplete"})
        self.assertEqual(c["recording"], {"a": "complete", "b": "incomplete"})
        self.assertEqual(list(c["recording_caveat"]), ["b"])
        self.assertTrue(any(n.startswith("run B: recording incomplete") for n in c["notes"]))
        both = analytics.compare_experiments(trials, trials, {"status": "complete"}, {"status": "complete"})
        self.assertIsNone(both["recording_caveat"])
        self.assertEqual(both["difference"], c["difference"])


class ServerCase(unittest.TestCase):
    enable_synthetic = True

    def setUp(self):
        self._threads_before = set(threading.enumerate())
        self.td = tempfile.TemporaryDirectory()
        self.store = RunStore(Path(self.td.name) / "runs.sqlite")
        self.server = LiveServer(("127.0.0.1", 0), self.store, enable_synthetic=self.enable_synthetic,
                                 heartbeat_s=0.2, poll_s=0.02)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)
        # Request-handler and synthetic-run threads are daemons that server_close() does not
        # wait for; one still inside a store call keeps runs.sqlite open, and Windows cannot
        # delete an open file (WinError 32). Wait for them, bounded, before deleting.
        deadline = time.monotonic() + 10
        for t in set(threading.enumerate()) - self._threads_before:
            t.join(max(0.0, deadline - time.monotonic()))
        self.td.cleanup()

    def get(self, path, headers=None, raw=False):
        req = urllib.request.Request(self.base + path, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                body = r.read().decode()
                return r.status, (body if raw else json.loads(body))
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode() or "{}")

    def post(self, path, body, headers=None):
        h = {"Content-Type": "application/json", "X-ARL-Local": "1"}
        h.update(headers or {})
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode(), headers=h, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode() or "{}")

    def raw_post(self, path="/api/dev/synthetic-run", body=b"{}", host="127.0.0.1", local="1",
                 length=None, extra=b"", hold_open=False):
        """Send one hand-built POST over a raw socket and return everything the server sends back
        before it closes the connection. Any reset (e.g. WinError 10053/10054) propagates."""
        head = (f"POST {path} HTTP/1.1\r\nHost: {host}\r\nX-ARL-Local: {local}\r\n"
                f"Content-Type: application/json\r\n").encode()
        if length is not False:
            head += f"Content-Length: {len(body) if length is None else length}\r\n".encode()
        with socket.create_connection(self.server.server_address[:2], timeout=10) as s:
            s.sendall(head + extra + b"\r\n" + body)
            if not hold_open:
                s.shutdown(socket.SHUT_WR)
            buf = b""
            while chunk := s.recv(65536):
                buf += chunk
        return buf

    @staticmethod
    def status_of(response):
        return int(response.split(b" ", 2)[1])

    def wait_status(self, eid, wanted=("completed", "failed"), timeout=20):
        end = time.time() + timeout
        while time.time() < end:
            _, d = self.get(f"/api/experiments/{eid}")
            if d["experiment"]["status"] in wanted:
                return d
            time.sleep(0.05)
        self.fail(f"experiment {eid} did not reach {wanted}")


class TestApi(ServerCase):
    def test_idle_state_is_truthful(self):
        s, health = self.get("/api/health")
        self.assertEqual((s, health["status"], health["last_event_id"]), (200, "ok", 0))
        self.assertEqual(health["authentication"], "none (loopback only)")
        s, d = self.get("/api/experiments")
        self.assertEqual((s, d["experiments"]), (200, []))
        self.assertEqual(self.get("/api/events")[1], {"events": []})

    def test_end_to_end_synthetic_run(self):
        s, d = self.post("/api/dev/synthetic-run", {"model": "scripted", "task_ids": TASK_IDS, "delay_s": 0.05})
        self.assertEqual(s, 202)
        eid = d["experiment_id"]
        self.assertTrue(eid.startswith("synthetic-"))
        detail = self.wait_status(eid)
        exp = detail["experiment"]
        self.assertEqual((exp["status"], exp["kind"], exp["source"]), ("completed", "synthetic", "live_backend"))
        self.assertEqual(exp["progress"], {"finished": 3, "started": 3, "total": 3, "remaining": 0, "fraction": 1.0})
        self.assertIsNone(exp["current_task"])
        self.assertGreater(exp["elapsed_s"], 0.1)
        self.assertEqual([t["task_id"] for t in detail["trials"]], TASK_IDS)
        self.assertEqual(detail["analytics"]["counts"]["valid"], 3)
        self.assertTrue(detail["analytics"]["pass_rate"]["insufficient_sample"])
        types = [e["type"] for e in self.get(f"/api/experiments/{eid}/events")[1]["events"]]
        self.assertEqual(types[0], "experiment_queued")
        self.assertEqual(types[1], "experiment_running")
        self.assertEqual(types[-1], "experiment_completed")
        self.assertEqual(types.count("trial_finished"), 3)

    def test_progress_is_visible_while_running(self):
        _, d = self.post("/api/dev/synthetic-run", {"model": "stub", "task_ids": TASK_IDS, "delay_s": 0.3})
        eid = d["experiment_id"]
        seen = set()
        end = time.time() + 15
        while time.time() < end:
            exp = self.get(f"/api/experiments/{eid}")[1]["experiment"]
            seen.add((exp["status"], exp["progress"]["finished"]))
            if exp["status"] == "completed":
                break
            time.sleep(0.05)
        self.assertIn(("completed", 3), seen)
        self.assertTrue(any(s == "running" and n < 3 for s, n in seen), seen)

    def test_failed_run(self):
        _, d = self.post("/api/dev/synthetic-run", {"model": "stub", "task_ids": TASK_IDS, "fail_after": 2})
        detail = self.wait_status(d["experiment_id"])
        exp = detail["experiment"]
        self.assertEqual(exp["status"], "failed")
        self.assertEqual(exp["error"], {"error_type": "_InjectedSyntheticFailure"})
        self.assertEqual(exp["progress"]["finished"], 2)
        self.assertEqual(exp["progress"]["remaining"], 1)

    def test_incomplete_run_is_flagged_stale_not_failed(self):
        self.store.create_experiment({"experiment_id": "orphan", "kind": "run", "total_trials": 4}, "running")
        self.store.trial_started("orphan", "r1", "waiver-c101", 1)
        exp = self.store.get_experiment("orphan")
        fresh = public_experiment(exp, exp["updated_at"] + 1)
        late = public_experiment(exp, exp["updated_at"] + 10_000)
        self.assertFalse(fresh["stale"])
        self.assertTrue(late["stale"])
        self.assertEqual(late["status"], "running")
        self.assertIn("not marked failed", late["stale_reason"])
        d = self.get("/api/experiments/orphan")[1]
        self.assertEqual(d["trials"][0]["state"], "running")
        self.assertEqual(d["analytics"]["counts"]["trials_running"], 1)

    def test_bad_requests(self):
        self.assertEqual(self.get("/api/experiments/nope")[0], 404)
        self.assertEqual(self.get("/api/nothing")[0], 404)
        self.assertEqual(self.get("/api/trajectories")[0], 404)
        self.assertEqual(self.get("/api/events?after=abc")[0], 400)
        self.assertEqual(self.get("/api/compare?a=x&b=y")[0], 400)
        self.assertEqual(self.post("/api/dev/synthetic-run", {"model": "gemini"})[0], 400)
        self.assertEqual(self.post("/api/dev/synthetic-run", {"task_ids": ["nope"]})[0], 400)
        self.assertEqual(self.post("/api/dev/synthetic-run", {"repeats": 999})[0], 400)
        self.assertEqual(self.post("/api/experiments", {})[0], 404)

    def test_request_guards(self):
        self.assertEqual(self.get("/api/health", headers={"Host": "evil.example"})[0], 403)
        self.assertEqual(self.post("/api/dev/synthetic-run", {}, headers={"X-ARL-Local": "0"})[0], 403)
        self.assertEqual(self.post("/api/dev/synthetic-run", {}, headers={"Host": "evil.example"})[0], 403)

    def test_non_loopback_bind_is_refused(self):
        for host in ("0.0.0.0", "192.168.1.10", ""):
            with self.assertRaises(ValueError):
                LiveServer((host, 0), self.store)

    def test_no_endpoint_exposes_raw_evidence(self):
        ids = []
        for model in ("scripted", "stub"):
            _, d = self.post("/api/dev/synthetic-run", {"model": model})
            ids.append(d["experiment_id"])
        for eid in ids:
            self.wait_status(eid)
        paths = ["/api/health", "/api/experiments", "/api/events", f"/api/compare?a={ids[0]}&b={ids[1]}"]
        for eid in ids:
            paths += [f"/api/experiments/{eid}", f"/api/experiments/{eid}/events"]
        for p in paths:
            s, body = self.get(p, raw=True)
            self.assertEqual(s, 200, p)
            assert_no_raw(self, body, p)
        cmp_ = self.get(f"/api/compare?a={ids[0]}&b={ids[1]}")[1]["comparison"]
        self.assertEqual(cmp_["shared_tasks"], 10)
        self.assertTrue(any("share tasks" in n for n in cmp_["notes"]))
        # the server passes each run's recording integrity into the figures
        self.assertEqual(cmp_["recording"], {"a": "complete", "b": "complete"})
        self.assertIsNone(cmp_["recording_caveat"])
        detail = self.get(f"/api/experiments/{ids[0]}")[1]
        self.assertEqual(detail["analytics"]["recording_status"], detail["experiment"]["recording"]["status"])

    def test_synthetic_run_writes_nothing_to_results(self):
        results = Path(__file__).resolve().parents[1] / "results"
        before = sorted((p.name, p.stat().st_size, p.stat().st_mtime_ns) for p in results.iterdir())
        _, d = self.post("/api/dev/synthetic-run", {"model": "scripted", "task_ids": TASK_IDS})
        self.wait_status(d["experiment_id"])
        after = sorted((p.name, p.stat().st_size, p.stat().st_mtime_ns) for p in results.iterdir())
        self.assertEqual(before, after)


class TestRejectedRequestBodies(ServerCase):
    """Every early POST rejection reads (bounded) and discards the body, replies, and closes.

    Without the drain, Windows resets a connection closed with unread bytes and the
    client sees WinError 10053 instead of the status code (the old intermittent failure).
    """
    REPEATS = 25  # the old failure showed up in about a third of runs; repeat to catch it

    def assert_rejected(self, response, status):
        self.assertEqual(self.status_of(response), status, response[:200])
        self.assertIn(b"\r\nConnection: close\r\n", response)
        self.assertEqual(response.count(b"HTTP/1.1 "), 1, "exactly one response, then close")

    def assert_rejected_or_reset(self, status, **kw):
        """For requests that leave bytes the server must not read (unframed or pipelined data):
        closing with them unread may reset the connection on Windows. Either outcome is safe;
        what must never happen is a second response. Callers also check no run was created."""
        try:
            r = self.raw_post(**kw)
        except (ConnectionResetError, ConnectionAbortedError):
            return
        self.assert_rejected(r, status)

    def test_each_rejection_path_with_a_body_returns_its_status(self):
        body = json.dumps({"model": "scripted", "pad": "x" * 2000}).encode()
        cases = [
            (dict(host="evil.example"), 403),
            (dict(path="/api/experiments"), 404),
            (dict(local="0"), 403),
            (dict(body=b"x" * (MAX_BODY + 1)), 413),
        ]
        for kw, status in cases:
            for _ in range(self.REPEATS):
                with self.subTest(kw=list(kw), status=status):
                    self.assert_rejected(self.raw_post(**{"body": body, **kw}), status)
        self.assertEqual(self.store.list_experiments(), [])

    def test_oversized_declared_length_is_refused_without_reading_it(self):
        t0 = time.monotonic()
        r = self.raw_post(body=b"", length=10**12)  # nothing sent; nothing must be awaited
        self.assert_rejected(r, 413)
        self.assertLess(time.monotonic() - t0, 1.0)

    def test_incomplete_body_on_a_rejection_path_is_bounded_by_the_deadline(self):
        t0 = time.monotonic()
        r = self.raw_post(host="evil.example", body=b'{"a"', length=5000, hold_open=True)
        self.assert_rejected(r, 403)
        self.assertLess(time.monotonic() - t0, BODY_DEADLINE_S + 3)

    def test_incomplete_accepted_body_times_out_instead_of_waiting_forever(self):
        t0 = time.monotonic()
        r = self.raw_post(body=b'{"mo', length=200, hold_open=True)
        self.assert_rejected(r, 408)
        self.assertLess(time.monotonic() - t0, BODY_DEADLINE_S + 3)
        self.assertEqual(self.store.list_experiments(), [])

    def test_malformed_framing_and_bodies_are_400(self):
        cases = [  # no body bytes are sent, so nothing is left unread and the reply is certain
            dict(length="abc"),
            dict(length="-1"),       # used to mean "read until the client closes"
            dict(length="+2"),
            dict(length=False, extra=b"Transfer-Encoding: chunked\r\n"),
            dict(extra=b"Content-Length: 2\r\n"),  # duplicate Content-Length (0 and 2)
        ]
        for kw in cases:
            with self.subTest(kw=kw):
                t0 = time.monotonic()
                self.assert_rejected(self.raw_post(body=b"", **kw), 400)
                self.assertLess(time.monotonic() - t0, 1.0)
        for body in (b"{not json", b"[1, 2]", b"\xff\xfe", b'{"model": "gemini"}'):
            with self.subTest(body=body):
                self.assert_rejected(self.raw_post(body=body), 400)
        self.assertEqual(self.store.list_experiments(), [])

    def test_transfer_encoding_with_content_length_is_refused(self):
        chunked = b"2\r\n{}\r\n0\r\n\r\n"
        for extra in (b"Transfer-Encoding: chunked\r\n", b"Transfer-Encoding: identity\r\n",
                      b"transfer-encoding: chunked\r\n"):
            with self.subTest(extra=extra):
                # Content-Length: 0 with TE: nothing unread, so the 400 is certain
                self.assert_rejected(self.raw_post(body=b"", extra=extra), 400)
                # Content-Length: 13 with TE: the body is not read, so the close may reset
                self.assert_rejected_or_reset(400, body=chunked, extra=extra)
        self.assertEqual(self.store.list_experiments(), [])

    def test_no_second_request_is_served_after_any_rejected_post(self):
        """Bytes pipelined after a rejected POST (a complete, valid request) are never processed."""
        second = (b"POST /api/dev/synthetic-run HTTP/1.1\r\nHost: 127.0.0.1\r\n"
                  b"X-ARL-Local: 1\r\nContent-Length: 2\r\n\r\n{}")
        body = b'{"model": "scripted"}'
        cases = [  # (first request, its expected status)
            (dict(host="evil.example"), 403), (dict(path="/api/x"), 404), (dict(local="0"), 403),
            (dict(body=b"x" * (MAX_BODY + 1)), 413), (dict(length="abc"), 400),
            (dict(extra=b"Content-Length: 2\r\n"), 400),
            (dict(body=b'{"model": "gemini"}'), 400),  # rejected after the body was read
        ]
        for kw, status in cases:
            with self.subTest(kw=kw if "body" not in kw else list(kw), status=status):
                b = kw.get("body", body)
                n = kw.get("length", len(b))
                rest = {k: v for k, v in kw.items() if k not in ("body", "length")}
                self.assert_rejected_or_reset(status, body=b + second, length=n, **rest)
        self.assertEqual(self.store.list_experiments(), [])

    def test_a_request_hidden_in_a_rejected_body_is_never_served(self):
        smuggled = (b"POST /api/dev/synthetic-run HTTP/1.1\r\nHost: 127.0.0.1\r\n"
                    b"X-ARL-Local: 1\r\nContent-Length: 2\r\n\r\n{}")
        for kw, status in ((dict(host="evil.example"), 403), (dict(local="0"), 403)):
            with self.subTest(kw=list(kw)):  # body fully drained: the reply is certain
                self.assert_rejected(self.raw_post(body=smuggled, **kw), status)
        # chunked: the body is never read, so the close may reset; it must still not be served
        self.assert_rejected_or_reset(400, body=smuggled, length=False,
                                      extra=b"Transfer-Encoding: chunked\r\n")
        self.assertEqual(self.store.list_experiments(), [])

    def test_server_still_serves_after_rejections(self):
        self.raw_post(body=b"", length=10**12)
        self.raw_post(host="evil.example", body=b"{", length=50, hold_open=True)
        self.assertEqual(self.get("/api/health")[0], 200)
        s, d = self.post("/api/dev/synthetic-run", {"model": "scripted", "task_ids": TASK_IDS[:1]})
        self.assertEqual(s, 202)
        self.wait_status(d["experiment_id"])


class TestSyntheticDisabled(ServerCase):
    enable_synthetic = False

    def test_synthetic_endpoint_is_off_by_default(self):
        self.assertEqual(self.post("/api/dev/synthetic-run", {})[0], 403)
        self.assertFalse(self.get("/api/health")[1]["synthetic_runs_enabled"])

    def test_disabled_endpoint_rejects_a_body_cleanly(self):
        body = json.dumps({"model": "scripted", "pad": "x" * 2000}).encode()
        for _ in range(25):
            r = self.raw_post(body=body)
            self.assertEqual(self.status_of(r), 403)
            self.assertIn(b"\r\nConnection: close\r\n", r)


class TestEventStream(ServerCase):
    def read_stream(self, after=None, last_event_id=None, until=None, max_s=15):
        """Collect SSE frames until `until(event)` is true or time runs out."""
        headers = {"Accept": "text/event-stream"}
        if last_event_id is not None:
            headers["Last-Event-ID"] = str(last_event_id)
        path = "/api/stream" + (f"?after={after}" if after is not None else "")
        req = urllib.request.Request(self.base + path, headers=headers)
        events, comments = [], []
        end = time.time() + max_s
        with urllib.request.urlopen(req, timeout=max_s) as r:
            self.assertEqual(r.headers["Content-Type"], "text/event-stream; charset=utf-8")
            frame = {}
            while time.time() < end:
                line = r.readline().decode()
                if not line:
                    break
                line = line.rstrip("\n")
                if line.startswith(":"):
                    comments.append(line)
                    if until and until(None, comments):
                        break
                elif line == "":
                    if "data" in frame:
                        ev = json.loads(frame["data"])
                        self.assertEqual(str(ev["id"]), frame["id"])
                        self.assertEqual(ev["type"], frame["event"])
                        events.append(ev)
                        if until and until(ev, comments):
                            break
                    frame = {}
                else:
                    k, _, v = line.partition(": ")
                    frame[k] = v
        return events, comments

    def test_stream_delivers_a_run_as_it_happens(self):
        got = {}

        def reader():
            got["events"], _ = self.read_stream(until=lambda ev, c: ev and ev["type"] == "experiment_completed")

        t = threading.Thread(target=reader)
        t.start()
        time.sleep(0.2)                                   # the stream is open BEFORE the run exists
        _, d = self.post("/api/dev/synthetic-run", {"model": "scripted", "task_ids": TASK_IDS, "delay_s": 0.05})
        t.join(20)
        types = [e["type"] for e in got["events"]]
        self.assertEqual(types, ["experiment_queued", "experiment_running"]
                         + ["trial_started", "trial_finished"] * 3 + ["experiment_completed"])
        self.assertTrue(all(e["experiment_id"] == d["experiment_id"] for e in got["events"]))
        assert_no_raw(self, json.dumps(got["events"]), "the event stream")

    def test_reconnect_resumes_after_last_event_id(self):
        _, d = self.post("/api/dev/synthetic-run", {"model": "stub", "task_ids": TASK_IDS})
        self.wait_status(d["experiment_id"])
        all_ids = [e["id"] for e in self.get("/api/events")[1]["events"]]
        cut = all_ids[3]
        for kwargs in ({"last_event_id": cut}, {"after": cut}):
            events, _ = self.read_stream(until=lambda ev, c: ev and ev["id"] == all_ids[-1], **kwargs)
            self.assertEqual([e["id"] for e in events], all_ids[4:], kwargs)

    def test_idle_stream_sends_keepalives(self):
        events, comments = self.read_stream(until=lambda ev, c: c.count(": keepalive") >= 2, max_s=5)
        self.assertEqual(events, [])
        self.assertGreaterEqual(comments.count(": keepalive"), 2)


class TestStaticServing(unittest.TestCase):
    def test_static_dir_is_confined(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "dist"
            root.mkdir()
            (root / "index.html").write_text("<html>app</html>")
            (Path(td) / "secret.txt").write_text("nope")
            server = LiveServer(("127.0.0.1", 0), RunStore(Path(td) / "db.sqlite"), static_dir=root)
            th = threading.Thread(target=server.serve_forever, daemon=True)
            th.start()
            try:
                base = f"http://127.0.0.1:{server.server_address[1]}"
                self.assertIn("app", urllib.request.urlopen(base + "/").read().decode())
                self.assertIn("app", urllib.request.urlopen(base + "/live/some/route").read().decode())
                body = urllib.request.urlopen(base + "/..%2Fsecret.txt").read().decode()
                self.assertNotIn("nope", body)
                (root / "data").mkdir()
                (root / "data" / "exp-x.traj.json").write_text('[{"raw_history": "RAW"}]')
                (root / "data" / "exp-x.json").write_text('[{"task_id": "t"}]')
                with self.assertRaises(urllib.error.HTTPError) as ctx:
                    urllib.request.urlopen(base + "/data/exp-x.traj.json")
                self.assertEqual(ctx.exception.code, 403)
                self.assertIn("task_id", urllib.request.urlopen(base + "/data/exp-x.json").read().decode())
            finally:
                server.shutdown()
                server.server_close()
                th.join(5)


if __name__ == "__main__":
    unittest.main()
