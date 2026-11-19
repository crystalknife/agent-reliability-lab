"""Experiment runner. Controls and records config; claims no LLM determinism.

temperature=0 does NOT guarantee deterministic LLM behavior. The runner
records the full config per run so results are auditable, not assumed
reproducible.
"""

import argparse
import json
import random
import sys
import time
import uuid
import warnings
from pathlib import Path

from .agent import run_agent
from .env import MiniBankEnv
from .evaluation import evaluate
from .failures import classify, classify_trial, fault_origin, provider_error_text
from .tasks import TASKS, get_task
from .tools import TOOL_NAMES
from .trajectory import Trajectory

RESULTS_FIELDS = (
    "run_id", "human_run_id", "experiment_id", "timestamp", "seed", "model", "provider",
    "temperature", "max_steps", "task_id", "difficulty", "trial_status", "passed", "failure",
    "termination_reason", "reasons", "provider_error",
)

# Oracle scripted tool sequences + correct finals. Validates harness
# plumbing only; not a capability claim.
_ORACLE = {
    "waiver-c101": (
        [
            ("get_customer", {"customer_id": "C101"}),
            ("get_account", {"customer_id": "C101"}),
            ("get_transactions", {"account_id": "A101"}),
            ("get_transactions", {"account_id": "A102"}),
            ("search_policy", {"query": "waiver"}),
            ("calculate", {"op": "avg", "values": [1800, 1200]}),
        ],
        "C101 qualifies for the fee waiver under WAIVER-01 RULE-A. "
        "Average balance (1800+1200)/2 = 1500, which meets the >= 1500 threshold.",
    ),
    "waiver-c102": (
        [
            ("get_customer", {"customer_id": "C102"}),
            ("get_account", {"customer_id": "C102"}),
            ("get_transactions", {"account_id": "A103", "last_n_days": 30}),
            ("search_policy", {"query": "waiver"}),
            ("calculate", {"op": "sum", "values": [600]}),
        ],
        "C102 qualifies for the fee waiver under WAIVER-01 RULE-B. "
        "Total direct deposits in the last 30 days = 600, which meets the >= 500 threshold.",
    ),
    "waiver-c103": (
        [
            ("get_customer", {"customer_id": "C103"}),
            ("get_account", {"customer_id": "C103"}),
            ("get_transactions", {"account_id": "A104", "last_n_days": 30}),
            ("search_policy", {"query": "waiver"}),
            ("calculate", {"op": "avg", "values": [300]}),
        ],
        "C103 does not qualify for the fee waiver under WAIVER-01. "
        "Average balance = 300 (< 1500) and direct deposits in the last 30 days = 100 (< 500).",
    ),
    "retrieval-bal-a103": (
        [
            ("get_account", {"customer_id": "C102"}),
        ],
        "C102's checking account A103 has balance 800.",
    ),
    "calc-total-c101": (
        [
            ("get_account", {"customer_id": "C101"}),
            ("calculate", {"op": "sum", "values": [1800, 1200]}),
        ],
        "Combined balance across C101's accounts is 3000.",
    ),
    "policy-attr-c102": (
        [
            ("get_account", {"customer_id": "C102"}),
            ("get_transactions", {"account_id": "A103", "last_n_days": 30}),
            ("search_policy", {"query": "waiver"}),
        ],
        "C102 qualifies under WAIVER-01 RULE-B with 30-day deposits of 600. "
        "RULE-A is not satisfied: average balance is 800, below 1500.",
    ),
    "temporal-deposit-a103": (
        [
            ("get_transactions", {"account_id": "A103"}),
            ("calculate", {"op": "sum", "values": [600]}),
        ],
        "Total 30-day direct deposits for A103: 600.",
    ),
    "horizon-checking-combined": (
        [
            ("get_customer", {"customer_id": "C101"}),
            ("get_customer", {"customer_id": "C102"}),
            ("get_account", {"customer_id": "C101"}),
            ("get_account", {"customer_id": "C102"}),
            ("get_transactions", {"account_id": "A101", "last_n_days": 30}),
            ("get_transactions", {"account_id": "A103", "last_n_days": 30}),
            ("calculate", {"op": "add", "values": [1800, 800]}),
        ],
        "Combined checking balance for C101 and C102 is 2600 (A101 1800 + A103 800).",
    ),
    "deposit-total-combined": (
        [
            ("get_transactions", {"account_id": "A101", "last_n_days": 30}),
            ("get_transactions", {"account_id": "A103", "last_n_days": 30}),
            ("calculate", {"op": "sum", "values": [400, 600]}),
        ],
        "Total 30-day direct deposits for A101 and A103: 1000.",
    ),
    "retrieval-multi-checking": (
        [
            ("get_account", {"customer_id": "C101"}),
            ("get_account", {"customer_id": "C102"}),
            ("get_account", {"customer_id": "C103"}),
        ],
        "Checking balances: A101 1800, A103 800, A104 300.",
    ),
}


