"""BankOps ModelAdapter (readiness doc §8, tests A1–A10). Loopback fake servers only; no network."""

import json
import os
import socket
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from minilab.bankops import models as m
from minilab.env import MiniBankEnv
from minilab.agent import run_agent
from minilab.tools import TOOL_SCHEMAS

SENTINEL = "sk-SENTINEL-do-not-leak-9f3a"
KEY_ENV = "BANKOPS_TEST_API_KEY"


def _tool_reply(*calls):
    return {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
        {"id": f"c{i}", "type": "function", "function": {"name": n, "arguments": json.dumps(a)}}
        for i, (n, a) in enumerate(calls)]}}], "usage": {"prompt_tokens": 5, "completion_tokens": 2}}


def _final_reply(text):
    return {"choices": [{"message": {"role": "assistant", "content": text}}]}


class FakeServer:
    """Scripted OpenAI-compatible endpoint. Each request pops the next step:
    ("json", obj) | ("raw", bytes) | ("status", code, body, headers) | ("hang",)."""

    def __init__(self):
        self.steps, self.requests = [], []
        self.props = None          # optional GET /props body (llama-server style); None -> 404
        self.release = threading.Event()
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):  # noqa: N802
                data = json.dumps(outer.props).encode() if outer.props is not None else b"{}"
                self.send_response(200 if (outer.props is not None and self.path == "/props") else 404)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):  # noqa: N802
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))  # always drain
                outer.requests.append({"path": self.path, "headers": dict(self.headers),
                                       "json": json.loads(body or b"{}")})
                step = outer.steps.pop(0) if outer.steps else ("json", _final_reply("default"))
                try:
                    if step[0] == "hang":
                        outer.release.wait(10)
                        return
                    if step[0] == "status":
                        _, code, payload, headers = step
                        self.send_response(code)
                        for k, v in headers.items():
                            self.send_header(k, v)
                        data = payload.encode()
                    else:
                        self.send_response(200)
                        data = json.dumps(step[1]).encode() if step[0] == "json" else step[1]
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                except OSError:
                    pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.httpd.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/v1"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.release.set()
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(5)


class AdapterCase(unittest.TestCase):
    def setUp(self):
        self.srv = FakeServer()
        os.environ[KEY_ENV] = SENTINEL
        self.profile = m.ModelProfile(id="fake-api", kind="api", base_url=self.srv.url, model_id="fake-model",
                                      credential_ref=f"env:{KEY_ENV}", timeout_s=5)
        self.adapter = m.OpenAICompatibleAdapter(self.profile, sleep=lambda s: self.sleeps.append(s))
        self.sleeps = []

    def tearDown(self):
        self.srv.close()
        os.environ.pop(KEY_ENV, None)


class TestProfilesAndCredentials(unittest.TestCase):  # A1
    def test_only_env_references_are_accepted(self):
        with self.assertRaises(m.ConfigError) as ctx:
            m.ModelProfile(id="p", kind="api", base_url="https://x/v1", model_id="m", credential_ref=SENTINEL)
        self.assertNotIn(SENTINEL, str(ctx.exception))
        for bad in ("env:", "env:lower", "file:/x", "ENV:KEY"):
            with self.assertRaises(m.ConfigError):
                m.ModelProfile(id="p", kind="api", base_url="https://x/v1", model_id="m", credential_ref=bad)

    def test_profile_validation(self):
        with self.assertRaises(m.ConfigError):
            m.ModelProfile(id="p", kind="api", base_url="https://x/v1", model_id="m")  # api needs a ref
        with self.assertRaises(m.ConfigError):
            m.ModelProfile(id="p", kind="cloud", base_url="https://x/v1", model_id="m")
        with self.assertRaises(m.ConfigError):
            m.ModelProfile(id="p", kind="local", base_url="ftp://x", model_id="m")
        with self.assertRaises(m.ConfigError):
            m.ModelProfile(id="p", kind="local", base_url="http://x/v1", model_id="")
        with self.assertRaises(m.ConfigError):
            m.ModelProfile(id="p", kind="local", base_url="http://x/v1", model_id="m", timeout_s=0)

    def test_missing_variable_is_a_config_error_without_any_value(self):
        os.environ.pop("BANKOPS_UNSET_KEY", None)
        with self.assertRaises(m.ConfigError) as ctx:
            m.resolve_credential("env:BANKOPS_UNSET_KEY")
        self.assertIn("env:BANKOPS_UNSET_KEY", str(ctx.exception))

    def test_example_profiles(self):
        api = m.api_profile("some/model")
        local = m.local_profile("qwen3:4b")
        self.assertEqual((api.kind, api.credential_ref), ("api", "env:OPENROUTER_API_KEY"))
        self.assertEqual((local.kind, local.credential_ref), ("local", None))
        self.assertTrue(local.base_url.startswith("http://127.0.0.1"))


