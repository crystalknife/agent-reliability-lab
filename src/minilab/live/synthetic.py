"""Controlled SYNTHETIC runs for developing and testing the live path.

A synthetic run drives the real runner with one of the two built-in offline
models ("scripted": replays the oracle tool sequence; "stub": always answers
with one fixed sentence). No provider is called and no model is evaluated.
Result rows and trajectories go to a temporary directory that is deleted
when the run ends; they never touch results/. The run is stored with
kind="synthetic" and the dashboard labels it as such. Its pass rate measures
harness plumbing, not agent reliability, and must never be reported as an
experimental result.
"""

from __future__ import annotations

import tempfile
import threading
import time
import uuid

from ..runner import run_experiment
from ..tasks import TASK_SCHEMA_VERSION, TASKS
from .recorder import LiveRecorder
from .sanitize import safe_experiment

SYNTHETIC_MODELS = ("scripted", "stub")
MAX_DELAY_S = 5.0


class _PacedRecorder(LiveRecorder):
    """Adds a pause before each trial so progress is visible in a UI."""

    def __init__(self, *a, delay_s=0.0, **kw):
        super().__init__(*a, **kw)
        self.delay_s = delay_s

    def trial_started(self, info):
        super().trial_started(info)
        if self.delay_s:
            time.sleep(self.delay_s)


class InvalidSyntheticRequest(ValueError):
    """A caller-supplied synthetic-run argument is invalid (raised before anything is stored).
    Distinct from any other ValueError, which is an internal fault, not bad input."""


def start_synthetic_run(store, *, model="scripted", task_ids=None, repeats=1, delay_s=0.0,
                        exec_mode="sequential", code_revision=None, fail_after=None):
    """Queue a synthetic run and execute it on a background thread.
    Returns (experiment_id, thread). fail_after=N makes the run raise after
    N trials (to exercise the failed-run path). Invalid arguments raise
    InvalidSyntheticRequest (a ValueError)."""
    if model not in SYNTHETIC_MODELS:
        raise InvalidSyntheticRequest(f"synthetic runs use one of {SYNTHETIC_MODELS}, got {model!r}")
    known = [t["task_id"] for t in TASKS]
    try:
        task_ids = list(task_ids or known)
    except TypeError:
        raise InvalidSyntheticRequest("task_ids must be a list of task ids") from None
    unknown = [t for t in task_ids if t not in known]
    if unknown:
        raise InvalidSyntheticRequest(f"unknown task id(s): {unknown}")
    if not isinstance(repeats, int) or isinstance(repeats, bool) or not 1 <= repeats <= 20:
        raise InvalidSyntheticRequest("repeats must be an int in [1, 20]")
    try:
        delay_s = max(0.0, min(float(delay_s), MAX_DELAY_S))
    except (TypeError, ValueError):
        raise InvalidSyntheticRequest("delay_s must be a number") from None
    if exec_mode not in ("sequential", "batch"):
        raise InvalidSyntheticRequest("exec_mode must be 'sequential' or 'batch'")

    experiment_id = f"synthetic-{uuid.uuid4()}"
    extra = {"code_revision": code_revision, "task_schema_version": TASK_SCHEMA_VERSION,
             "protocol_ref": "none (synthetic plumbing run)", "task_ids": task_ids,
             "repeats": repeats, "total_trials": repeats * len(task_ids)}
    label_text = f"SYNTHETIC {model} / {exec_mode}"
    store.create_experiment(safe_experiment({
        "experiment_id": experiment_id, "kind": "synthetic", "label": label_text, "model": model,
        "provider": "local-stub", "framework": "minilab", "strategy": "normal", "exec_mode": exec_mode,
        "temperature": 0.0, "seed": 0, "repeats": repeats, "task_ids": task_ids,
        "total_trials": repeats * len(task_ids), **extra}), status="queued")

    sink = _PacedRecorder(store, kind="synthetic", label_text=label_text, experiment_id=experiment_id,
                          extra=extra, delay_s=delay_s)

    def work():
        with tempfile.TemporaryDirectory(prefix="arl-synthetic-") as td:
            try:
                if fail_after is None:
                    run_experiment(task_ids, repeats, 0, model, 0.0, f"{td}/r.jsonl", f"{td}/t.jsonl",
                                   exec_mode=exec_mode, event_sink=sink)
                else:
                    _run_then_fail(sink, task_ids, repeats, model, exec_mode, td, fail_after, experiment_id)
            except Exception as e:  # noqa: BLE001 - recorded as a failed run
                exp = store.get_experiment(experiment_id)
                if exp and exp["status"] not in ("completed", "failed"):
                    sink.experiment_failed(experiment_id, e)

    thread = threading.Thread(target=work, name=f"arl-{experiment_id}", daemon=True)
    thread.start()
    return experiment_id, thread


class _InjectedSyntheticFailure(RuntimeError):
    pass


def _run_then_fail(sink, task_ids, repeats, model, exec_mode, td, fail_after, experiment_id):
    """Run the first `fail_after` trials for real, then fail the run. The
    runner swallows sink exceptions by design, so the failure is raised here
    (run level), which is where a real crash would surface."""
    flat = [t for _ in range(repeats) for t in task_ids][:fail_after]
    if flat:
        run_experiment(flat, 1, 0, model, 0.0, f"{td}/r.jsonl", f"{td}/t.jsonl",
                       exec_mode=exec_mode, event_sink=_NoFinish(sink))
    raise _InjectedSyntheticFailure("synthetic failure requested by the test")


class _NoFinish:
    """Forwards everything except experiment_finished (the run is about to fail)."""

    def __init__(self, sink):
        self._sink = sink

    def __getattr__(self, name):
        if name == "experiment_finished":
            return lambda *a, **k: None
        return getattr(self._sink, name)
