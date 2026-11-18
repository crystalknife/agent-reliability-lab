"""BankOps CLI (normal import path). Fake OpenAI-compatible server only: no real provider, no credentials."""

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

try:  # `discover -s tests` puts tests/ on sys.path; `-m unittest tests.x` uses the namespace package
    from test_bankops_models import FakeServer, _final_reply, _tool_reply
except ImportError:  # pragma: no cover
    from tests.test_bankops_models import FakeServer, _final_reply, _tool_reply

from minilab.bankops import cli
from minilab.bankops import runner as r
from minilab.bankops.parser_eval import evaluate

SENTINEL = "sk-test-SENTINEL-must-never-be-written-0000"
FIN_FALSE = _tool_reply(("finish", {"status": "completed", "payments": [
    {"invoice_id": "INV-1001", "claimed_status": "released"}]}))           # nothing was paid: an agent failure
BAD_ARGS = {"choices": [{"message": {"tool_calls": [{"function": {"name": "release_payment",
                                                                    "arguments": "{oops"}}]}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 2}}


class CliCase(unittest.TestCase):
    def setUp(self):
        self.srv = FakeServer()
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name) / "runs"

    def tearDown(self):
        self.srv.close()
        self.tmp.cleanup()

    def cli(self, *extra, profile="local", steps=()):
        self.srv.steps[:] = list(steps)
        args = ["run", "--profile", profile, "--model-id", "scripted-model", "--base-url", self.srv.url,
                "--timeout", "5", "--tasks", "MER-PAY-001", "--out", str(self.out), *extra]
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = cli.main(args)
        return code, stdout.getvalue(), stderr.getvalue()

    def run_dirs(self):
        return sorted(self.out.iterdir()) if self.out.exists() else []

    def load(self, run_dir):
        episodes = [json.loads(line) for line in (run_dir / "episodes.jsonl").read_text(encoding="utf-8").splitlines()]
        return (json.loads((run_dir / "manifest.json").read_text(encoding="utf-8")), episodes,
                json.loads((run_dir / "summary.json").read_text(encoding="utf-8")),
                json.loads((run_dir / "review_queue.json").read_text(encoding="utf-8")))


class TestSafety(CliCase):
    def test_dry_run_sends_nothing_and_writes_nothing(self):
        code, out, _ = self.cli("--dry-run", "--seeds", "1,2", "--repeats", "3",
                                "--faults", "none,request_lost,response_lost,late_commit_6")
        self.assertEqual(code, 0)
        self.assertIn("plan: 24 episodes", out)
        self.assertEqual((self.srv.requests, self.run_dirs()), ([], []))

    def test_paid_profile_needs_explicit_approval(self):
        code, _, err = self.cli("--credential-ref", "env:BANKOPS_TEST_KEY", profile="api")
        self.assertEqual(code, 2)
        self.assertIn("--approve-paid-run", err)
        self.assertEqual((self.srv.requests, self.run_dirs()), ([], []))

    def test_missing_credential_is_refused_by_name_only(self):
        os.environ.pop("BANKOPS_TEST_KEY", None)
        code, _, err = self.cli("--credential-ref", "env:BANKOPS_TEST_KEY", "--approve-paid-run", profile="api")
        self.assertEqual(code, 2)
        self.assertIn("env:BANKOPS_TEST_KEY", err)
        self.assertEqual((self.srv.requests, self.run_dirs()), ([], []))

    def test_pasted_secret_is_rejected_without_echo(self):
        code, out, err = self.cli("--credential-ref", SENTINEL, "--approve-paid-run", profile="api")
        self.assertEqual(code, 2)
        self.assertNotIn(SENTINEL, out + err)

    def test_secret_is_used_but_never_printed_or_persisted(self):
        os.environ["BANKOPS_TEST_KEY"] = SENTINEL
        try:
            code, out, err = self.cli("--credential-ref", "env:BANKOPS_TEST_KEY", "--approve-paid-run",
                                      profile="api", steps=[("json", FIN_FALSE)])
        finally:
            del os.environ["BANKOPS_TEST_KEY"]
        self.assertEqual(code, 0)
        self.assertEqual(self.srv.requests[0]["headers"]["Authorization"], f"Bearer {SENTINEL}")
        self.assertNotIn(SENTINEL, out + err)
        for f in self.run_dirs()[0].iterdir():
            self.assertNotIn(SENTINEL, f.read_text(encoding="utf-8"), f.name)
        manifest = self.load(self.run_dirs()[0])[0]
        self.assertEqual(manifest["credential_ref"], "env:BANKOPS_TEST_KEY")

    def test_bad_selection_is_refused(self):
        for extra in (("--faults", "chaos"), ("--tasks", "NOPE")):
            with self.subTest(extra=extra), self.assertRaises(SystemExit):
                self.cli(*extra, "--dry-run")