def make_model(name, task_id=None):
    if name == "stub":
        def stub_fn(messages, tools, config):
            return {"final": "The customer qualifies."}
        return stub_fn
    if name == "scripted":
        calls, final = _ORACLE[task_id]
        state = {"i": 0}

        def scripted_fn(messages, tools, config):
            if state["i"] < len(calls):
                tool, args = calls[state["i"]]
                state["i"] += 1
                return {"tool": tool, "arguments": args}
            return {"final": final}

        return scripted_fn
    # Gemini adapter
    if name == "gemini" or name.startswith("gemini:"):
        try:
            from .models.gemini import make_gemini_model, DEFAULT_MODEL as GEMINI_DEFAULT_MODEL
        except ImportError as e:
            raise ValueError(
                "The gemini adapter requires the google-genai package. "
                "Install it with `pip install google-genai`."
            ) from e
        if name == "gemini":
            return make_gemini_model(GEMINI_DEFAULT_MODEL)
        return make_gemini_model(name.split(":", 1)[1])
    # Groq adapter
    if name == "groq" or name.startswith("groq:"):
        try:
            from .models.groq import make_groq_model, DEFAULT_MODEL as GROQ_DEFAULT_MODEL
        except ImportError as e:
            raise ValueError(
                "The groq adapter requires the openai package. Install it with `pip install openai`."
            ) from e
        if name == "groq":
            return make_groq_model(GROQ_DEFAULT_MODEL)
        return make_groq_model(name.split(":", 1)[1])
    # OpenRouter adapter
    if name == "openrouter" or name.startswith("openrouter:"):
        try:
            from .models.openrouter import make_openrouter_model, DEFAULT_MODEL as OPENROUTER_DEFAULT_MODEL
        except ImportError as e:
            raise ValueError(
                "The openrouter adapter requires the openai package. Install it with `pip install openai`."
            ) from e
        if name == "openrouter":
            return make_openrouter_model(OPENROUTER_DEFAULT_MODEL)
        else:
            # name is "openrouter:model-id"
            model_id = name.split(":", 1)[1]
            return make_openrouter_model(model_id)
    # OmniRoute adapter (local OpenAI-compatible server)
    if name == "omniroute" or name.startswith("omniroute:"):
        try:
            from .models.omniroute import make_omniroute_model, DEFAULT_MODEL as OMNIROUTE_DEFAULT_MODEL
        except ImportError as e:
            raise ValueError(
                "The omniroute adapter requires the openai package. Install it with `pip install openai`."
            ) from e
        if name == "omniroute":
            return make_omniroute_model(OMNIROUTE_DEFAULT_MODEL)
        return make_omniroute_model(name.split(":", 1)[1])
    # Local llama-server adapter (external OpenAI-compatible server)
    if name == "local" or name.startswith("local:"):
        try:
            from .models.local import make_local_model, DEFAULT_MODEL as LOCAL_DEFAULT_MODEL
        except ImportError as e:
            raise ValueError(
                "The local adapter requires the openai package. Install it with `pip install openai`."
            ) from e
        if name == "local":
            return make_local_model(LOCAL_DEFAULT_MODEL)
        return make_local_model(name.split(":", 1)[1])
    # Treat any other name as a request for an OpenAI model
    try:
        from .models.openai import make_openai_model
    except ImportError as e:
        raise ValueError(
            "The openai adapter requires the openai package. Install it with `pip install openai`."
        ) from e
    return make_openai_model(name)



def _provenance_counts(traj):
    """Model-request and tool-call counts for one trajectory.

    model_requests counts returned model responses (assistant messages in
    the harness history, AIMessages in a LangChain history); requests that
    raised before producing a response are not counted. tool_calls counts
    recorded steps that dispatch a real tool (pseudo-steps such as
    model_error/invalid_action are excluded).
    """
    history = traj.raw_history or []
    if history and isinstance(history[0], dict) and "role" in history[0]:
        model_requests = sum(1 for m in history if m.get("role") == "assistant")
    else:
        model_requests = sum(1 for m in history if m.get("type") == "AIMessage")
    steps = traj.steps or []
    tool_calls = sum(1 for s in steps
                     if (s if isinstance(s, dict) else {"action": s.action}).get("action") in TOOL_NAMES)
    return model_requests, tool_calls


