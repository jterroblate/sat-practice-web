#!/usr/bin/env python3
"""
QA Validator for SAT RW question type classification.

Checks:
1. Every question has classification field
2. domain and skill are from official taxonomy
3. evidence is non-empty
4. confidence low/medium → needs_review
5. model_agreement conflict → needs_review
6. model_unavailable is properly recorded

Usage:
    python3 scripts/validate_classification.py \
        --input merged_tagged_questions.json \
        --taxonomy config/sat_rw_taxonomy.json \
        --output classification_qa_report.json
"""

from __future__ import annotations

import json
import sys
import argparse
import re
from pathlib import Path
from typing import Any


SKILL_DIR = Path(__file__).resolve().parents[1]


def load_taxonomy(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def get_valid_skills(taxonomy: dict) -> set[str]:
    skills = set()
    for domain in taxonomy.get("domains", []):
        for skill in domain.get("skills", []):
            skills.add(skill["skill"])
    return skills


def get_domain_for_skill(taxonomy: dict, skill_name: str) -> str | None:
    for domain in taxonomy.get("domains", []):
        for skill in domain.get("skills", []):
            if skill["skill"] == skill_name:
                return domain["domain"]
    return None


def get_valid_subtypes(taxonomy: dict) -> dict[tuple[str, str], set[str]]:
    valid: dict[tuple[str, str], set[str]] = {}
    for domain in taxonomy.get("domains", []):
        domain_name = domain.get("domain", "")
        for skill in domain.get("skills", []):
            examples = skill.get("subtype_examples", {}) or {}
            valid[(domain_name, skill.get("skill", ""))] = set(examples.keys())
    return valid


def option_shape_mismatch(question: dict[str, Any], skill: str | None) -> str | None:
    """Catch high-confidence stem-only tags contradicted by A-D option form."""
    if skill != "Transitions":
        return None
    choices = [
        str(choice.get("text") or "")
        for choice in question.get("choices", [])
        if isinstance(choice, dict)
    ]
    if len(choices) != 4:
        return None
    apostrophe_variants = sum("'" in text or "’" in text for text in choices)
    token_sets = [set(re.findall(r"[a-z]+", text.casefold())) for text in choices]
    shared_tokens = set.intersection(*token_sets) if all(token_sets) else set()
    # A transition set normally varies connector words. When most choices
    # instead repeat the same nouns and differ in apostrophe placement, the
    # option shape is a noun-form/possessive convention question even if an
    # OCR/source stem mistakenly says "transition."
    if apostrophe_variants >= 2 and len(shared_tokens) >= 2:
        return "transition_tag_conflicts_with_possessive_plural_option_shape"
    return None


def validate(
    questions: list[dict[str, Any]],
    taxonomy: dict,
    model_api_success: bool | None = None,
) -> dict[str, Any]:
    """Run all QA checks. Returns a validation report."""
    valid_skills = get_valid_skills(taxonomy)
    valid_subtypes = get_valid_subtypes(taxonomy)
    total = len(questions)
    passed = 0
    failed = 0
    needs_review_list = []
    conflict_list = []
    low_confidence_list = []
    missing_fields = []
    invalid_skills = []
    domain_skill_mismatch = []
    evidence_errors = []
    subtype_warnings = []
    option_shape_mismatches = []

    for question in questions:
        qid = question.get("question_id", "unknown")
        classification = question.get("classification", {})

        issues = []
        is_valid = True

        # Check 1: classification exists
        if not classification:
            missing_fields.append({"question_id": qid, "missing": "classification"})
            is_valid = False
            failed += 1
            continue

        # Check 2: domain and skill present
        domain = classification.get("domain")
        skill = classification.get("skill")
        if not domain:
            missing_fields.append({"question_id": qid, "missing": "domain"})
            is_valid = False
        if not skill:
            missing_fields.append({"question_id": qid, "missing": "skill"})
            is_valid = False

        # Check 3: skill in official taxonomy
        if skill and skill not in valid_skills:
            invalid_skills.append({"question_id": qid, "skill": skill})
            is_valid = False

        # Check 4: domain matches skill
        if domain and skill:
            expected_domain = get_domain_for_skill(taxonomy, skill)
            if expected_domain and domain != expected_domain:
                domain_skill_mismatch.append({
                    "question_id": qid,
                    "domain": domain,
                    "skill": skill,
                    "expected_domain": expected_domain,
                })
                is_valid = False

        # Check 4a: option morphology can disprove a stem-only high-confidence
        # tag (for example, a source stem saying "transition" while A-D vary
        # only plural/possessive apostrophes).
        shape_issue = option_shape_mismatch(question, skill)
        if shape_issue:
            option_shape_mismatches.append({
                "question_id": qid,
                "skill": skill,
                "issue": shape_issue,
            })
            is_valid = False

        # Check 5: evidence present and non-empty
        evidence = classification.get("evidence", {})
        if not evidence or not any(evidence.values()):
            evidence_errors.append({"question_id": qid, "evidence": evidence})
            is_valid = False

        # Check 5a: subtype vocabulary warning only. Do not block current skill-level taxonomy.
        subtype = classification.get("subtype")
        allowed_subtypes = valid_subtypes.get((domain, skill), set())
        if subtype and allowed_subtypes and subtype not in allowed_subtypes:
            subtype_warnings.append({
                "question_id": qid,
                "domain": domain,
                "skill": skill,
                "subtype": subtype,
                "allowed_subtypes": sorted(allowed_subtypes),
            })

        # Check 6: confidence
        confidence = classification.get("confidence", "")
        if confidence in ("low", "medium"):
            low_confidence_list.append({
                "question_id": qid,
                "confidence": confidence,
                "skill": skill,
                "needs_review": classification.get("needs_review", False),
            })
            if not classification.get("needs_review"):
                # confidence low/medium should trigger needs_review
                issues.append("confidence low/medium but needs_review is false")

        # Check 7: model agreement conflicts
        agreement = classification.get("model_agreement", "")
        if agreement == "conflict":
            conflict_list.append({
                "question_id": qid,
                "rule_based_skill": skill,
                "model_suggestion": classification.get("model_suggestion"),
            })
            if not classification.get("needs_review"):
                issues.append("model conflict but needs_review is false")

        # Check 8: model_unavailable
        if agreement == "model_unavailable":
            if not classification.get("needs_review"):
                issues.append("model unavailable but needs_review is false")

        if issues:
            is_valid = False

        if not is_valid:
            failed += 1
            if classification.get("needs_review"):
                needs_review_list.append({
                    "question_id": qid,
                    "skill": skill,
                    "confidence": confidence,
                    "agreement": agreement,
                    "issues": issues,
                })
        else:
            passed += 1

    # Summary
    model_available = model_api_success if model_api_success is not None else True
    model_calls_succeeded = sum(
        1 for q in questions
        if q.get("classification", {}).get("model_agreement") in ("agree", "conflict")
    )
    model_calls_failed = sum(
        1 for q in questions
        if q.get("classification", {}).get("model_agreement") == "model_unavailable"
    )

    qa_passed = (
        failed == 0
        and len(invalid_skills) == 0
        and len(domain_skill_mismatch) == 0
    )

    report = {
        "qa_passed": qa_passed,
        "total_questions": total,
        "passed_questions": passed,
        "failed_questions": failed,
        "needs_review_count": len(needs_review_list),
        "conflict_count": len(conflict_list),
        "low_confidence_count": len(low_confidence_list),
        "model_api": {
            "available": model_available,
            "successful_calls": model_calls_succeeded,
            "failed_calls": model_calls_failed,
        },
        "domain_skill_distribution": {},
        "details": {
            "needs_review": needs_review_list,
            "conflicts": conflict_list,
            "subtype_warnings": subtype_warnings,
        },
        "errors": {
            "missing_fields": missing_fields,
            "invalid_skills": invalid_skills,
            "domain_skill_mismatch": domain_skill_mismatch,
            "evidence_errors": evidence_errors,
            "option_shape_mismatches": option_shape_mismatches,
        },
    }

    # Domain/skill distribution
    for q in questions:
        c = q.get("classification", {})
        d = c.get("domain", "unknown")
        s = c.get("skill", "unknown")
        if d not in report["domain_skill_distribution"]:
            report["domain_skill_distribution"][d] = {"total": 0, "skills": {}}
        report["domain_skill_distribution"][d]["total"] += 1
        if s not in report["domain_skill_distribution"][d]["skills"]:
            report["domain_skill_distribution"][d]["skills"][s] = 0
        report["domain_skill_distribution"][d]["skills"][s] += 1

    return report


def main():
    parser = argparse.ArgumentParser(description="Classification QA Validator")
    parser.add_argument("--input", required=True, type=Path, help="merged tagged questions JSON")
    parser.add_argument("--taxonomy", type=Path, default=None, help="taxonomy JSON path")
    parser.add_argument("--output", required=True, type=Path, help="QA report JSON path")
    parser.add_argument("--model-status", type=str, default=None,
                        help="model_api_success? (true/false), auto-detected if omitted")
    args = parser.parse_args()

    taxonomy_path = args.taxonomy or SKILL_DIR / "config" / "sat_rw_taxonomy.json"
    if not taxonomy_path.exists():
        print(f"[ERROR] Taxonomy not found: {taxonomy_path}", file=sys.stderr)
        return 1

    taxonomy = load_taxonomy(taxonomy_path)
    data = json.loads(args.input.read_text(encoding="utf-8"))
    questions = data if isinstance(data, list) else data.get("questions", data)

    if args.model_status:
        model_ok = args.model_status.lower() == "true"
    else:
        # Auto-detect from first question's model_agreement
        model_ok = None

    report = validate(questions, taxonomy, model_ok)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"\n── Classification QA Report ──", file=sys.stderr)
    print(f"  QA {'PASS' if report['qa_passed'] else 'FAIL'}", file=sys.stderr)
    print(f"  Total:     {report['total_questions']}", file=sys.stderr)
    print(f"  Passed:    {report['passed_questions']}", file=sys.stderr)
    print(f"  Failed:    {report['failed_questions']}", file=sys.stderr)
    print(f"  Needs review: {report['needs_review_count']}", file=sys.stderr)
    print(f"  Conflicts:    {report['conflict_count']}", file=sys.stderr)
    print(f"  Low conf:     {report['low_confidence_count']}", file=sys.stderr)
    print(f"  Model API OK:  {report['model_api']['available']}", file=sys.stderr)
    print(f"  Model calls:   {report['model_api']['successful_calls']} OK / {report['model_api']['failed_calls']} failed", file=sys.stderr)
    print(f"\nDomain/Skill Distribution:", file=sys.stderr)
    for d, info in report["domain_skill_distribution"].items():
        print(f"  {d}: {info['total']} questions", file=sys.stderr)
        for s, count in info["skills"].items():
            print(f"    {s}: {count}", file=sys.stderr)
    print(f"\nOutput: {args.output}", file=sys.stderr)

    return 0 if report["qa_passed"] else 1


if __name__ == "__main__":
    exit(main())
