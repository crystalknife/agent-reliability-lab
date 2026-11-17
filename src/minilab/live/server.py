"""Local HTTP + Server-Sent Events API over the sanitized run store.

    python -m minilab.live.server            # database: ~/.arl/live/runs.sqlite

Security posture (read this before changing it):
  * No authentication. Therefore the server binds to loopback only and
    refuses any other bind address. Do not put it behind a public proxy.
    Deployment beyond this machine needs authentication first.
  * Requests whose Host header is not a loopback name are rejected (DNS
    rebinding). No CORS headers are sent, so other origins cannot read
    responses; the one state-changing endpoint also requires a custom
    header, which browsers will not send cross-origin without a preflight.
  * Only layer-2/3 data is served (see sanitize.py). There is no endpoint
    for trajectories, prompts, tool arguments or observations, and the
    legacy static exports are not served from here.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from .analytics import compare_experiments, experiment_analytics, task_info
from .store import RunStore, recording_integrity
from .synthetic import start_synthetic_run

LOOPBACK_BINDS = ("127.0.0.1", "localhost", "::1")
# Outside the repository on purpose: run records must not be committed or
# published by accident.
DEFAULT_DB = Path.home() / ".arl" / "live" / "runs.sqlite"
API_VERSION = "1"
STALE_AFTER_S = 120.0
MAX_BODY = 10_000        # largest POST body that is accepted
MAX_DRAIN = 64 * 1024    # most bytes read and discarded from a body that is rejected
BODY_DEADLINE_S = 2.0    # total time allowed to read (or drain) one request body


def code_revision(repo_root=None):
    """Current commit hash, read-only; None when unavailable."""
    try:
        out = subprocess.run(["git", "--no-optional-locks", "rev-parse", "HEAD"], cwd=repo_root,
                             capture_output=True, text=True, timeout=5,
                             env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"})
        return out.stdout.strip() or None if out.returncode == 0 else None
    except Exception:  # noqa: BLE001
        return None


def public_experiment(exp, now):
    """Experiment row as served: adds progress and a staleness flag."""
    total = exp.get("total_trials")
    done = exp["trials_finished"]
    out = {
        "experiment_id": exp["experiment_id"], "status": exp["status"], "kind": exp["kind"],
        "source": "live_backend", "config": exp["config"], "error": exp["error"],
        "created_at": exp["created_at"], "started_at": exp["started_at"],
        "finished_at": exp["finished_at"], "updated_at": exp["updated_at"],
        "current_task": exp["current_task"] if exp["status"] == "running" else None,
        "progress": {"finished": done, "started": exp["trials_started"], "total": total,
                     "remaining": (total - done) if isinstance(total, int) else None,
                     "fraction": (done / total) if total else None},
        "elapsed_s": ((exp["finished_at"] or now) - exp["started_at"]) if exp["started_at"] else None,
        "stale": False, "stale_reason": None,
        "recording": recording_integrity(exp),
    }
    if exp["status"] in ("running", "queued") and now - exp["updated_at"] > STALE_AFTER_S:
        out["stale"] = True
        out["stale_reason"] = (f"no update for {int(now - exp['updated_at'])} s; the run may be in a long "
                               "model call or may have been interrupted (not marked failed automatically)")
    return out


class LiveServer(ThreadingHTTPServer):
    daemon_threads = True
    # POSIX SO_REUSEADDR only permits rebinding over TIME_WAIT. On Windows it lets a second
    # socket bind a port that is in use, so there the bind asks for exclusive use instead.
    allow_reuse_address = os.name != "nt"

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):  # Windows only
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def __init__(self, addr, store, *, enable_synthetic=False, static_dir=None,
                 heartbeat_s=10.0, poll_s=0.25, repo_root=None):
        if addr[0] not in LOOPBACK_BINDS:
            raise ValueError(
                f"refusing to bind {addr[0]!r}: this server has no authentication and is loopback-only")
        super().__init__(addr, Handler)
        self.store = store
        self.enable_synthetic = enable_synthetic
        self.static_dir = Path(static_dir).resolve() if static_dir else None
        self.heartbeat_s = heartbeat_s
        self.poll_s = poll_s
        self.closing = threading.Event()
        self.revision = code_revision(repo_root)
        self.started_at = time.time()

    def shutdown(self):
        self.closing.set()
        super().shutdown()


class Handler(BaseHTTPRequestHandler):
    server_version = "ARLLive/" + API_VERSION
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):  # quiet; the store is the record
        pass

    # -- helpers -----------------------------------------------------------
    def _host_ok(self):
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]").lower()
        return host in LOOPBACK_BINDS

    def _json(self, status, body):
        data = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if self.close_connection:
            self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(data)

    def _not_found(self):
        self._json(404, {"error": "not found"})

    def _content_length(self):
        """The declared body length, or None when the framing is malformed or ambiguous."""
        if self.headers.get("Transfer-Encoding") is not None:
            return None  # chunked bodies are not supported; never guess their length
        values = self.headers.get_all("Content-Length") or []
        if len(values) > 1:
            return None  # duplicate lengths are a request-smuggling vector
        v = values[0].strip() if values else "0"
        return int(v) if v.isascii() and v.isdigit() else None

    def _read_body(self, n):
        """Read exactly n body bytes within BODY_DEADLINE_S overall; None if they do not arrive."""
        deadline = time.monotonic() + BODY_DEADLINE_S
        old_timeout = self.connection.gettimeout()
        chunks, left = [], n
        try:
            while left > 0:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self.connection.settimeout(remaining)
                chunk = self.rfile.read1(min(left, 65536))
                if not chunk:
                    return None
                chunks.append(chunk)
                left -= len(chunk)
        except OSError:  # timeout or reset
            return None
        finally:
            self.connection.settimeout(old_timeout)
        return b"".join(chunks)

    def _reject(self, status, body):
        """Refuse a POST without leaving its body unread, then close the connection.

        On Windows, closing a socket that still holds unread bytes sends a reset, so the
        client may never see the status. A bounded, deadline-limited drain avoids that;
        closing afterwards means no leftover byte is ever parsed as a further request.
        """
        self.close_connection = True
        n = self._content_length()
        if n is not None and 0 < n <= MAX_DRAIN:
            self._read_body(n)  # discarded; a short or slow body just ends the drain
        self._json(status, body)

    # -- routing -----------------------------------------------------------
    def do_GET(self):  # noqa: N802
        if not self._host_ok():
            return self._json(403, {"error": "forbidden host"})
        url = urlparse(self.path)
        parts = [p for p in url.path.split("/") if p]
        q = parse_qs(url.query)
        store = self.server.store
        try:
            if parts[:1] != ["api"]:
                return self._static(url.path)
            if parts == ["api", "health"]:
                return self._json(200, {
                    "status": "ok", "api_version": API_VERSION, "server_time": time.time(),
                    "uptime_s": time.time() - self.server.started_at,
                    "code_revision": self.server.revision, "last_event_id": store.last_event_id(),
                    "synthetic_runs_enabled": self.server.enable_synthetic,
                    "authentication": "none (loopback only)", "data_layers_served": [
                        "sanitized run metadata", "aggregated analytics"]})
            if parts == ["api", "experiments"]:
                now = time.time()
                return self._json(200, {"experiments": [public_experiment(e, now)
                                                        for e in store.list_experiments()],
                                        "server_time": now})
            if len(parts) >= 3 and parts[:2] == ["api", "experiments"]:
                exp = store.get_experiment(parts[2])
                if exp is None:
                    return self._not_found()
                trials = store.list_trials(parts[2])
                if len(parts) == 3:
                    if exp["status"] in ("completed", "failed"):
                        # The run is over: a trial without a finish record did not
                        # keep running, its finish was simply never recorded.
                        trials = [dict(t, state="unrecorded") if t["state"] == "running" else t
                                  for t in trials]
                    return self._json(200, {"experiment": public_experiment(exp, time.time()),
                                            "trials": [dict(t, task=task_info(t["task_id"])) for t in trials],
                                            "analytics": experiment_analytics(trials, recording_integrity(exp))})
                if parts[3:] == ["events"]:
                    after = int(q.get("after", ["0"])[0])
                    return self._json(200, {"events": store.events_after(after, parts[2])})
                return self._not_found()
            if parts == ["api", "events"]:
                after = int(q.get("after", ["0"])[0])
                return self._json(200, {"events": store.events_after(after)})
            if parts == ["api", "compare"]:
                a, b = q.get("a", [None])[0], q.get("b", [None])[0]
                ea = store.get_experiment(a) if a else None
                eb = store.get_experiment(b) if b else None
                if ea is None or eb is None:
                    return self._json(400, {"error": "compare needs two existing experiment ids: a, b"})
                return self._json(200, {"a": a, "b": b,
                                        "comparison": compare_experiments(
                                            store.list_trials(a), store.list_trials(b),
                                            recording_integrity(ea), recording_integrity(eb))})
            if parts == ["api", "stream"]:
                return self._stream(q)
            return self._not_found()
        except (BrokenPipeError, ConnectionResetError):
            return None
        except ValueError:
            return self._json(400, {"error": "bad request"})

    def do_POST(self):  # noqa: N802
        if not self._host_ok():
            return self._reject(403, {"error": "forbidden host"})
        if urlparse(self.path).path != "/api/dev/synthetic-run":
            return self._reject(404, {"error": "not found"})
        if not self.server.enable_synthetic:
            return self._reject(403, {"error": "synthetic runs are disabled (start with --enable-synthetic)"})
        if self.headers.get("X-ARL-Local") != "1":
            return self._reject(403, {"error": "missing X-ARL-Local header"})
        n = self._content_length()
        if n is None:
            return self._reject(400, {"error": "bad request (ValueError)"})
        if n > MAX_BODY:
            return self._reject(413, {"error": "body too large"})
        raw = self._read_body(n)
        if raw is None:
            self.close_connection = True
            return self._json(408, {"error": "request body incomplete"})
        try:
            body = json.loads(raw or b"{}")
            if not isinstance(body, dict):
                raise TypeError("body must be a JSON object")
            eid, _ = start_synthetic_run(
                self.server.store, model=body.get("model", "scripted"), task_ids=body.get("task_ids"),
                repeats=body.get("repeats", 1), delay_s=body.get("delay_s", 0.0),
                exec_mode=body.get("exec_mode", "sequential"), code_revision=self.server.revision,
                fail_after=body.get("fail_after"))
        except (ValueError, TypeError) as e:
            self.close_connection = True  # every rejected POST ends its connection
            return self._json(400, {"error": f"bad request ({type(e).__name__})"})
        return self._json(202, {"experiment_id": eid, "kind": "synthetic"})

    # -- SSE ---------------------------------------------------------------
    def _stream(self, q):
        try:
            last = int(self.headers.get("Last-Event-ID") or q.get("after", ["0"])[0])
        except ValueError:
            last = 0
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        srv = self.server
        try:
            self.wfile.write(b"retry: 2000\n: connected\n\n")
            self.wfile.flush()
            beat = time.monotonic()
            while not srv.closing.is_set():
                events = srv.store.events_after(last, limit=200)
                for ev in events:
                    last = ev["id"]
                    chunk = f"id: {ev['id']}\nevent: {ev['type']}\ndata: {json.dumps(ev)}\n\n"
                    self.wfile.write(chunk.encode("utf-8"))
                if events:
                    self.wfile.flush()
                    beat = time.monotonic()
                elif time.monotonic() - beat >= srv.heartbeat_s:
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
                    beat = time.monotonic()
                if not events:
                    srv.closing.wait(srv.poll_s)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    # -- optional static frontend -------------------------------------------
    def _static(self, path):
        root = self.server.static_dir
        if root is None:
            return self._not_found()
        refused = {"error": "raw trajectory exports are not served by the live service"}
        segments = [s for s in re.split(r"[\\/]", unquote(path)) if s]
        # Windows drops trailing dots and spaces from names and reads "name:stream" as an NTFS
        # stream, so such a segment can open a file the name checks never saw; "." and ".."
        # end in a dot too. None of these is a legitimate asset path.
        if any(s[-1] in ". " or ":" in s or "\0" in s for s in segments):
            return self._json(403, {"error": "forbidden"})
        if segments and segments[-1].lower().endswith(".traj.json"):
            # Legacy raw-trajectory exports may sit in a built frontend's data
            # folder (owner decision U9 is open). This service never serves them.
            return self._json(403, refused)
        target = root.joinpath(*segments).resolve()
        if root not in target.parents and target != root:
            return self._json(403, {"error": "forbidden"})
        if target.name.lower().endswith(".traj.json"):  # the file actually reached (links, short names)
            return self._json(403, refused)
        if not target.is_file():
            target = root / "index.html"          # SPA fallback
            if not target.is_file():
                return self._not_found()
        data = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(str(target))[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Local ARL live API (loopback only, no authentication).")
    ap.add_argument("--db", default=str(DEFAULT_DB),
                    help="SQLite file for sanitized run records (default: outside the repository)")
    ap.add_argument("--host", default="127.0.0.1", choices=LOOPBACK_BINDS)
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--enable-synthetic", action="store_true",
                    help="allow POST /api/dev/synthetic-run (development only)")
    ap.add_argument("--static", default=None, help="serve a built frontend from this directory")
    a = ap.parse_args(argv)
    try:
        server = LiveServer((a.host, a.port), RunStore(a.db), enable_synthetic=a.enable_synthetic,
                            static_dir=a.static)
    except OSError as e:
        raise SystemExit(f"cannot listen on {a.host}:{a.port} ({e.strerror or e}); "
                         "is another live server already running on this port?") from e
    print(f"ARL live API on http://{a.host}:{server.server_address[1]} (loopback only; no authentication)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