class _SinkGuard:
    """Isolates an optional event sink from the experiment.

    Guarantees: (1) a missing hook is skipped; (2) a hook that raises never
    propagates into the run, not even when warnings are configured as
    errors; (3) every such failure is counted, so the sink can be told at
    the end that its record of this run is incomplete (hook
    `observability_report`). The JSONL result and trajectory files are
    written by the runner itself and do not depend on any of this.
    """

    def __init__(self, sink):
        self.sink = sink
        self.failures = []          # [{"hook": name, "error_type": ExceptionClassName}]

    def call(self, method, *args):
        if self.sink is None:
            return True
        hook = getattr(self.sink, method, None)
        if hook is None:
            return True
        try:
            hook(*args)
            return True
        except Exception as e:  # noqa: BLE001 - a sink fault is not an experiment fault
            self.failures.append({"hook": method, "error_type": type(e).__name__})
            try:
                warnings.warn(f"event sink {method} failed: {type(e).__name__}",
                              RuntimeWarning, stacklevel=3)
            except Exception:  # noqa: BLE001 - e.g. -W error turns the warning into a raise
                pass
            return False

    def report(self, experiment_id):
        """Tell the sink which of its hooks failed (best effort, guarded)."""
        if self.sink is None:
            return
        summary = {"hook_failures": list(self.failures), "source": "runner"}
        try:
            self.sink.runner_sink_failures = list(self.failures)
        except Exception:  # noqa: BLE001
            pass
        hook = getattr(self.sink, "observability_report", None)
        if hook is None:
            return
        try:
            hook(experiment_id, summary)
        except Exception:  # noqa: BLE001 - nothing more can be done from here
            pass

    def all_failures(self):
        """Hook failures seen here plus write failures the sink reports about
        itself (a recorder that catches its own errors lists them in
        `failure_log`). Read defensively: the sink may be broken."""
        own = []
        try:
            log = getattr(self.sink, "failure_log", None)
            if isinstance(log, list):
                own = [dict(f, source="sink") for f in log if isinstance(f, dict)]
        except Exception:  # noqa: BLE001
            own = []
        return [dict(f, source="runner") for f in self.failures] + own


def _notify(sink, method, *args):
    """Call one sink hook, isolated (see _SinkGuard). `sink` may be a
    _SinkGuard (failures are then accounted for) or a bare sink/None."""
    guard = sink if isinstance(sink, _SinkGuard) else _SinkGuard(sink)
    return guard.call(method, *args)


def _provider_of(model):
    """Provider label for a model selector (same mapping the trial loop uses)."""
    if model in ("stub", "scripted"):
        return "local-stub"
    for prefix in ("gemini", "groq", "openrouter", "omniroute", "local"):
        if model == prefix or model.startswith(prefix + ":"):
            return prefix
    return "openai"


def _build_manifest(task_ids, repeats, seed, model, temperature, framework, strategy,
                    exec_mode, data_path, protocol_ref, experiment_id=None):
    """Run-start provenance snapshot; a failure here is recorded as an
    unavailable manifest and never stops or changes the run."""
    from . import run_manifest
    try:
        from .tasks import TASK_SCHEMA_VERSION
        provider = _provider_of(model)
        model_version = None
        if provider == "local":
            try:
                from .models.local import MODEL_VERSION as model_version
            except Exception:  # noqa: BLE001 - optional SDK missing
                model_version = None
        config = {"model": model, "framework": framework, "strategy": strategy,
                  "exec_mode": exec_mode if framework == "minilab" else "native",
                  "temperature": temperature, "seed": seed, "repeats": repeats,
                  "task_ids": list(task_ids)}
        return run_manifest.build_run_manifest(
            config=config, tasks=[get_task(t) for t in task_ids],
            task_schema_version=TASK_SCHEMA_VERSION, provider=provider,
            model_version=model_version, protocol_ref=protocol_ref, fixture_path=data_path,
            experiment_id=experiment_id)
    except Exception as e:  # noqa: BLE001
        return run_manifest.manifest_unavailable(e, experiment_id)


