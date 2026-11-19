"""SQLite store for sanitized run records and events (standard library).

Holds layer-2 data only (see sanitize.py); every write passes assert_safe.
One short-lived connection per operation keeps it safe to use from the
runner thread and the HTTP server threads at once; WAL lets readers proceed
while a run writes.
"""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from .sanitize import assert_safe

EXPERIMENT_STATUSES = ("queued", "running", "completed", "failed")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS experiments (
  experiment_id TEXT PRIMARY KEY,
  status TEXT NOT NULL,
  kind TEXT NOT NULL,
  created_at REAL NOT NULL,
  started_at REAL,
  finished_at REAL,
  updated_at REAL NOT NULL,
  total_trials INTEGER,
  current_task TEXT,
  config TEXT NOT NULL,
  error TEXT
);
CREATE TABLE IF NOT EXISTS trials (
  run_id TEXT PRIMARY KEY,
  experiment_id TEXT NOT NULL REFERENCES experiments(experiment_id),
  seq INTEGER,
  task_id TEXT NOT NULL,
  state TEXT NOT NULL,
  started_at REAL,
  finished_at REAL,
  summary TEXT
);
CREATE INDEX IF NOT EXISTS trials_by_experiment ON trials(experiment_id, seq);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  experiment_id TEXT NOT NULL,
  ts REAL NOT NULL,
  type TEXT NOT NULL,
  payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_by_experiment ON events(experiment_id, id);
