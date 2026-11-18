# Historical BankOps run records (regression fixtures)

Byte-for-byte copies of `episodes.jsonl` from two local Qwen3-4B runs. The originals are under the
run-output directory outside the repository, and were never modified. The tests check both SHA-256
hashes before use.

| File | Run | Recorded under | SHA-256 |
|---|---|---|---|
| `20261118T004304Z-b0c925.episodes.jsonl` | First valid local run (pilot protocol §9.3): suite 0.1, environment 0.1 | grader 0.2.0, result schema 0.2 | `a1f932e898c6c698f91b437f4b22c97cfe6e7379dbbd37edac80eea892f97707` |
| `20261118T012206Z-77335f.episodes.jsonl` | Second local pilot (pilot protocol §12): suite 0.2, environment 0.2, `MER-REC-001` v1 | grader 0.3.0, result schema 0.3 | `449db3086f950b1a0f7137733947866270245fade4ba3ac488bdf57047490258` |

**How they are used:**
- Every episode must replay and reproduce **exactly** under its own recorded grader version.
- `compare_interpretations` shows, read-only, what grader 0.4.0 says about the same trace.

**Rules:** never edit these files, and never re-grade them in place. The only non-loopback information they contain is the model id; the base URL is `127.0.0.1`.