def _persist_manifest(manifest, manifest_path):
    """Write the manifest file. Optional output: a failure here must not stop
    or change the run, and must not be silent. Returns the persistence
    status; a failure is also raised as a RuntimeWarning (guarded, so that
    warnings-as-errors cannot abort the run either)."""
    from .run_manifest import write_manifest
    try:
        write_manifest(manifest, manifest_path)
        return {"status": "written"}
    except Exception as e:  # noqa: BLE001 - reported, not propagated
        try:
            warnings.warn(f"run manifest was NOT written: {type(e).__name__}", RuntimeWarning, stacklevel=3)
        except Exception:  # noqa: BLE001
            pass
        return {"status": "failed", "error_type": type(e).__name__}


def run_experiment(task_ids=None, repeats=1, seed=0, model="stub",
                   temperature=0.0, results_path="results/results.jsonl",
                   trajectories_path="results/trajectories.jsonl", data_path=None,
                   framework="minilab", strategy="normal", exec_mode="sequential",
                   event_sink=None, protocol_ref=None, manifest_path=None, run_info=None):
    """Run trials and append result rows and trajectories to JSONL files.

    event_sink (optional): an object with any of the hooks
    experiment_started(info), trial_started(info), trial_finished(row,
    trajectory), experiment_finished(experiment_id, n_trials),
    experiment_failed(experiment_id, exc), observability_report(
    experiment_id, summary). Hooks only observe; rows, trajectories and
    return value are identical with or without a sink, and a hook that
    raises is counted and reported to the sink, never propagated.

    protocol_ref (optional): a protocol revision label supplied by the
    caller; recorded in the run manifest as given, never inferred.
    manifest_path (optional): write the run-start provenance manifest (see
    run_manifest.py) to this NEW file. A manifest is built only when a sink
    or a manifest_path is given; it never affects rows or scoring. If the
    file cannot be written the run still proceeds; the failure is warned
    about, passed to the sink and recorded in run_info.
    run_info (optional): a dict the caller supplies; it is filled with
    experiment_id, manifest_sha256 (None unless a manifest was built),
    manifest_status ("available"/"unavailable"/"not_built"),
    manifest_persistence ({"status": "written"|"failed"|"not_requested",
    "error_type"}) and, at the end, sink_failures.
    """
    if framework not in ("minilab", "langchain"):
        raise ValueError(f"unknown framework: {framework!r} (use 'minilab' or 'langchain')")
    if framework == "minilab" and strategy != "normal":
        raise ValueError(
            f"strategy {strategy!r} applies only to --framework langchain.")
    if exec_mode not in ("sequential", "batch"):
        raise ValueError(
            f"unknown exec_mode: {exec_mode!r} (use 'sequential' or 'batch').")
    if framework != "minilab" and exec_mode != "sequential":
        raise ValueError(
            f"--exec {exec_mode!r} applies only to --framework minilab.")
    langchain_meta = None
    if framework == "langchain":
        from .frameworks.langchain import (
            FRAMEWORK, STRATEGIES, STRATEGY, framework_version, make_langchain_chat,
            run_langchain_agent, ScriptedChatModel,
        )
        if strategy not in STRATEGIES:
            raise ValueError(
                f"unknown strategy: {strategy!r} (use one of {STRATEGIES})")
        langchain_meta = {
            "framework": FRAMEWORK,
            "framework_version": framework_version(),
            "agent_strategy": STRATEGY,
            "strategy": strategy,
        }
    provider = "local-stub"
    task_ids = task_ids or [t["task_id"] for t in TASKS]
    results_path = Path(results_path)
    trajectories_path = Path(trajectories_path)
    results_path.parent.mkdir(parents=True, exist_ok=True)
    trajectories_path.parent.mkdir(parents=True, exist_ok=True)
    experiment_id = str(uuid.uuid4())
    rows = []
    manifest = None
    persistence = {"status": "not_requested"}
    if event_sink is not None or manifest_path is not None:
        manifest = _build_manifest(task_ids, repeats, seed, model, temperature, framework,
                                   strategy, exec_mode, data_path, protocol_ref, experiment_id)
        if manifest_path is not None:
            persistence = _persist_manifest(manifest, manifest_path)
    if run_info is not None:
        run_info.update({
            "experiment_id": experiment_id,
            # links rows (which carry experiment_id) to the manifest file's content
            "manifest_sha256": (manifest or {}).get("manifest_sha256"),
            "manifest_status": ("not_built" if manifest is None else
                                "unavailable" if manifest.get("status") == "unavailable" else "available"),
            "manifest_persistence": dict(persistence),
        })
    event_sink = _SinkGuard(event_sink)
    _notify(event_sink, "experiment_started", {
        "experiment_id": experiment_id, "model": model, "framework": framework,
        "strategy": strategy, "exec_mode": exec_mode if framework == "minilab" else "native",
        "temperature": temperature, "seed": seed, "repeats": repeats,
        "task_ids": list(task_ids), "total_trials": repeats * len(task_ids),
        "provider": _provider_of(model), "manifest": manifest,
        "manifest_persistence": dict(persistence),
    })
    try:
        _run_trials(rows, experiment_id, task_ids, repeats, seed, model, temperature,
                    results_path, trajectories_path, data_path, framework, strategy,
                    exec_mode, langchain_meta, event_sink)
    except BaseException as e:
        _notify(event_sink, "experiment_failed", experiment_id, e)
        event_sink.report(experiment_id)
        if run_info is not None:
            run_info["sink_failures"] = event_sink.all_failures()
        raise
    _notify(event_sink, "experiment_finished", experiment_id, len(rows))
    event_sink.report(experiment_id)
    if run_info is not None:
        run_info["sink_failures"] = event_sink.all_failures()
    return rows


