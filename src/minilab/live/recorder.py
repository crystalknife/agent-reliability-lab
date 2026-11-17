"""Event sink that the runner calls; writes sanitized records to a RunStore.

The runner hands this object RAW rows and trajectories. They are reduced by
sanitize.py here and are not kept. A recorder failure must never change an
experiment's results: the runner swallows sink exceptions (see
runner._notify), and this class additionally counts its own failures.
"""

from __future__ import annotations

from .sanitize import label, safe_error, safe_experiment, safe_trial


class EventSink:
    """Interface the runner calls. All methods are optional no-ops."""

    def experiment_started(self, info):
        pass

    def trial_started(self, info):
        pass

    def trial_finished(self, row, trajectory):
        pass

    def experiment_finished(self, experiment_id, n_trials):
        pass

    def experiment_failed(self, experiment_id, exc):
        pass


class LiveRecorder(EventSink):
    """Writes sanitized records. Never raises out of a hook: a failed write
    is counted in `failure_log`, noted durably in the store when the store
    can still be written, and retried as a note at later hooks if it could
    not be. A run whose record has any failed write is reported by the API
    as recording `incomplete`."""

    def __init__(self, store, *, kind="run", label_text=None, experiment_id=None, extra=None):
        self.store = store
        self.kind = kind
        self.label_text = label_text
        self.queued_experiment_id = experiment_id   # row created earlier as "queued"
        self.extra = dict(extra or {})
        self.failure_log = []       # every failed write: {"hook", "error_type"}
        self._unnoted = []          # failures not yet written to the store
        self._seq = {}
        self._started = {}
        self._alias = {}
        self._config = {}

    @property
    def failures(self):
        return len(self.failure_log)

    def _eid(self, experiment_id):
        return self._alias.get(experiment_id, experiment_id)

    # -- failure accounting --------------------------------------------------
    def _flush_notes(self, eid):
        """Try to write pending failure notes; keep what still cannot be written."""
        pending, self._unnoted = self._unnoted, []
        for item in pending:
            try:
                if eid in self._config:
                    self.store.ensure_experiment(self._config[eid])
                self.store.note_recording_failure(eid, item["hook"], item["error_type"], item["source"])
            except Exception:  # noqa: BLE001 - store still unavailable; keep for a later attempt
                self._unnoted.append(item)

    def _failed(self, eid, hook, exc, source="recorder"):
        item = {"hook": hook, "error_type": type(exc).__name__ if isinstance(exc, BaseException) else str(exc),
                "source": source}
        self.failure_log.append({"hook": item["hook"], "error_type": item["error_type"]})
        self._unnoted.append(item)
        self._flush_notes(eid)

    def _guarded(self, eid, hook, fn):
        try:
            fn()
        except Exception as e:  # noqa: BLE001 - recorded, never propagated
            self._failed(eid, hook, e)
            return False
        if self._unnoted:
            self._flush_notes(eid)
        return True

    # -- hooks ---------------------------------------------------------------
    def experiment_started(self, info):
        info = {**info, **self.extra, "kind": self.kind, "label": self.label_text}
        runner_id = info["experiment_id"]
        if self.queued_experiment_id:
            self._alias[runner_id] = self.queued_experiment_id
            info["experiment_id"] = self.queued_experiment_id
        eid = info["experiment_id"]

        def write():
            self._config[eid] = safe_experiment(info)
            self.store.start_experiment(self._config[eid])

        self._guarded(eid, "experiment_started", write)

    def trial_started(self, info):
        eid = self._eid(info["experiment_id"])
        seq = self._seq[eid] = self._seq.get(eid, 0) + 1
        run_id = label(info["run_id"], 64)
        self._started[run_id] = (seq, None)

        def write():
            self._started[run_id] = (seq, self.store.trial_started(eid, run_id, label(info["task_id"], 80), seq))

        self._guarded(eid, "trial_started", write)

    def trial_finished(self, row, trajectory):
        eid = self._eid(row["experiment_id"])
        seq, started = self._started.pop(label(row["run_id"], 64), (None, None))

        def write():
            summary = safe_trial({**row, "experiment_id": eid}, trajectory, seq=seq, started_at=started)
            self.store.trial_finished(eid, summary)

        self._guarded(eid, "trial_finished", write)

    def _finish(self, eid, hook, status, error=None):
        ok = self._guarded(eid, hook, lambda: self.store.finish_experiment(eid, status, error))
        if ok and not self._unnoted:
            self._guarded(eid, "close_recording", lambda: self.store.close_recording(eid))

    def experiment_finished(self, experiment_id, n_trials):
        self._finish(self._eid(experiment_id), "experiment_finished", "completed")

    def experiment_failed(self, experiment_id, exc):
        self._finish(self._eid(experiment_id), "experiment_failed", "failed", safe_error(exc))

    def observability_report(self, experiment_id, summary):
        """Hook failures the RUNNER saw (hooks of this sink that raised).
        This class catches its own failures, so normally there are none."""
        eid = self._eid(experiment_id)
        for f in summary.get("hook_failures", []):
            self._failed(eid, label(f.get("hook"), 40), label(f.get("error_type"), 60), source="runner")
        if self._unnoted:
            self._flush_notes(eid)