class TestTranslationAndParsing(AdapterCase):  # A2, A3
    def test_request_shape_and_single_tool_call(self):
        self.srv.steps.append(("json", _tool_reply(("get_customer", {"customer_id": "C101"}))))
        out = self.adapter.complete([{"role": "user", "content": "hi"}], TOOL_SCHEMAS, {"temperature": 0.0})
        self.assertEqual(out, {"tool": "get_customer", "arguments": {"customer_id": "C101"}})
        req = self.srv.requests[0]
        self.assertEqual(req["path"], "/v1/chat/completions")
        self.assertEqual(req["headers"]["Authorization"], f"Bearer {SENTINEL}")
        self.assertEqual(req["json"]["model"], "fake-model")
        self.assertEqual(req["json"]["tools"], m.to_api_tools(TOOL_SCHEMAS))
        names = [t["function"]["name"] for t in req["json"]["tools"]]
        self.assertEqual(names, [t["name"] for t in TOOL_SCHEMAS])
        self.assertEqual(self.adapter.last_call["usage"]["completion_tokens"], 2)

    def test_multiple_calls_and_final_text(self):
        self.srv.steps.append(("json", _tool_reply(("get_account", {"account_id": "A1"}),
                                                   ("get_customer", {"customer_id": "C1"}))))
        self.srv.steps.append(("json", _final_reply("done")))
        out = self.adapter.complete([{"role": "user", "content": "x"}], TOOL_SCHEMAS, {})
        self.assertEqual([c["tool"] for c in out["calls"]], ["get_account", "get_customer"])
        self.assertEqual(self.adapter.complete([{"role": "user", "content": "x"}], [], {}), {"final": "done"})
        self.assertNotIn("tools", self.srv.requests[1]["json"])

    def test_drives_the_legacy_agent_loop(self):
        self.srv.steps.append(("json", _tool_reply(("get_customer", {"customer_id": "C101"}))))
        self.srv.steps.append(("json", _final_reply("Customer C101 found.")))
        traj = run_agent("t", "Look up customer C101.", MiniBankEnv(), self.adapter.as_model_fn(), max_steps=3,
                         meta={"model": "fake-model", "provider": "fake"})
        self.assertEqual(traj.final_answer, "Customer C101 found.")
        self.assertEqual(len(self.srv.requests), 2)
        second = self.srv.requests[1]["json"]["messages"]
        self.assertTrue(any(msg.get("role") == "tool" for msg in second))  # to_api_messages translation


class TestMalformedResponses(AdapterCase):  # A4
    def test_malformed_variants(self):
        bad_args = {"choices": [{"message": {"tool_calls": [{"function": {"name": "x", "arguments": "{oops"}}]}}]}
        no_name = {"choices": [{"message": {"tool_calls": [{"function": {"arguments": "{}"}}]}}]}
        cases = [(("raw", b"<html>not json</html>"), "provider"), (("json", {"no": "choices"}), "provider"),
                 (("json", {"choices": []}), "provider"), (("json", [1, 2]), "provider"),
                 (("json", bad_args), "model"), (("json", no_name), "model")]   # model-generated content
        for step, origin in cases:
            with self.subTest(step=str(step)[:60]):
                self.srv.steps.append(step)
                with self.assertRaises(m.MalformedResponseError) as ctx:
                    self.adapter.complete([{"role": "user", "content": "x"}], TOOL_SCHEMAS, {})
                self.assertEqual(ctx.exception.origin, origin)
                self.assertEqual(isinstance(ctx.exception, m.InvalidToolCallError), origin == "model")


class TestHttpErrors(AdapterCase):  # A5
    def test_status_mapping(self):
        cases = [(401, m.AuthError), (403, m.AuthError), (404, m.NotFoundError), (400, m.RequestRejectedError),
                 (429, m.RateLimitError), (500, m.ServerError), (503, m.ServerError)]
        for code, cls in cases:
            with self.subTest(code=code):
                self.srv.steps.append(("status", code, '{"error":"x"}', {}))
                with self.assertRaises(cls) as ctx:
                    self.adapter.complete([{"role": "user", "content": "x"}], [], {})
                self.assertEqual((ctx.exception.status, ctx.exception.origin), (code, "provider"))

    def test_retry_after_is_parsed(self):
        self.srv.steps.append(("status", 429, "slow down", {"Retry-After": "7"}))
        with self.assertRaises(m.RateLimitError) as ctx:
            self.adapter.complete([{"role": "user", "content": "x"}], [], {})
        self.assertEqual(ctx.exception.retry_after, 7.0)