def _run_trials(rows, experiment_id, task_ids, repeats, seed, model, temperature,
                results_path, trajectories_path, data_path, framework, strategy,
                exec_mode, langchain_meta, event_sink):
    if framework == "langchain":
        from .frameworks.langchain import (
            make_langchain_chat, run_langchain_agent, ScriptedChatModel,
        )
    provider = "local-stub"
    with open(results_path, "a", encoding="utf-8") as rf, \
         open(trajectories_path, "a", encoding="utf-8") as tf:
        for rep in range(repeats):
            seed_used = seed + rep
            random.seed(seed_used)
            for task_id in task_ids:
                task = get_task(task_id)
                # Generate globally unique run_id (UUID4)
                # Globally unique across experiments; the old format
                # (task-s{seed}-r{rep}) collided whenever the same
                # task/seed/repeat was re-run in a new experiment.
                run_id = str(uuid.uuid4())
                human_run_id = f"{task_id}-s{seed_used}-r{rep}"
                # Preserve human-readable identity fields
                human_run_id = f"{task_id}-s{seed_used}-r{rep}"
                timestamp = time.time()
                _notify(event_sink, "trial_started", {
                    "experiment_id": experiment_id, "run_id": run_id, "task_id": task_id,
                    "repeat": rep, "seed": seed_used,
                })
                env = MiniBankEnv(data_path)
                model_version = None
                if model in ("stub", "scripted"):
                    provider = "local-stub"
                elif model == "gemini" or model.startswith("gemini:"):
                    provider = "gemini"
                elif model == "groq" or model.startswith("groq:"):
                    provider = "groq"
                elif model == "openrouter" or model.startswith("openrouter:"):
                    provider = "openrouter"
                elif model == "omniroute" or model.startswith("omniroute:"):
                    provider = "omniroute"
                elif model == "local" or model.startswith("local:"):
                    provider = "local"
                    from .models.local import MODEL_VERSION as model_version
                else:
                    provider = "openai"
                meta = {
                    "experiment_id": experiment_id,
                    "run_id": run_id,
                    "human_run_id": human_run_id,
                    "timestamp": timestamp,
                    "model": model,
                    "provider": provider,
                    "model_version": model_version,
                    "temperature": temperature,
                    "seed": seed_used,
                }
                if framework == "langchain":
                    if model == "scripted":
                        calls, final = _ORACLE[task_id]
                        chat_model = ScriptedChatModel(calls, final).model
                    elif model == "local" or model.startswith("local:"):
                        chat_model = make_langchain_chat(
                            model.split(":", 1)[1] if ":" in model else "qwen3-4b",
                            temperature=temperature)
                    else:
                        raise ValueError(
                            f"framework 'langchain' supports only 'scripted' and "
                            f"'local:*' models, got {model!r}.")
                    traj = run_langchain_agent(
                        task_id, task["prompt"], env, chat_model,
                        max_steps=task["max_steps"], meta=meta, strategy=strategy)
                else:
                    traj = run_agent(task_id, task["prompt"], env,
                                     make_model(model, task_id), max_steps=task["max_steps"], meta=meta,
                                     exec_mode=exec_mode)
                # The evaluator is deterministic and reads only canonical fields,
                # but a fault here is still a trial-level condition, not agent behavior.
                evaluator_exception = None
                try:
                    verdict = evaluate(task, traj)
                    evaluator_raised = False
                except Exception as e:  # noqa: BLE001 - evaluator faults are data, not crashes
                    evaluator_exception = e
                    verdict = {"passed": False, "reasons": ["evaluator raised"],
                               "verdict_match": False, "evidence_match": False,
                               "grounding_ok": False, "support_ok": False}
                    evaluator_raised = True
                trial_status = classify_trial(traj, evaluator_raised=evaluator_raised)
                failure = classify(task, traj, verdict)
                model_requests, tool_calls = _provenance_counts(traj)
                origin = fault_origin(traj, evaluator_raised=evaluator_raised,
                                      evaluator_exception=evaluator_exception)
                row = {
                    "run_id": run_id,
                    "human_run_id": human_run_id,
                    "experiment_id": experiment_id,
                    "timestamp": timestamp,
                    "seed": seed_used,
                    "model": model,
                    "provider": provider,
                    "temperature": temperature,
                    "max_steps": task["max_steps"],
                    "task_id": task_id,
                    "difficulty": task["difficulty"],
                    "trial_status": trial_status,
                    "passed": verdict["passed"],
                    "failure": failure,
                    "termination_reason": traj.termination_reason,
                    "reasons": verdict["reasons"],
                    "model_requests": model_requests,
                    "tool_calls": tool_calls,
                    # Non-scoring annotation (failures.fault_origin): where a fault
                    # was caught and how firmly its origin is established.
                    "fault_origin": origin["origin"],
                    "fault_attribution": origin["attribution"],
                    "fault_basis": origin["basis"],
                    "fault_boundary": origin["boundary"],
                    "fault_exception_type": origin["exception_type"],
                }
                # Framework provenance: explicit on every row so a LangChain
                # trial can never be confused with a custom-harness trial.
                # Trajectory files stay canonical (model/provider/temperature/
                # seed identical by design); framework identity lives here.
                if langchain_meta is not None:
                    row.update(langchain_meta)
                    # LangChain executes whole AIMessage batches natively
                    # (parallel ToolNode); there is no sequential/batch switch.
                    row["exec_mode"] = "native"
                else:
                    row.update({
                        "framework": "minilab",
                        "framework_version": None,
                        "agent_strategy": "minilab.agent.run_agent",
                        "strategy": "normal",
                    })
                    row["exec_mode"] = exec_mode
                if trial_status == "provider_error":
                    # Raw provider text, preserved verbatim for debugging.
                    row["provider_error"] = provider_error_text(traj)
                rf.write(json.dumps(row) + "\n")
                rec = traj.to_dict()
                # The trajectory already includes the provenance fields from the meta.
                tf.write(json.dumps(rec, default=str) + "\n")
                rows.append(row)
                _notify(event_sink, "trial_finished", row, traj)