class TestRunOutputs(CliCase):
    def test_run_directory_provenance_trace_and_replay(self):
        code, _, _ = self.cli("--seeds", "3", "--repeats", "2", "--faults", "none",
                              steps=[("json", _tool_reply(("list_invoices", {}))), ("json", FIN_FALSE),
                                     ("json", _final_reply("All done!"))])
        self.assertEqual(code, 0)
        (run_dir,) = self.run_dirs()
        manifest, episodes, summary, queue = self.load(run_dir)
        self.assertEqual(manifest["run_id"], run_dir.name)
        for key in ("runner_version", "grader_version", "contract_version", "benchmark_version", "code_sha256",
                    "tasks", "fault_scenarios", "seeds", "repeats", "model", "budget", "episodes_planned"):
            self.assertIn(key, manifest)
        self.assertEqual(manifest["model"]["model_id"], "scripted-model")
        self.assertEqual([e["repeat"] for e in episodes], [0, 1])
        self.assertEqual(len({e["episode_id"] for e in episodes}), 2)
        for e in episodes:
            self.assertEqual(e["run_id"], run_dir.name)
            self.assertIn("events", e["trace"])
            r.replay_episode(e)                                           # independently re-gradeable
        self.assertEqual(summary["episodes_run"], 2)
        self.assertEqual(summary["usage"]["prompt_tokens"], 10)          # two tool-call replies of 5 tokens
        self.assertEqual(summary["usage"]["turns_without_usage"], 1)      # the plain-text reply reported none
        self.assertEqual(summary["usage"]["source"], "provider_reported_only")
        no_usage = [t for e in episodes for t in e["trace"]["events"] if t["type"] == "model_turn" and t["usage"] is None]
        self.assertEqual(len(no_usage), 1)                                # recorded as unavailable, not as zero
        self.assertEqual([q["submitted"] for q in queue], ["All done!"])
        self.assertIsNone(queue[0]["human_label"])

    def test_run_ids_are_unique(self):
        for _ in range(2):
            self.cli(steps=[("json", FIN_FALSE)])
        self.assertEqual(len(self.run_dirs()), 2)

    def test_provider_invalid_output_and_agent_failures_stay_separate(self):
        code, _, _ = self.cli("--repeats", "3", "--price-prompt-per-mtok", "1", "--price-completion-per-mtok", "2",
                              steps=[("status", 500, "{}", {}), ("json", BAD_ARGS), ("json", FIN_FALSE)])
        self.assertEqual(code, 0)
        _, episodes, summary, _ = self.load(self.run_dirs()[0])
        self.assertEqual([e["execution"]["termination"] for e in episodes],
                         ["model_error", "invalid_model_output", "finished"])
        self.assertEqual(summary["attribution"], {"agent": 2, "provider": 1})
        agg = summary["aggregate"]
        self.assertEqual((agg["valid_episodes"], agg["infrastructure_error_episodes"]), (2, 1))
        self.assertIn("false_success_claim", episodes[2]["failure_labels"])
        self.assertEqual(summary["estimated_cost_usd"], round((10 / 1e6) * 1 + (4 / 1e6) * 2, 6))

    def test_token_budget_stops_starting_new_episodes(self):
        code, _, _ = self.cli("--repeats", "3", "--max-total-tokens", "1",
                              steps=[("json", FIN_FALSE), ("json", FIN_FALSE)])
        self.assertEqual(code, 1)
        _, episodes, summary, _ = self.load(self.run_dirs()[0])
        self.assertEqual((len(episodes), summary["stop_reason"]), (1, "token_budget_reached"))

    def test_budget_override_is_recorded_in_the_task_spec(self):
        self.cli("--max-agent-ticks", "4", steps=[("json", _tool_reply(("list_invoices", {})))] * 6)
        _, episodes, _, _ = self.load(self.run_dirs()[0])
        self.assertEqual(episodes[0]["task"]["spec"]["max_agent_ticks"], 4)
        self.assertEqual(episodes[0]["execution"]["termination"], "tick_budget_exhausted")
        r.replay_episode(episodes[0])


class TestParserEval(unittest.TestCase):
    def test_metrics_on_a_tiny_corpus(self):
        corpus = {"corpus_id": "t", "corpus_version": "0", "labelling": "test", "items": [
            {"id": "1", "category": "a", "text": "INV-1001 was paid.",
             "gold": {"decision": "accept", "status": "completed", "claims": [["INV-1001", "released"]]}},
            {"id": "2", "category": "a", "text": "All done!", "gold": {"decision": "review", "status": None, "claims": []}},
            {"id": "3", "category": "b", "text": "Released INV-1001. It has settled.",   # pronoun: settled lost
             "gold": {"decision": "accept", "status": "completed", "claims": [["INV-1001", "settled"]]}}]}
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "c.json"
            path.write_text(json.dumps(corpus), encoding="utf-8")
            res = evaluate(path)
        self.assertEqual(res["claims"], {"tp": 1, "fp": 1, "fn": 1})
        self.assertEqual((res["claim_precision"], res["claim_recall"]), (0.5, 0.5))
        self.assertEqual((res["human_review_rate"], res["false_acceptance_rate"]), (round(1 / 3, 4), 0.0))
        self.assertEqual(res["status_accuracy"], 1.0)

    def test_shipped_corpus_is_well_formed_and_deterministic(self):
        a, b = evaluate(), evaluate()
        self.assertEqual(json.dumps(a, sort_keys=True), json.dumps(b, sort_keys=True))
        self.assertGreaterEqual(a["corpus"]["items"], 40)
        self.assertGreater(a["corpus"]["review_items"], 0)

    def test_cli_subcommand(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(cli.main(["parser-eval"]), 0)
        self.assertIn("claim_precision", json.loads(buf.getvalue()))


if __name__ == "__main__":
    unittest.main()
