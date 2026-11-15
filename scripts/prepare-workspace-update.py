#!/usr/bin/env python3
"""Prepare a review plan from public Git changes; never write to production."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
CONTENT_FIELDS = {
    "passage", "prompt", "choices", "correct_answer", "explanation", "classification",
    "difficulty_estimate", "skills_tested", "table_data", "chart_display_scale",
    "chart_image_width", "chart_image_height", "chart_image", "chart_image_data_uri",
    "visual_assets", "public_visual_alt_text", "math_metadata", "module_id", "module_position",
}
SOURCE_PATHS = [
    "skills/skill-sat-rw-classification/scripts/build_learning_experience.py",
    "skills/skill-sat-rw-classification/assets/templates/html_template_v1.1.html",
    "skills/skill-sat-rw-classification/assets/templates/learning_index_v2.html",
    "skills/skill-sat-rw-classification/assets/templates/learning_storage.js",
    "skills/skill-sat-rw-classification/config/sat_rw_taxonomy.json",
    *["skills/skill-sat-rw-classification/assets/zen-interface/" + name + ".svg"
      for name in ("background", "pattern", "texture")],
]


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def value_digest(value: object) -> str:
    return digest(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode())


def at_ref(ref: str, relative: str) -> bytes:
    result = subprocess.run(["git", "show", f"{ref}:{relative}"], cwd=ROOT, capture_output=True)
    if result.returncode:
        raise ValueError(f"Missing baseline file {relative}; choose a valid --base-ref")
    return result.stdout


def indexed(package: object) -> dict:
    rows = package.get("questions") if isinstance(package, dict) else package
    if not isinstance(rows, list):
        raise ValueError("Canonical package has no question list")
    result = {}
    for row in rows:
        qid = row.get("question_id")
        if not qid or qid in result:
            raise ValueError("Missing or duplicate question_id")
        result[qid] = row
    return result


def plan_question_changes(before: dict, after: dict, private: dict) -> list[dict]:
    changes = []
    for field in sorted(set(before) | set(after)):
        if before.get(field) == after.get(field) and (field in before) == (field in after):
            continue
        matches = (field in before) == (field in private) and private.get(field) == before.get(field)
        allowed = field in CONTENT_FIELDS
        changes.append({"field": field, "operation": "replace" if field in after else "remove",
                        "baselineValueSha256": value_digest(before.get(field)),
                        "privateValueSha256": value_digest(private.get(field)),
                        "proposedValue": after.get(field), "preimageMatches": matches,
                        "editableField": allowed,
                        "status": "ready-for-review" if matches and allowed else "manual-review-required"})
    return changes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--base-ref", default="initial-import")
    parser.add_argument("--output", type=Path, default=ROOT / "build/workspace-update.json")
    args = parser.parse_args()
    workspace, output = args.workspace.resolve(), args.output.resolve()
    if workspace == ROOT or not (workspace / "sat-content-registry.json").is_file():
        parser.error("--workspace must be a separate existing production content workspace")
    if not output.is_relative_to((ROOT / "build").resolve()):
        parser.error("--output must stay under this repository's ignored build directory")
    resolved = subprocess.run(["git", "rev-parse", "--verify", f"{args.base_ref}^{{commit}}"],
                              cwd=ROOT, text=True, capture_output=True, check=True).stdout.strip()
    source_changes = []
    for relative in SOURCE_PATHS:
        before = at_ref(resolved, relative)
        source, target = ROOT / relative, workspace / relative
        if not source.is_file() or source.is_symlink():
            raise ValueError(f"Missing or symlinked shared source: {relative}")
        if not target.resolve().is_relative_to(workspace) or target.is_symlink():
            raise ValueError(f"Unsafe private source path: {relative}")
        after = source.read_bytes()
        if before != after:
            original = target.read_bytes() if target.is_file() else b""
            source_changes.append({"path": relative, "baselineSha256": digest(before),
                                   "proposedSha256": digest(after), "privateSha256": digest(original),
                                   "status": "ready-for-review" if original == before else "manual-review-required"})
    questions = []
    for current in sorted((ROOT / "content/canonical").rglob("*.json")):
        relative = current.relative_to(ROOT).as_posix()
        try:
            before = indexed(json.loads(at_ref(resolved, relative)))
        except ValueError:
            questions.append({"path": relative, "status": "new-package-manual-review-required"})
            continue
        after = indexed(json.loads(current.read_text(encoding="utf-8")))
        target = workspace / relative
        if target.is_symlink() or not target.resolve().is_relative_to(workspace):
            raise ValueError(f"Unsafe private canonical path: {relative}")
        private = indexed(json.loads(target.read_text(encoding="utf-8"))) if target.is_file() else {}
        for qid in sorted(set(before) | set(after)):
            if qid not in before or qid not in after or qid not in private:
                questions.append({"path": relative, "questionId": qid,
                                  "status": "question-membership-manual-review-required"})
                continue
            fields = plan_question_changes(before[qid], after[qid], private[qid])
            if fields:
                questions.append({"path": relative, "questionId": qid, "changes": fields})
    protocol_changes = [{"path": p, "status": "manual-review-required"}
                        for p in ("sat-content-registry.json", "sat-practice-migrations.json")
                        if at_ref(resolved, p) != (ROOT / p).read_bytes()]
    tracked_before = subprocess.run(["git", "ls-tree", "-r", "--name-only", resolved, "content/canonical"],
                                    cwd=ROOT, capture_output=True, text=True, check=True).stdout.splitlines()
    asset_changes = subprocess.run(["git", "diff", "--name-only", resolved, "--", "assets", "categories/chart_images"],
                                   cwd=ROOT, capture_output=True, text=True, check=True).stdout.splitlines()
    report = {"schemaVersion": "sat-workspace-review-plan-v1", "baseCommit": resolved,
              "writesToProduction": False, "sourceChanges": source_changes,
              "questionChanges": questions, "protocolChanges": protocol_changes,
              "assetChangesForManualReview": asset_changes,
              "removedPackagesForManualReview": [p for p in tracked_before if not (ROOT / p).is_file()],
              "nextStep": "Review fields, assets, scoring revisions and private preimages; assemble a new candidate. This plan applies nothing."}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Prepared local review plan: {len(source_changes)} source changes, {len(questions)} question entries. Production untouched.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
