"""Minimal CLI for BankOps Meridian evaluation runs (pilot).

    python -m minilab.bankops.cli run --profile local --model-id qwen2.5:7b --dry-run
    python -m minilab.bankops.cli run --profile api --model-id <id> --credential-ref env:OPENROUTER_API_KEY \
        --approve-paid-run ...
    python -m minilab.bankops.cli parser-eval

Paid (api) runs require `--approve-paid-run`. Credentials are only `env:NAME` references; the value is
resolved by the adapter at request time and never printed or written. Each run gets its own directory
`<out>/<run_id>/`: manifest.json (provenance and plan), episodes.jsonl (one full graded result with its
raw trace per line, appended as each episode finishes), summary.json (aggregate, attribution, usage)
and review_queue.json (natural-language reports that need a human).
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import hashlib
import secrets
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import grading as g
from . import models as m
from . import runner as r

CLI_VERSION = "bankops-cli/0.2.0"
DEFAULT_OUT = Path("runs") / "bankops"

# Named fault scenarios (all on the first release of the episode; readiness §5.2).
FAULT_SCENARIOS: Dict[str, List[Dict[str, Any]]] = {
    "none": [],
    "request_lost": [{"tool": "release_payment", "occurrence": 1, "fault": "fail_before_commit"}],
    "response_lost": [{"tool": "release_payment", "occurrence": 1, "fault": "commit_response_lost"}],
    "late_commit_6": [{"tool": "release_payment", "occurrence": 1, "fault": "late_commit", "commit_delay_ticks": 6}],
    # The first release of an APPROVED order (a release the bank would refuse anyway never consumes it).
    "late_commit_6_approved": [{"tool": "release_payment", "occurrence": 1, "when": "order_approved",
                                "fault": "late_commit", "commit_delay_ticks": 6}],
}


def _ints(text: str) -> List[int]:
    return [int(x) for x in text.split(",") if x.strip()]


def _names(text: str) -> List[str]:
    return [x.strip() for x in text.split(",") if x.strip()]


def _floats(text: str) -> List[float]:
    return [float(x) for x in text.split(",") if x.strip()]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m minilab.bankops.cli", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run and grade Meridian episodes")
    run.add_argument("--profile", choices=("api", "local"), required=True)
    run.add_argument("--model-id", required=True, help="explicit provider model identifier (no aliases)")
    run.add_argument("--base-url", help="OpenAI-compatible root; defaults: OpenRouter (api), Ollama (local)")
    run.add_argument("--credential-ref", help="env:NAME holding the API key (api profile only)")
    run.add_argument("--timeout", type=float, default=60.0, help="seconds per model request")
    run.add_argument("--max-retries", type=int, default=0, help="model-call retries (429/5xx/timeout); default 0")
    run.add_argument("--temperature", type=float, default=0.0)
    run.add_argument("--temperatures", type=_floats,
                     help="one temperature per repeat index (e.g. 0,0.3 with --repeats 2); overrides --temperature")
    run.add_argument("--max-tokens", type=int, help="per-response output cap sent with every request (max_tokens)")
    run.add_argument("--sampling-seed", type=int, help="sampling seed sent with every request (seed)")
    run.add_argument("--suite", default="meridian_v0_pilot")
    run.add_argument("--tasks", default="all", help="comma-separated task ids, or 'all'")
    run.add_argument("--seeds", type=_ints, default=[1])
    run.add_argument("--repeats", type=int, default=1, help="episodes per (task, fault scenario, seed)")
    run.add_argument("--faults", type=_names, default=["none"], help=",".join(FAULT_SCENARIOS))
    run.add_argument("--max-agent-ticks", type=int, help="override every task's tick budget (recorded in its spec)")
    run.add_argument("--max-model-turns", type=int, help="default: task tick budget + 1")
    run.add_argument("--max-episodes", type=int, help="stop after this many episodes")
    run.add_argument("--max-total-tokens", type=int,
                     help="stop starting episodes once provider-reported tokens reach this (may overshoot by one)")
    run.add_argument("--price-prompt-per-mtok", type=float, help="USD per 1M prompt tokens, for the estimate")
    run.add_argument("--price-completion-per-mtok", type=float, help="USD per 1M completion tokens")
    run.add_argument("--out", type=Path, default=DEFAULT_OUT)
    run.add_argument("--dry-run", action="store_true", help="validate and print the plan; send no requests")
    run.add_argument("--approve-paid-run", action="store_true", help="required for --profile api")

    pe = sub.add_parser("parser-eval", help="evaluate the natural-language report parser on its labelled corpus")
    pe.add_argument("--parser-version", default=g.PARSER_VERSION, choices=g.PARSER_VERSIONS)
    return p


def _profile(a) -> m.ModelProfile:
    common = {"timeout_s": a.timeout, "max_retries": a.max_retries}
    if a.base_url:
        common["base_url"] = a.base_url
    if a.profile == "api":
        if a.credential_ref:
            common["credential_ref"] = a.credential_ref
        return m.api_profile(a.model_id, **common)
    if a.credential_ref:
        common["credential_ref"] = a.credential_ref
    return m.local_profile(a.model_id, **common)


def _plan(a) -> Dict[str, Any]:
    suite = g.load_suite(a.suite)
    tasks = list(suite["tasks"].values())
    if a.tasks != "all":
        wanted = _names(a.tasks)
        unknown = [t for t in wanted if t not in suite["tasks"]]
        if unknown:
            raise SystemExit(f"unknown task id(s): {', '.join(unknown)}")
        tasks = [suite["tasks"][t] for t in wanted]
    if a.max_agent_ticks:
        tasks = [dataclasses.replace(t, max_agent_ticks=a.max_agent_ticks) for t in tasks]
    unknown = [f for f in a.faults if f not in FAULT_SCENARIOS]
    if unknown:
        raise SystemExit(f"unknown fault scenario(s): {', '.join(unknown)}; choose from {', '.join(FAULT_SCENARIOS)}")
    if a.repeats < 1 or not a.seeds:
        raise SystemExit("--repeats must be >= 1 and --seeds non-empty")
    temps = a.temperatures if a.temperatures else [a.temperature] * a.repeats
    if len(temps) != a.repeats:
        raise SystemExit("--temperatures needs exactly one value per repeat (--repeats)")
    if a.max_tokens is not None and a.max_tokens < 1:
        raise SystemExit("--max-tokens must be a positive integer")
    episodes = [(t, f, s, k, temps[k]) for t in tasks for f in a.faults for s in a.seeds for k in range(a.repeats)]
    if a.max_episodes:
        episodes = episodes[:a.max_episodes]
    return {"suite": suite, "tasks": tasks, "episodes": episodes}


def _cost(usage: Dict[str, int], a) -> Optional[float]:
    if a.price_prompt_per_mtok is None or a.price_completion_per_mtok is None:
        return None
    return round(usage["prompt_tokens"] / 1e6 * a.price_prompt_per_mtok
                 + usage["completion_tokens"] / 1e6 * a.price_completion_per_mtok, 6)


def summarize(results: List[Dict[str, Any]], a, stop_reason: str, planned: int) -> Dict[str, Any]:
    usage = {k: sum(x["execution"]["usage"][k] for x in results)
             for k in ("model_calls", "prompt_tokens", "completion_tokens", "turns_with_usage", "turns_without_usage")}
    usage["source"] = "provider_reported_only"
    count = lambda key: {v: sum(1 for x in results if x["execution"][key] == v)  # noqa: E731
                         for v in sorted({x["execution"][key] for x in results})}
    return {
        "stop_reason": stop_reason, "episodes_planned": planned, "episodes_run": len(results),
        "attribution": count("attribution"), "termination": count("termination"),
        "needs_human_review": sum(1 for x in results if x["report"]["needs_human_review"]),
        "usage": usage, "estimated_cost_usd": _cost(usage, a),
        "cost_note": "estimate from provider-reported tokens and the prices given on the command line; "
                     "turns without reported usage are not counted",
        "aggregate": g.aggregate(results),
    }


def capture_server_props(base_url: str, timeout: float = 5.0) -> Dict[str, Any]:
    """llama.cpp llama-server's GET /props (local profiles only): build, model file, quantisation, context,
    slots, chat-template hash and default sampler settings. Verified against b11435; on any other server
    the capture is recorded as unavailable, never guessed."""
    root = base_url.rstrip("/")
    root = root[:-3] if root.endswith("/v1") else root
    try:
        with urllib.request.urlopen(root + "/props", timeout=timeout) as resp:
            props = json.loads(resp.read())
        if not isinstance(props, dict):
            raise ValueError("props is not an object")
    except (OSError, ValueError) as e:
        return {"available": False, "error": type(e).__name__}
    gen = props.get("default_generation_settings") or {}
    params = gen.get("params") or {}
    template = props.get("chat_template")
    return {
        "available": True, "endpoint": "/props",
        "build_info": props.get("build_info"), "model_path": props.get("model_path"),
        "model_ftype": props.get("model_ftype"), "total_slots": props.get("total_slots"), "n_ctx": gen.get("n_ctx"),
        "chat_template_sha256": hashlib.sha256(template.encode()).hexdigest() if isinstance(template, str) else None,
        "chat_template_caps": props.get("chat_template_caps"),
        "default_sampling": {k: params.get(k) for k in ("temperature", "top_k", "top_p", "min_p", "repeat_penalty",
                                                        "seed", "samplers", "n_predict")},
        "note": "server defaults; each request's temperature, max_tokens and seed override them. On b11435 "
                "n_predict here does not reflect a -n launch flag, so the output cap is sent per request "
                "(--max-tokens) instead. Launch flags not exposed here (e.g. chat-template kwargs) are not captured.",
    }


def cmd_run(a) -> int:
    profile = _profile(a)                                    # validates base_url, credential_ref shape, limits
    plan = _plan(a)
    suite = plan["suite"]
    env_version, benchmark_version = suite["env_version"], suite["benchmark_version"]

    def model_config(temperature: float) -> Dict[str, Any]:
        cfg = {"profile_id": profile.id, "kind": profile.kind, "base_url": profile.base_url,
               "model_id": profile.model_id, "scaffold": f"{r.RUNNER_VERSION} finish-tool",
               "temperature": temperature, "timeout_s": profile.timeout_s, "max_retries": profile.max_retries}
        if a.max_tokens is not None:
            cfg["response_length_limit"] = a.max_tokens
        if a.sampling_seed is not None:
            cfg["sampling_seed"] = a.sampling_seed
        return g.sanitize_model_config(cfg)

    temps = sorted({ep[4] for ep in plan["episodes"]})
    for t in temps:
        model_config(t)
    print(f"plan: {len(plan['episodes'])} episodes = {len(plan['tasks'])} task(s) x {len(a.faults)} fault scenario(s) "
          f"x {len(a.seeds)} seed(s) x {a.repeats} repeat(s); model {profile.model_id} via {profile.base_url}; "
          f"suite {suite['suite_id']} {suite['suite_version']} on environment {env_version}; "
          f"temperatures {temps}; max_tokens {a.max_tokens}; sampling seed {a.sampling_seed}")
    for task, fault, seed, rep, temp in plan["episodes"]:
        print(f"  episode: {task.task_id}@{task.task_version} fault={fault} {FAULT_SCENARIOS[fault]} seed={seed} "
              f"repeat={rep} temperature={temp}")
    if a.dry_run:
        print("dry run: no requests sent, nothing written")
        return 0
    if profile.kind == "api" and not a.approve_paid_run:
        print("refused: --profile api sends paid requests; re-run with --approve-paid-run after approval",
              file=sys.stderr)
        return 2
    try:
        m.resolve_credential(profile.credential_ref)         # fail fast; the value is discarded
    except m.ConfigError as e:
        print(f"refused: {e}", file=sys.stderr)              # names the reference, never a value
        return 2

    server = capture_server_props(profile.base_url) if profile.kind == "local" else {"available": False,
                                                                                    "error": "not_captured_for_api"}
    tools = r.agent_tools(env_version)
    run_id = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + secrets.token_hex(3)
    run_dir = Path(a.out) / run_id
    run_dir.mkdir(parents=True, exist_ok=False)              # unique: never overwrite an earlier run
    manifest = {
        "run_id": run_id, "cli_version": CLI_VERSION, "runner_version": r.RUNNER_VERSION,
        "benchmark_version": benchmark_version, "contract_version": g.CONTRACT_VERSION,
        "grader_version": g.GRADER_VERSION, "result_schema": g.RESULT_SCHEMA, "code_sha256": g.code_digests(),
        "created_utc": run_id[:16],
        "status": "PILOT: descriptive only; no benchmark-validity or frontier-model reliability claims",
        "suite": {"suite_id": suite["suite_id"], "suite_version": suite["suite_version"], "suite_file": a.suite},
        "environment": {"env_id": "meridian-commercial", "env_version": env_version,
                        "system_prompt_sha256": hashlib.sha256(r.SYSTEM_PROMPTS[env_version].encode()).hexdigest(),
                        "agent_tools_sha256": r._sha(tools), "agent_tool_names": [t["name"] for t in tools]},
        "tasks": [t.to_dict() for t in plan["tasks"]],
        "fault_scenarios": {f: FAULT_SCENARIOS[f] for f in a.faults},
        "seeds": a.seeds, "repeats": a.repeats,
        "sampling": {"temperatures_by_repeat": a.temperatures or [a.temperature] * a.repeats,
                     "max_tokens": a.max_tokens, "seed": a.sampling_seed,
                     "other_sampler_settings": "server defaults (see server.default_sampling)"},
        "model": model_config(temps[0]), "server": server,
        "credential_ref": profile.credential_ref,          # a reference such as env:NAME, never a value
        "budget": {"max_agent_ticks": a.max_agent_ticks, "max_model_turns": a.max_model_turns,
                   "max_episodes": a.max_episodes, "max_total_tokens": a.max_total_tokens},
        "prices_usd_per_mtok": {"prompt": a.price_prompt_per_mtok, "completion": a.price_completion_per_mtok},
        "episodes_planned": [{"task_id": t.task_id, "task_version": t.task_version, "fault": f, "seed": s,
                              "repeat": k, "temperature": temp} for t, f, s, k, temp in plan["episodes"]],
    }
    _write(run_dir / "manifest.json", manifest)

    model_fn = m.OpenAICompatibleAdapter(profile).as_model_fn()
    results: List[Dict[str, Any]] = []
    stop = "completed"
    try:
        for task, fault, seed, rep, temp in plan["episodes"]:
            used = sum(x["execution"]["usage"]["prompt_tokens"] + x["execution"]["usage"]["completion_tokens"]
                       for x in results)
            if a.max_total_tokens and used >= a.max_total_tokens:
                stop = "token_budget_reached"
                break
            res = r.run_episode(task, model_fn, seed=seed, fault_plan=FAULT_SCENARIOS[fault],
                                model_config=model_config(temp), max_model_turns=a.max_model_turns,
                                temperature=temp, repeat=rep, env_version=env_version,
                                benchmark_version=benchmark_version, response_length_limit=a.max_tokens,
                                sampling_seed=a.sampling_seed)
            res["run_id"], res["fault_scenario"] = run_id, fault
            results.append(res)
            with open(run_dir / "episodes.jsonl", "a", encoding="utf-8") as fh:
                fh.write(json.dumps(res, sort_keys=True) + "\n")
            ex = res["execution"]
            print(f"[{len(results)}/{len(plan['episodes'])}] {res['episode_id']}: {res['outcome']} "
                  f"({ex['termination']}, {ex['attribution']})")
    except KeyboardInterrupt:
        stop = "interrupted"
    summary = summarize(results, a, stop, len(plan["episodes"]))
    summary["run_id"] = run_id
    _write(run_dir / "summary.json", summary)
    _write(run_dir / "review_queue.json", [
        {"episode_id": x["episode_id"], "problem": x["report"]["problem"], "submitted": x["report"]["submitted"],
         "ledger_outcome": x["outcome"], "human_label": None} for x in results if x["report"]["needs_human_review"]])
    print(f"run {run_id}: {stop}; outcomes {summary['aggregate']['outcomes']}; "
          f"attribution {summary['attribution']}; written to {run_dir}")
    return 0 if stop == "completed" else 1


def _write(path: Path, obj: Any) -> None:
    path.write_text(json.dumps(obj, indent=1, sort_keys=True), encoding="utf-8")


def main(argv: Optional[List[str]] = None) -> int:
    a = build_parser().parse_args(argv)
    if a.command == "parser-eval":
        from .parser_eval import evaluate
        print(json.dumps(evaluate(version=a.parser_version), indent=1, sort_keys=True))
        return 0
    try:
        return cmd_run(a)
    except (m.ConfigError, g.GradingError) as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