class TestTimeoutsAndTransport(AdapterCase):  # A6
    def test_timeout_is_bounded_and_typed(self):
        prof = m.ModelProfile(id="slow", kind="local", base_url=self.srv.url, model_id="m", timeout_s=0.4)
        self.srv.steps.append(("hang",))
        import time
        t0 = time.monotonic()
        with self.assertRaises(m.ModelTimeoutError) as ctx:
            m.OpenAICompatibleAdapter(prof).complete([{"role": "user", "content": "x"}], [], {})
        self.assertLess(time.monotonic() - t0, 3.0)
        self.assertEqual(ctx.exception.origin, "transport")

    def test_connection_refused_is_transport(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()  # nothing listens here now
        prof = m.ModelProfile(id="down", kind="local", base_url=f"http://127.0.0.1:{port}/v1", model_id="m",
                              timeout_s=5)
        with self.assertRaises((m.TransportError, m.ModelTimeoutError)) as ctx:
            m.OpenAICompatibleAdapter(prof).complete([{"role": "user", "content": "x"}], [], {})
        self.assertEqual(ctx.exception.origin, "transport")


class TestRetryPolicy(AdapterCase):  # A7
    def _adapter(self, retries):
        prof = m.ModelProfile(id="r", kind="local", base_url=self.srv.url, model_id="m", timeout_s=5,
                              max_retries=retries)
        return m.OpenAICompatibleAdapter(prof, sleep=self.sleeps.append)

    def test_no_retry_by_default(self):
        self.srv.steps += [("status", 503, "x", {}), ("json", _final_reply("ok"))]
        with self.assertRaises(m.ServerError):
            self._adapter(0).complete([{"role": "user", "content": "x"}], [], {})
        self.assertEqual((len(self.srv.requests), self.sleeps), (1, []))

    def test_retries_only_retryable_errors_deterministically(self):
        self.srv.steps += [("status", 429, "x", {"Retry-After": "2"}), ("status", 502, "x", {}),
                           ("json", _final_reply("ok"))]
        a = self._adapter(2)
        self.assertEqual(a.complete([{"role": "user", "content": "x"}], [], {}), {"final": "ok"})
        self.assertEqual(self.sleeps, [2.0, 1.0])
        self.assertEqual(a.last_call["attempts"], 3)
        self.srv.steps += [("status", 401, "x", {}), ("json", _final_reply("never"))]
        with self.assertRaises(m.AuthError):
            a.complete([{"role": "user", "content": "x"}], [], {})
        self.assertEqual(len(self.srv.requests), 4)  # auth errors are never retried


class TestHealthCheck(AdapterCase):  # A8
    def test_never_raises(self):
        self.srv.steps.append(("json", _final_reply("pong")))
        ok = m.health_check(self.profile)
        self.assertEqual((ok["ok"], ok["kind"]), (True, "ok"))
        self.assertEqual(self.srv.requests[0]["json"]["max_tokens"], 1)
        self.srv.steps.append(("status", 401, "bad key", {}))
        self.assertEqual(m.health_check(self.profile)["kind"], "auth")
        self.srv.steps.append(("raw", b"garbage"))
        self.assertEqual(m.health_check(self.profile)["kind"], "malformed")
        os.environ.pop(KEY_ENV)
        res = m.health_check(self.profile)
        self.assertEqual((res["ok"], res["kind"]), (False, "config"))
        self.assertIsInstance(res["latency_ms"], float)


class TestNoSecretLeak(AdapterCase):  # A9
    def test_sentinel_never_appears(self):
        # the server echoes the Authorization header back in an error body
        self.srv.steps.append(("status", 401, f"invalid key Bearer {SENTINEL}", {}))
        with self.assertRaises(m.AuthError) as ctx:
            self.adapter.complete([{"role": "user", "content": "x"}], [], {})
        self.srv.steps.append(("status", 500, SENTINEL, {}))
        health = m.health_check(self.profile)
        surfaces = [repr(self.profile), str(self.profile), repr(self.adapter), str(ctx.exception),
                    json.dumps(self.adapter.last_call), json.dumps(health)]
        for s in surfaces:
            self.assertNotIn(SENTINEL, s)
        self.assertIn("***", str(ctx.exception))


class TestNoNetworkOrSideEffects(unittest.TestCase):  # A10
    def test_import_has_no_side_effects_and_tests_use_loopback_only(self):
        import importlib.util
        # Execute a private copy: importing performs no I/O and resolves no credential. (An in-place
        # reload would rebind the shared module's exception classes and break `except` clauses elsewhere.)
        spec = importlib.util.find_spec("minilab.bankops.models")
        spec.loader.exec_module(importlib.util.module_from_spec(spec))
        srv = FakeServer()
        try:
            self.assertTrue(srv.url.startswith("http://127.0.0.1:"))
        finally:
            srv.close()


if __name__ == "__main__":
    unittest.main()
