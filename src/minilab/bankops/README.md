# BankOps (Meridian v0): developer setup

BankOps evaluates banking-agent **recovery reliability under operational uncertainty**:
- lost or delayed responses;
- maker–checker approval;
- double-entry ledger invariants.

It is graded deterministically from the recorded ledger, operation lifecycle and authorisation records, never from the agent's own report.

**Status: pilot.** Four tasks, one synthetic bank. Nothing here supports benchmark-validity or frontier-model reliability claims. Design, contracts and limits: `docs/roadmap/BANKOPS_DAY2_READINESS.md` §5; pilot plan: `docs/roadmap/BANKOPS_REAL_MODEL_PILOT_PROTOCOL.md`.

## Layout
| Path | Purpose |
|---|---|
| `ledger.py` | Double-entry ledger, operation lifecycle and audit log, violation history |
| `envs/meridian.py`, `envs/meridian_v0*.json` | Meridian Commercial Bank environment; fixture per environment version (0.1 frozen; 0.2 adds policy POL-6 stating the `client_ref` contract; 0.3 adds USD, superseded before any run; 0.4: INR in integer paise, agent-facing `*_paise` fields, required currency); runner-controlled fault worlds |
| `grading.py` | Deterministic grader with versioned semantics (0.2.0 to current 0.6.0 all executable) and report parser versions 0.1/0.2: report interpretation, recovery evidence, approval states, instruction correctness, task validity, aggregates |
| `runner.py` | Episode loop, event trace, `replay_episode` (re-grades under the recorded grader version), `compare_interpretations` (read-only regrade), `trajectory_summary` (grader-independent) |
| `models.py` | Standard-library OpenAI-compatible model adapter (`api` and `local` profiles) |
| `cli.py` | Command line: `run`, `parser-eval` |
| `parser_eval.py`, `suites/report_parser_corpus_v0.json` | Natural-language report parser evaluation (no LLM judge) |
| `suites/meridian_v0*_pilot.json` | Suites 0.1–0.4, one per environment version (0.2 adds the delayed-commit task `MER-REC-001`; 0.4 is the current INR suite) |

## Requirements
- Python ≥ 3.10 (the repository's `requires-python`; verified on 3.13.2). The BankOps code uses only the standard library plus the repository's own `minilab` package.
- Tests: `PYTHONPATH=src python -m unittest discover -s tests` from the repository root (PowerShell: `$env:PYTHONPATH = "src"` first).

## Run with a local model (no cost)
Any OpenAI-compatible server works. With llama.cpp:
```powershell
$env:LLAMA_ARG_CHAT_TEMPLATE_KWARGS = '{"enable_thinking":false}'   # Qwen3: --reasoning-budget 0 alone does NOT stop thinking
llama-server -m <path>\Qwen3-4B-Q4_K_M.gguf --host 127.0.0.1 --port 8081 -c 8192 -np 1 -t 4 -n 768 --jinja --reasoning-budget 0
$env:PYTHONPATH = "src"
python -m minilab.bankops.cli run --profile local --model-id qwen3-4b --base-url http://127.0.0.1:8081/v1 `
  --suite meridian_v0_2_pilot --tasks MER-REC-001 --faults late_commit_6_approved --seeds 1 `
  --repeats 2 --temperatures 0,0.3 --sampling-seed 7 --max-tokens 512 --timeout 300 --max-retries 0 `
  --max-model-turns 16 --out <directory outside the repository> --dry-run
```
Remove `--dry-run` to execute.
- An 8k context is needed: the tool schemas and history exceed 4k.
- Check that thinking is off before a run: a first turn should be a short tool call, not prose.
- `--max-tokens` caps each response; a capped response ends the episode as `truncated_model_output` (an agent failure), never as a report.
- For local profiles the run manifest records the server's own `/props` (build, model file, quantisation, context, default sampler settings).
- Ollama works too: `--base-url http://127.0.0.1:11434/v1` with an Ollama model id.

## Hosted APIs (paid)
- `--profile api --credential-ref env:NAME` names the environment variable that holds the key. Keys are never passed on the command line, printed or written.
- Paid runs are refused without `--approve-paid-run`.

## Outputs
Each run writes `<out>/<UTC timestamp>-<random>/`, containing `manifest.json`, `episodes.jsonl`, `summary.json` and `review_queue.json`.

`runs/` is **not** git-ignored, so write run outputs outside the repository (`--out`) or never stage them.

Re-grade any stored episode:
```python
import json
from minilab.bankops.runner import replay_episode
for line in open("episodes.jsonl", encoding="utf-8"):
    replay_episode(json.loads(line))     # raises ReplayMismatch if anything differs
```
