#!/usr/bin/env python3
"""
Merge optional classification review results back into the tagged questions.

Reads rule-based tagged questions plus review results, merges, and detects conflicts.

Usage:
    python3 scripts/merge_classification_results.py \
        --tagged tagged_questions.json \
        --model-results model_classification_results.json \
        --output merged_tagged_questions.json
"""

from __future__ import annotations

import json
import sys
import argparse
from pathlib import Path
from typing import Any


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def merge(
    tagged_questions: list[dict[str, Any]],
    model_results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge review results into tagged questions. Returns updated questions list."""
    review_map: dict[str, dict] = {}
    for r in model_results:
        tid = r.get("task_id")
        if tid:
            review_map[tid] = r

    stats = {
        "total": len(tagged_questions),
        "model_reviewed": 0,
        "agreed": 0,
        "conflict": 0,
        "model_unavailable": 0,
        "not_checked": 0,
    }

    for question in tagged_questions:
        qid = question.get("question_id", "")
        rule_class = question.get("classification", {})

        if qid in review_map:
            review = review_map[qid]
            stats["model_reviewed"] += 1

            if "error" in review:
                # Model review failed for this question
                rule_class["classifier_sources"] = ["rule_based"]
                rule_class["model_agreement"] = "model_unavailable"
                rule_class["needs_review"] = True
                rule_class["model_error"] = review["error"]
                stats["model_unavailable"] += 1
            else:
                # Compare rule-based vs review result
                model_data_item = review
                rule_skill = rule_class.get("skill")
                review_skill = model_data_item.get("skill")

                if rule_skill and review_skill and rule_skill != review_skill:
                    # Conflict detected
                    rule_class["model_agreement"] = "conflict"
                    rule_class["needs_review"] = True
                    rule_class["model_suggestion"] = {
                        "domain": model_data_item.get("domain"),
                        "skill": model_data_item.get("skill"),
                        "evidence": model_data_item.get("evidence"),
                        "reasoning": model_data_item.get("reasoning"),
                    }
                    stats["conflict"] += 1
                else:
                    # Agreement (or rule-based was None and review filled it)
                    rule_class["model_agreement"] = "agree"
                    rule_class["classifier_sources"] = ["rule_based", "classification_model"]
                    rule_class["needs_review"] = model_data_item.get("needs_review", False)

                    # If rule-based was None, adopt review classification
                    if not rule_skill and review_skill:
                        rule_class["domain"] = model_data_item.get("domain")
                        rule_class["skill"] = model_data_item.get("skill")
                        rule_class["subtype"] = model_data_item.get("subtype", "unknown")
                        rule_class["confidence"] = model_data_item.get("confidence", "medium")
                        rule_class["evidence"] = model_data_item.get("evidence", rule_class.get("evidence", {}))

                    # Upgrade confidence if review confirms
                    if model_data_item.get("confidence") == "high" and rule_class.get("confidence") in ("medium", "low"):
                        rule_class["confidence"] = "high"

                    stats["agreed"] += 1

                # Add review evidence notes
                review_evidence = model_data_item.get("evidence", {})
                if review_evidence:
                    existing_evidence = rule_class.get("evidence", {})
                    for key in review_evidence:
                        if review_evidence[key] and not existing_evidence.get(key):
                            existing_evidence[key] = review_evidence[key]
                    rule_class["evidence"] = existing_evidence
        else:
            # No review for this question (usually high confidence or --skip-review)
            rule_class["classifier_sources"] = ["rule_based"]
            rule_class["model_agreement"] = "not_checked"
            stats["not_checked"] += 1

        # Final needs_review check
        if rule_class.get("confidence") in ("medium", "low"):
            rule_class["needs_review"] = True

        question["classification"] = rule_class

    print(f"── Merge Summary ──", file=sys.stderr)
    print(f"  Total questions:       {stats['total']}", file=sys.stderr)
    print(f"  Model reviewed:         {stats['model_reviewed']}", file=sys.stderr)
    print(f"  Agreed:                {stats['agreed']}", file=sys.stderr)
    print(f"  Conflict:              {stats['conflict']}", file=sys.stderr)
    print(f"  Model unavailable:      {stats['model_unavailable']}", file=sys.stderr)
    print(f"  Not checked (bypass):  {stats['not_checked']}", file=sys.stderr)

    return tagged_questions


def main():
    parser = argparse.ArgumentParser(description="Merge classification results")
    parser.add_argument("--tagged", required=True, type=Path, help="tagged_questions.json from tag_question_types.py")
    parser.add_argument("--model-results", required=True, type=Path, help="model_classification_results.json")
    parser.add_argument("--output", required=True, type=Path, help="output merged JSON")
    args = parser.parse_args()

    if not args.tagged.exists():
        print(f"[ERROR] Tagged input not found: {args.tagged}", file=sys.stderr)
        return 1
    if not args.model_results.exists():
        print(f"[ERROR] Model results not found: {args.model_results}", file=sys.stderr)
        print("[HINT] Run run_classification_review.py first", file=sys.stderr)
        return 1

    tagged = load_json(args.tagged)
    model_data = load_json(args.model_results)
    model_results = model_data.get("results", model_data) if isinstance(model_data, dict) else model_data

    if isinstance(tagged, dict) and "questions" in tagged:
        questions = tagged["questions"]
    else:
        questions = tagged

    merged = merge(questions, model_results)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(merged, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Output: {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    exit(main())
