# Captured llama-server responses (truncation regression fixtures)

Real `/v1/chat/completions` responses, captured on 2026-11-18 from llama.cpp `llama-server` build
b11435 (commit 43fe9c642) serving `Qwen3-4B-Q4_K_M.gguf` (Q4_K Medium). The server was started with
`-c 8192 -np 1 -t 4 -n 768 --jinja --reasoning-budget 0` and `LLAMA_ARG_CHAT_TEMPLATE_KWARGS={"enable_thinking":false}`.
All requests used temperature 0.

| File | Request | `finish_reason` | What it shows |
|---|---|---|---|
| `tool_call_complete.json` | Meridian system prompt, `MER-PAY-001` instruction, the 14 agent tools | `tool_calls` | A normal tool call (`list_invoices`) |
| `tool_call_truncated.json` | Same, with `max_tokens: 6` | `length` | **A tool call cut off at the limit arrives with no `tool_calls` and the partial call as plain `content`** (`'<tool_call>\n{"name": "'`) |
| `text_truncated.json` | A free-text question, `max_tokens: 12` | `length` | Truncated prose |
| `text_complete.json` | "Reply with exactly the word: done", `max_tokens: 20` | `stop` | A normal text answer |

**Sanitisation:** only the top-level `model` field was changed, from the server's local file path to the file name. Every other field (`id`, `created`, `usage`, `timings`, `system_fingerprint`, `choices`) is byte-for-byte as returned.