"""
STORE_SCHEMA_VERSION = "2"      # 2: experiments.recording (observability integrity)

RECORDING_STATES = ("complete", "incomplete", "unavailable", "in_progress")


def recording_integrity(exp):
    """Whether the live record of one run can be trusted to be whole.

    complete     the run ended, the recorder closed its record, no hook or
                 store write failed, and the trial records reconcile.
    incomplete   at least one event/record write is known to have failed, or
                 the stored records do not reconcile. The history shown is
                 NOT the whole run.
    unavailable  completeness cannot be established (no integrity record,
                 e.g. the recorder never closed it or the row predates this
                 mechanism).
    in_progress  the run has not ended and no failure is known so far.

    This describes the DASHBOARD RECORD only. It says nothing about the
    agent, and the JSONL result files remain the authoritative evidence.
    """
    rec = exp.get("recording")
    failures = (rec or {}).get("hook_failures") or {}
    n_fail = sum(failures.values())
    reasons = []
    terminal = exp["status"] in ("completed", "failed")
    started, finished, total = exp["trials_started"], exp["trials_finished"], exp.get("total_trials")
    if n_fail:
        reasons.append(f"{n_fail} recording write(s) failed")
    if terminal and started != finished:
        reasons.append(f"{started - finished} trial(s) have a start record but no finish record")
    if exp["status"] == "completed" and isinstance(total, int) and finished != total:
        reasons.append(f"{finished} of {total} expected trial records are present")
    if reasons:
        status = "incomplete"
    elif not terminal:
        status = "in_progress"
    elif rec is None or not rec.get("closed"):
        status = "unavailable"
        reasons.append("the recorder did not close this record, so completeness is not established")
    else:
        status = "complete"
    return {"status": status, "reasons": reasons, "write_failures": n_fail,
            "failures_by_hook": dict(sorted(failures.items())),
            "failure_types": dict(sorted(((rec or {}).get("failure_types") or {}).items())),
            "authoritative_evidence": "JSONL result and trajectory files"}


class RunStore:
    def __init__(self, path, clock=time.time):
        self.path = str(path)
        self._clock = clock
        if self.path in ("", ":memory:") or self.path.startswith("file:"):
            # SQLite gives every connection its own private database for these names, and
            # this store opens one connection per operation, so the tables created here would
            # be gone by the next call. The runner and the server also share records through
            # the file. A name starting "file:" (lowercase, exactly as SQLite checks it) is read
            # as a URI by SQLite builds compiled with SQLITE_USE_URI (e.g. Ubuntu's) even without
            # uri=True, which admits in-memory forms (file::memory:, mode=memory, vfs=memdb);
            # elsewhere it is an odd literal file name. Only plain file paths are supported.
            # Refuse clearly instead of failing later.
            raise ValueError(f"RunStore needs a database file path, not {self.path!r}: "
                             "an in-memory or temporary SQLite database is private to one "
                             "connection and cannot hold the shared run record, and SQLite "
                             "'file:' URI names are not supported (pass a plain file path)")
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(_SCHEMA)
            cols = {r["name"] for r in c.execute("PRAGMA table_info(experiments)")}
            if "recording" not in cols:      # schema 1 -> 2, in place, additive
                c.execute("ALTER TABLE experiments ADD COLUMN recording TEXT")
            c.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('schema_version', ?)",
                      (STORE_SCHEMA_VERSION,))

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            with conn:
                yield conn
        finally:
            conn.close()

    # -- writes ------------------------------------------------------------
    def _event(self, c, experiment_id, type_, payload, ts):
        assert_safe(payload, f"event[{type_}]")
        c.execute("INSERT INTO events(experiment_id, ts, type, payload) VALUES (?,?,?,?)",
                  (experiment_id, ts, type_, json.dumps(payload, sort_keys=True)))

    def create_experiment(self, config, status="queued"):
        assert_safe(config, "experiment")
        if status not in EXPERIMENT_STATUSES:
            raise ValueError(f"unknown status {status!r}")
        now = self._clock()
        eid = config["experiment_id"]
        with self._conn() as c:
            c.execute(
                "INSERT INTO experiments(experiment_id, status, kind, created_at, started_at, "
                "updated_at, total_trials, config) VALUES (?,?,?,?,?,?,?,?)",
                (eid, status, config.get("kind", "run"), now, now if status == "running" else None,
                 now, config.get("total_trials"), json.dumps(config, sort_keys=True)))
            self._event(c, eid, f"experiment_{status}",
                        {"total_trials": config.get("total_trials"), "kind": config.get("kind", "run")}, now)
        return eid

    def start_experiment(self, config):
        """Mark an experiment running; create it first if it was never queued."""
        assert_safe(config, "experiment")
        eid = config["experiment_id"]
        now = self._clock()
        with self._conn() as c:
            row = c.execute("SELECT status FROM experiments WHERE experiment_id=?", (eid,)).fetchone()
        if row is None:
            return self.create_experiment(config, status="running")
        with self._conn() as c:
            c.execute("UPDATE experiments SET status='running', started_at=?, updated_at=?, "
                      "total_trials=?, config=? WHERE experiment_id=?",
                      (now, now, config.get("total_trials"), json.dumps(config, sort_keys=True), eid))
            self._event(c, eid, "experiment_running", {"total_trials": config.get("total_trials")}, now)
        return eid

    def trial_started(self, experiment_id, run_id, task_id, seq):
        payload = {"run_id": run_id, "task_id": task_id, "seq": seq}
        now = self._clock()
        with self._conn() as c:
            c.execute("INSERT INTO trials(run_id, experiment_id, seq, task_id, state, started_at) "
                      "VALUES (?,?,?,?, 'running', ?)", (run_id, experiment_id, seq, task_id, now))
            c.execute("UPDATE experiments SET current_task=?, updated_at=? WHERE experiment_id=?",
                      (task_id, now, experiment_id))
            self._event(c, experiment_id, "trial_started", payload, now)
        return now

    def trial_finished(self, experiment_id, summary):
        assert_safe(summary, "trial")
        now = self._clock()
        event = {k: summary.get(k) for k in (
            "run_id", "task_id", "seq", "trial_status", "passed", "failure",
            "termination_reason", "tool_calls", "model_requests")}
        with self._conn() as c:
            cur = c.execute("UPDATE trials SET state='finished', finished_at=?, summary=? WHERE run_id=?",
                            (now, json.dumps(summary, sort_keys=True), summary["run_id"]))
            if cur.rowcount == 0:
                # The start record was never written (its write failed). Keep the
                # finish instead of dropping it silently; started_at stays unknown.
                c.execute("INSERT INTO trials(run_id, experiment_id, seq, task_id, state, finished_at, summary) "
                          "VALUES (?,?,?,?, 'finished', ?, ?)",
                          (summary["run_id"], experiment_id, summary.get("seq"), summary["task_id"], now,
                           json.dumps(summary, sort_keys=True)))
            c.execute("UPDATE experiments SET updated_at=? WHERE experiment_id=?", (now, experiment_id))
            self._event(c, experiment_id, "trial_finished", event, now)
        return now

    def finish_experiment(self, experiment_id, status="completed", error=None):
        if status not in ("completed", "failed"):
            raise ValueError("finish status must be completed or failed")
        if error is not None:
            assert_safe(error, "error")
        now = self._clock()
        with self._conn() as c:
            c.execute("UPDATE experiments SET status=?, finished_at=?, updated_at=?, current_task=NULL, "
                      "error=? WHERE experiment_id=?",
                      (status, now, now, json.dumps(error) if error else None, experiment_id))
            self._event(c, experiment_id, f"experiment_{status}", error or {}, now)

    # -- observability integrity ---------------------------------------------
    def ensure_experiment(self, config, status="running"):
        """Create the experiment row if it does not exist (used when the
        start record could not be written at the time). Returns True if a
        row was created."""
        assert_safe(config, "experiment")
        with self._conn() as c:
            row = c.execute("SELECT 1 FROM experiments WHERE experiment_id=?",
                            (config["experiment_id"],)).fetchone()
        if row is not None:
            return False
        self.create_experiment(config, status=status)
        return True

    def note_recording_failure(self, experiment_id, hook, error_type, source="recorder"):
        """Durably note that one recording write failed. Raises if the
        store itself cannot be written (the caller treats that as one more
        failure to report later)."""
        payload = {"hook": hook, "error_type": error_type, "source": source}
        assert_safe(payload, "recording_failure")
        now = self._clock()
        with self._conn() as c:
            row = c.execute("SELECT recording FROM experiments WHERE experiment_id=?",
                            (experiment_id,)).fetchone()
            if row is None:
                raise KeyError(f"no experiment row for {experiment_id}")
            rec = json.loads(row["recording"]) if row["recording"] else {}
            hooks = rec.setdefault("hook_failures", {})
            hooks[hook] = hooks.get(hook, 0) + 1
            types = rec.setdefault("failure_types", {})
            types[error_type] = types.get(error_type, 0) + 1
            c.execute("UPDATE experiments SET recording=?, updated_at=? WHERE experiment_id=?",
                      (json.dumps(rec, sort_keys=True), now, experiment_id))
            self._event(c, experiment_id, "recording_problem", payload, now)

    def close_recording(self, experiment_id):
        """Mark that the recorder reached the end of the run and wrote its
        last record. Without this a finished run is `unavailable`."""
        with self._conn() as c:
            row = c.execute("SELECT recording FROM experiments WHERE experiment_id=?",
                            (experiment_id,)).fetchone()
            if row is None:
                raise KeyError(f"no experiment row for {experiment_id}")
            rec = json.loads(row["recording"]) if row["recording"] else {}
            rec["closed"] = True
            c.execute("UPDATE experiments SET recording=? WHERE experiment_id=?",
                      (json.dumps(rec, sort_keys=True), experiment_id))

    # -- reads -------------------------------------------------------------
    @staticmethod
    def _experiment(row, counts):
        d = dict(row)
        d["config"] = json.loads(d["config"])
        d["error"] = json.loads(d["error"]) if d["error"] else None
        d["recording"] = json.loads(d["recording"]) if d.get("recording") else None
        d.update(counts)
        return d

    def _counts(self, c, eid):
        r = c.execute("SELECT COUNT(*) n, SUM(state='finished') f FROM trials WHERE experiment_id=?",
                      (eid,)).fetchone()
        return {"trials_started": r["n"] or 0, "trials_finished": r["f"] or 0}

    def list_experiments(self):
        with self._conn() as c:
            rows = c.execute("SELECT * FROM experiments ORDER BY created_at DESC, experiment_id").fetchall()
            return [self._experiment(r, self._counts(c, r["experiment_id"])) for r in rows]

    def get_experiment(self, experiment_id):
        with self._conn() as c:
            r = c.execute("SELECT * FROM experiments WHERE experiment_id=?", (experiment_id,)).fetchone()
            return self._experiment(r, self._counts(c, experiment_id)) if r else None

    def list_trials(self, experiment_id):
        with self._conn() as c:
            rows = c.execute("SELECT * FROM trials WHERE experiment_id=? ORDER BY seq, started_at",
                             (experiment_id,)).fetchall()
        out = []
        for r in rows:
            d = {"run_id": r["run_id"], "task_id": r["task_id"], "seq": r["seq"], "state": r["state"],
                 "started_at": r["started_at"], "finished_at": r["finished_at"]}
            if r["summary"]:
                d.update(json.loads(r["summary"]))
                d["state"] = r["state"]
            out.append(d)
        return out

    def events_after(self, after_id=0, experiment_id=None, limit=500):
        q = "SELECT * FROM events WHERE id > ?"
        args = [int(after_id)]
        if experiment_id:
            q += " AND experiment_id = ?"
            args.append(experiment_id)
        q += " ORDER BY id LIMIT ?"
        args.append(int(limit))
        with self._conn() as c:
            rows = c.execute(q, args).fetchall()
        return [{"id": r["id"], "experiment_id": r["experiment_id"], "ts": r["ts"], "type": r["type"],
                 "payload": json.loads(r["payload"])} for r in rows]

    def last_event_id(self):
        with self._conn() as c:
            return c.execute("SELECT COALESCE(MAX(id), 0) m FROM events").fetchone()["m"]