def _make_live_sink(live_db):
    """(sink, None), (None, None) when not requested, or (None, error type
    name) when the optional live database cannot be opened. Never raises:
    an unavailable dashboard must not prevent a run."""
    if not live_db:
        return None, None
    try:
        from .live.recorder import LiveRecorder
        from .live.server import code_revision
        from .live.store import RunStore
        from .tasks import TASK_SCHEMA_VERSION
        return LiveRecorder(RunStore(live_db), kind="run", extra={
            "code_revision": code_revision(), "task_schema_version": TASK_SCHEMA_VERSION}), None
    except Exception as e:  # noqa: BLE001
        return None, type(e).__name__


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tasks", default="all")
    ap.add_argument("--strategy", default="normal",
                    choices=("normal", "chatter_stripped", "schema_parity",
                             "schema_parity_chatter_stripped"),
                    help="LangChain isolation control (only with --framework "
                         "langchain): run the normal EXP-002 arm or exactly "
                         "one confounder control.")
    ap.add_argument("--framework", default="minilab", choices=("minilab", "langchain"),
                    help="Agent orchestration: the custom MiniLab harness or a "
                         "LangChain tool-calling agent over the same tasks, "
                         "environment, tools, and evaluator.")
    ap.add_argument("--exec", default="sequential", choices=("sequential", "batch"),
                    help="MiniLab batch handling: execute only the first emitted "
                         "tool call per turn, or every emitted call in listed "
                         "order. Only with --framework minilab; one model-call "
                         "budget unit is consumed per turn either way.")
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument(
        "--model",
        default="stub",
        help="Model selector. 'stub' or 'scripted' for built-in local models; "
             "'gemini' (default gemini-2.5-flash) or 'gemini:<model-id>' for Google Gemini "
             "[GEMINI_API_KEY]; "
             "'groq' (default groq model) or 'groq:<model-id>' for Groq [GROQ_API_KEY]; "
             "'openrouter' (default model) or 'openrouter:<model-id>' for OpenRouter "
             "[OPENROUTER_API_KEY]; "
             "'omniroute' (default model) or 'omniroute:<model-id>' for a local OmniRoute "
             "server at http://127.0.0.1:20128/v1 [OMNIROUTE_API_KEY, optional]; "
             "'local' (qwen3-4b) or 'local:<model-id>' for an external local "
             "llama-server at http://127.0.0.1:8081/v1 [LOCAL_API_KEY, optional]; "
             "any other string is treated as an OpenAI model name "
             "[OPENAI_API_KEY].",
    )
    ap.add_argument("--temperature", type=float, default=0.0)
    ap.add_argument("--results", default="results/results.jsonl")
    ap.add_argument("--trajectories", default="results/trajectories.jsonl")
    ap.add_argument("--live-db", default=None,
                    help="Optional SQLite file. When given, sanitized progress "
                         "(no prompts, arguments, observations or answers) is "
                         "recorded there for the local live dashboard "
                         "(python -m minilab.live.server --db <same file>). "
                         "Result and trajectory files are unaffected. Must be a plain "
                         "file path: ':memory:' and SQLite URI names starting with "
                         "'file:' (including file-backed ones) are refused.")
    ap.add_argument("--protocol-ref", default=None,
                    help="Protocol revision label to record in the run manifest, "
                         "exactly as given (e.g. EXP-005-protocol@3.1#<sha8>). "
                         "Nothing is inferred when omitted.")
    ap.add_argument("--manifest-out", default=None,
                    help="Write the run-start provenance manifest (commit, clean/"
                         "dirty tree, source/task/config hashes) to this new file.")
    a = ap.parse_args()
    event_sink, live_error = _make_live_sink(a.live_db)
    if live_error:
        print(f"LIVE RECORDING DISABLED: the live database could not be opened ({live_error}). "
              "The run continues; results and trajectories are unaffected; nothing will "
              "appear in the dashboard for this run.", file=sys.stderr)
    run_info = {}
    task_ids = [t["task_id"] for t in TASKS] if a.tasks == "all" else a.tasks.split(",")
    rows = run_experiment(task_ids, a.repeats, a.seed, a.model,
                          a.temperature, a.results, a.trajectories,
                          framework=a.framework, strategy=a.strategy,
                          exec_mode=a.exec, event_sink=event_sink,
                          protocol_ref=a.protocol_ref, manifest_path=a.manifest_out,
                          run_info=run_info)
    persistence = run_info.get("manifest_persistence", {})
    if persistence.get("status") == "failed":
        print(f"MANIFEST NOT WRITTEN ({persistence.get('error_type')}): {a.manifest_out!r} was not "
              "created. The run completed and its results are valid, but there is no manifest "
              "file for it.", file=sys.stderr)
    elif persistence.get("status") == "written" and run_info.get("manifest_status") == "unavailable":
        print(f"MANIFEST UNAVAILABLE: {a.manifest_out} was written but records only that the provenance "
              f"snapshot could not be built (experiment_id {run_info['experiment_id']}; the reason is in "
              "the file). The run completed and its results are valid, but their provenance is not "
              "established by this manifest.", file=sys.stderr)
    elif persistence.get("status") == "written":
        print(f"wrote run manifest to {a.manifest_out} (experiment_id {run_info['experiment_id']}, "
              f"manifest_sha256 {run_info.get('manifest_sha256')})")
    if run_info.get("sink_failures"):
        print(f"LIVE RECORDING INCOMPLETE: {len(run_info['sink_failures'])} dashboard write(s) failed. "
              "The JSONL files are complete and authoritative.", file=sys.stderr)
    print(f"wrote {len(rows)} runs to {a.results}")


if __name__ == "__main__":
    main()
