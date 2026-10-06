"""Experiment runner. Controls and records config; claims no LLM determinism.

temperature=0 does NOT guarantee deterministic LLM behavior. The runner
records the full config per run so results are auditable, not assumed
reproducible.
"""

import argparse
import json
import random
import time
import uuid
from pathlib import Path

from .agent import run_agent
from .env import MiniBankEnv
from .evaluation import evaluate
from .failures import classify, classify_trial, provider_error_text
from .tasks import TASKS, get_task
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



def run_experiment(task_ids=None, repeats=1, seed=0, model="stub",
                   temperature=0.0, results_path="results/results.jsonl",
                   trajectories_path="results/trajectories.jsonl", data_path=None):
    provider = "local-stub"
    task_ids = task_ids or [t["task_id"] for t in TASKS]
    results_path = Path(results_path)
    trajectories_path = Path(trajectories_path)
    results_path.parent.mkdir(parents=True, exist_ok=True)
    trajectories_path.parent.mkdir(parents=True, exist_ok=True)
    experiment_id = str(uuid.uuid4())
    rows = []
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
                traj = run_agent(task_id, task["prompt"], env,
                                 make_model(model, task_id), max_steps=task["max_steps"], meta=meta)
                # The evaluator is deterministic and reads only canonical fields,
                # but a fault here is still a trial-level condition, not agent behavior.
                try:
                    verdict = evaluate(task, traj)
                    evaluator_raised = False
                except Exception:  # noqa: BLE001 - evaluator faults are data, not crashes
                    verdict = {"passed": False, "reasons": ["evaluator raised"],
                               "verdict_match": False, "evidence_match": False,
                               "grounding_ok": False, "support_ok": False}
                    evaluator_raised = True
                trial_status = classify_trial(traj, evaluator_raised=evaluator_raised)
                failure = classify(task, traj, verdict)
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
                }
                if trial_status == "provider_error":
                    # Raw provider text, preserved verbatim for debugging.
                    row["provider_error"] = provider_error_text(traj)
                rf.write(json.dumps(row) + "\n")
                rec = traj.to_dict()
                # The trajectory already includes the provenance fields from the meta.
                tf.write(json.dumps(rec, default=str) + "\n")
                rows.append(row)
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tasks", default="all")
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
    a = ap.parse_args()
    task_ids = [t["task_id"] for t in TASKS] if a.tasks == "all" else a.tasks.split(",")
    rows = run_experiment(task_ids, a.repeats, a.seed, a.model,
                          a.temperature, a.results, a.trajectories)
    print(f"wrote {len(rows)} runs to {a.results}")


if __name__ == "__main__":
    main()
