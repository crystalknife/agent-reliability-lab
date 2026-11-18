"""Evaluate the deterministic natural-language report parser (`grading.parse_text_report`) against a
labelled corpus. No LLM judge. This measures the parser; it never changes grading rules.

Metrics (claims are (invoice_id, claimed_status) pairs):
  claim precision / recall      over all items (claims the parser emits on 'review' items are false positives)
  status accuracy               on items both gold and parser accept, where the gold status is scored
  human_review_rate             share of items the parser flags uninterpretable
  false_acceptance_rate         'review' items the parser accepted (the dangerous error)
  unnecessary_review_rate       'accept' items the parser flagged
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

from .grading import PARSER_VERSION, SUITE_DIR, parse_text_report

CORPUS = SUITE_DIR / "report_parser_corpus_v0.json"


def evaluate(corpus_path: Optional[Path] = None, version: str = PARSER_VERSION) -> Dict[str, Any]:
    """Evaluate one parser version against the unchanged gold labels."""
    corpus = json.loads(Path(corpus_path or CORPUS).read_text(encoding="utf-8"))
    tp = fp = fn = 0
    status_ok = status_n = flagged = false_accept = unnecessary = 0
    n_review = n_accept = 0
    per_item, by_cat = [], {}
    for item in corpus["items"]:
        gold = item["gold"]
        pred, reason = parse_text_report(item["text"], version)
        accepted = pred is not None
        p_claims = {(c["invoice_id"], c["claimed_status"]) for c in (pred or {}).get("payments", [])}
        g_claims = {tuple(c) for c in gold["claims"]}
        i_tp, i_fp, i_fn = len(p_claims & g_claims), len(p_claims - g_claims), len(g_claims - p_claims)
        tp, fp, fn = tp + i_tp, fp + i_fp, fn + i_fn
        flagged += not accepted
        if gold["decision"] == "review":
            n_review += 1
            false_accept += accepted
        else:
            n_accept += 1
            unnecessary += not accepted
            if accepted and gold["status"] is not None:
                status_n += 1
                status_ok += pred["status"] == gold["status"]
        correct = (accepted == (gold["decision"] == "accept") and i_fp == 0 and i_fn == 0
                   and (not accepted or gold["status"] is None or pred["status"] == gold["status"]))
        cat = by_cat.setdefault(item["category"], {"items": 0, "fully_correct": 0})
        cat["items"] += 1
        cat["fully_correct"] += correct
        per_item.append({"id": item["id"], "category": item["category"], "gold_decision": gold["decision"],
                         "parser": "accept" if accepted else "review", "reason": reason,
                         "pred_status": pred["status"] if pred else None, "gold_status": gold["status"],
                         "false_positive_claims": sorted(p_claims - g_claims),
                         "missed_claims": sorted(g_claims - p_claims), "fully_correct": correct,
                         "duplicate_disclosed": sorted(c["invoice_id"] for c in (pred or {}).get("payments", [])
                                                       if c.get("duplicate_disclosed"))})
    n = len(corpus["items"])
    ratio = lambda a, b: round(a / b, 4) if b else None  # noqa: E731
    accepted_gold_accept = sum(1 for x in per_item if x["gold_decision"] == "accept" and x["parser"] == "accept")
    return {
        "parser_version": version,
        # three separate questions: is it right, how much does it handle, how much needs a human
        "accuracy": ratio(sum(x["fully_correct"] for x in per_item), n),
        "coverage": ratio(accepted_gold_accept, n_accept),
        "human_review_rate": ratio(flagged, n),
        "duplicate_disclosures_detected": [x["id"] for x in per_item if x["duplicate_disclosed"]],
        "corpus": {"id": corpus["corpus_id"], "version": corpus["corpus_version"], "items": n,
                   "accept_items": n_accept, "review_items": n_review, "labelling": corpus["labelling"]},
        "claim_precision": ratio(tp, tp + fp), "claim_recall": ratio(tp, tp + fn),
        "claims": {"tp": tp, "fp": fp, "fn": fn},
        "status_accuracy": ratio(status_ok, status_n), "status_scored_items": status_n,
        "false_acceptance_rate": ratio(false_accept, n_review),
        "unnecessary_review_rate": ratio(unnecessary, n_accept),
        "fully_correct_items": sum(x["fully_correct"] for x in per_item),
        "by_category": dict(sorted(by_cat.items())),
        "items": per_item,
    }


if __name__ == "__main__":
    print(json.dumps(evaluate(), indent=1, sort_keys=True))
