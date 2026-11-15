#!/usr/bin/env python3
"""Prepare a complete review plan from a clean, fixed HEAD; never apply it."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
CONTENT_FIELDS = {
    "passage", "prompt", "choices", "correct_answer", "explanation", "classification",
    "difficulty_estimate", "skills_tested", "table_data", "chart_display_scale",
    "chart_image_width", "chart_image_height", "chart_image", "chart_image_data_uri",
    "visual_assets", "public_visual_alt_text", "math_metadata", "module_id", "module_position",
}
SOURCE_PATHS = {
    "skills/skill-sat-rw-classification/scripts/build_learning_experience.py",
    "skills/skill-sat-rw-classification/assets/templates/html_template_v1.1.html",
    "skills/skill-sat-rw-classification/assets/templates/learning_index_v2.html",
    "skills/skill-sat-rw-classification/assets/templates/learning_storage.js",
    "skills/skill-sat-rw-classification/config/sat_rw_taxonomy.json",
    *["skills/skill-sat-rw-classification/assets/zen-interface/" + name + ".svg"
      for name in ("background", "pattern", "texture")],
}
PROTOCOL_PATHS = {"sat-content-registry.json", "sat-practice-migrations.json", "assets/content-resources.json"}
REGULAR_MODES = {"100644", "100755"}


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def value_digest(value: object) -> str:
    return digest(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode())


def git(root: Path, *arguments: str) -> bytes:
    result = subprocess.run(["git", "-C", str(root), *arguments], capture_output=True)
    if result.returncode:
        raise ValueError("Git could not read the requested clean snapshot or baseline")
    return result.stdout


def assert_no_symlinks(path: Path, label: str) -> None:
    for component in (path, *path.parents):
        if component.is_symlink():
            raise ValueError(f"{label} must not contain a symbolic link")


def validate_boundaries(root: Path, workspace: Path, output: Path) -> tuple[Path, Path, Path]:
    # Resolving first would hide a build -> production symbolic link.
    root, workspace, output = root.absolute(), workspace.absolute(), output.absolute()
    for path, label in ((root, "Repository"), (workspace, "Workspace"), (output, "Output")):
        assert_no_symlinks(path, label)
    root, workspace, output = root.resolve(), workspace.resolve(), output.resolve()
    if root.is_relative_to(workspace) or workspace.is_relative_to(root):
        raise ValueError("Repository and production workspace must be disjoint directories")
    build = root / "build"
    if output == build or not output.is_relative_to(build) or output.is_relative_to(workspace):
        raise ValueError("Output must stay inside the real repository build directory, outside production")
    if build.exists() and not build.is_dir():
        raise ValueError("Repository build must be a directory")
    if output.exists() and not output.is_file():
        raise ValueError("Output must be a regular file or a new file")
    registry = workspace / "sat-content-registry.json"
    assert_no_symlinks(registry, "Private registry")
    if not registry.is_file():
        raise ValueError("Workspace must contain an existing content registry")
    return root, workspace, output


def require_clean_head(root: Path, expected_head: str | None = None) -> str:
    top = Path(os.fsdecode(git(root, "rev-parse", "--show-toplevel")).strip()).resolve()
    if top != root:
        raise ValueError("The script root must be the repository root")
    head = git(root, "rev-parse", "--verify", "HEAD^{commit}").decode().strip()
    if expected_head is not None and head != expected_head:
        raise ValueError("HEAD changed while preparing the plan; run again")
    if git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all"):
        raise ValueError("Commit or separately preserve tracked edits and non-ignored untracked files before preparing a plan")
    return head


def tree(root: Path, ref: str) -> dict[str, dict]:
    entries = {}
    for item in git(root, "ls-tree", "-r", "-z", "--full-tree", ref).split(b"\0"):
        if item:
            header, name = item.split(b"\t", 1)
            mode, kind, object_id = header.decode().split()
            entries[os.fsdecode(name)] = {"mode": mode, "kind": kind, "objectId": object_id}
    return entries


def blob(root: Path, entries: dict, relative: str) -> bytes | None:
    entry = entries.get(relative)
    if entry is None:
        return None
    if entry["mode"] not in REGULAR_MODES or entry["kind"] != "blob":
        raise ValueError(f"Review input is not a regular version-controlled file: {relative}")
    return git(root, "cat-file", "blob", entry["objectId"])


def changed_files(root: Path, before: str, after: str) -> list[dict]:
    tokens = git(root, "diff", "--name-status", "--no-renames", "-z", before, after, "--").split(b"\0")
    tokens = tokens[:-1] if tokens and not tokens[-1] else tokens
    if len(tokens) % 2:
        raise ValueError("Cannot enumerate every changed Git path")
    return [{"gitStatus": tokens[i].decode(), "path": os.fsdecode(tokens[i + 1])}
            for i in range(0, len(tokens), 2)]


def private_bytes(workspace: Path, relative: str) -> bytes | None:
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("Unsafe private review path")
    target = workspace / path
    assert_no_symlinks(target, "Private review input")
    if not target.resolve().is_relative_to(workspace):
        raise ValueError("Private review input escapes its workspace")
    if target.exists() and not target.is_file():
        raise ValueError(f"Private review input is not a regular file: {relative}")
    return target.read_bytes() if target.is_file() else None


def package_data(payload: bytes | None) -> tuple[dict, list[str], dict]:
    if payload is None:
        return {}, [], {}
    package = json.loads(payload)
    rows = package.get("questions") if isinstance(package, dict) else package
    if not isinstance(rows, list):
        raise ValueError("Canonical package has no question list")
    indexed = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("question_id"), str) or not row["question_id"]:
            raise ValueError("Canonical question must have a non-empty string ID")
        if row["question_id"] in indexed:
            raise ValueError("Duplicate canonical question ID")
        indexed[row["question_id"]] = row
    metadata = {key: value for key, value in package.items() if key != "questions"} if isinstance(package, dict) else {}
    return indexed, list(indexed), metadata


def plan_question_changes(before: dict, after: dict, private: dict, editable_fields: set | None = None) -> list[dict]:
    changes = []
    editable_fields = CONTENT_FIELDS if editable_fields is None else editable_fields
    for field in sorted(set(before) | set(after)):
        if before.get(field) == after.get(field) and (field in before) == (field in after):
            continue
        matches = (field in before) == (field in private) and private.get(field) == before.get(field)
        allowed = field in editable_fields
        operation = "remove" if field not in after else "add" if field not in before else "replace"
        changes.append({"field": field, "operation": operation,
                        "baselinePresent": field in before, "privatePresent": field in private,
                        "baselineValueSha256": value_digest(before.get(field)),
                        "privateValueSha256": value_digest(private.get(field)),
                        "proposedValue": after.get(field), "preimageMatches": matches,
                        "editableField": allowed,
                        "status": "ready-for-review" if matches and allowed else "manual-review-required"})
    return changes


def classify(relative: str) -> str:
    if relative.startswith("content/canonical/") and relative.endswith(".json"):
        return "canonical"
    if relative in PROTOCOL_PATHS:
        return "protocol"
    if relative.startswith(("assets/question-media/", "categories/chart_images/")):
        return "asset"
    if relative in SOURCE_PATHS:
        return "core-source"
    if relative.startswith(("scripts/", "skills/")):
        return "additional-source-manual"
    return "repository-file-manual"


def build_plan(root: Path, workspace: Path, base_ref: str) -> dict:
    head = require_clean_head(root)
    base = git(root, "rev-parse", "--verify", f"{base_ref}^{{commit}}").decode().strip()
    git(root, "merge-base", "--is-ancestor", base, head)
    before_tree, after_tree = tree(root, base), tree(root, head)
    inventory = changed_files(root, base, head)
    sources, questions, metadata_changes, order_changes, protocols, assets, removed = [], [], [], [], [], [], []
    for change in inventory:
        relative = change["path"]
        category = classify(relative)
        change.update({"category": category, "status": "manual-review-required",
                       "baselineGitObject": before_tree.get(relative), "proposedGitObject": after_tree.get(relative)})
        if category in {"core-source", "additional-source-manual"}:
            # Additions, deletions and new helper modules remain explicit manual work.
            regular = all(entry is None or entry["mode"] in REGULAR_MODES
                          for entry in (before_tree.get(relative), after_tree.get(relative)))
            before = blob(root, before_tree, relative) if regular else None
            after = blob(root, after_tree, relative) if regular else None
            private = private_bytes(workspace, relative)
            ready = category == "core-source" and change["gitStatus"] == "M" and regular and before == private
            sources.append({"path": relative, "operation": change["gitStatus"],
                            "baselineSha256": digest(before) if before is not None else None,
                            "proposedSha256": digest(after) if after is not None else None,
                            "privateSha256": digest(private) if private is not None else None,
                            "baselinePresent": relative in before_tree, "proposedPresent": relative in after_tree,
                            "status": "ready-for-review" if ready else "manual-review-required"})
        elif category == "canonical":
            before_bytes, after_bytes = blob(root, before_tree, relative), blob(root, after_tree, relative)
            before, before_order, before_meta = package_data(before_bytes)
            after, after_order, after_meta = package_data(after_bytes)
            private, private_order, private_meta = package_data(private_bytes(workspace, relative))
            if before_bytes is None or after_bytes is None:
                questions.append({"path": relative, "status": "new-package-manual-review-required"
                                  if before_bytes is None else "removed-package-manual-review-required"})
            if after_bytes is None:
                removed.append(relative)
            package_fields = plan_question_changes(before_meta, after_meta, private_meta, set())
            if package_fields:
                metadata_changes.append({"path": relative, "changes": package_fields,
                                         "status": "manual-review-required"})
            if before_order != after_order:
                order_changes.append({"path": relative, "baselineQuestionIds": before_order,
                                      "proposedQuestionIds": after_order, "privateQuestionIds": private_order,
                                      "preimageMatches": before_order == private_order,
                                      "status": "manual-review-required"})
            for qid in sorted(set(before) | set(after)):
                if qid not in before or qid not in after or qid not in private:
                    questions.append({"path": relative, "questionId": qid,
                                      "baselinePresent": qid in before, "proposedPresent": qid in after,
                                      "privatePresent": qid in private,
                                      "status": "question-membership-manual-review-required"})
                else:
                    fields = plan_question_changes(before[qid], after[qid], private[qid])
                    if fields:
                        questions.append({"path": relative, "questionId": qid, "changes": fields})
        elif category == "protocol":
            protocols.append({"path": relative, "operation": change["gitStatus"], "status": "manual-review-required"})
        elif category == "asset":
            assets.append({"path": relative, "operation": change["gitStatus"], "status": "manual-review-required"})
    require_clean_head(root, head)
    return {"schemaVersion": "sat-workspace-review-plan-v2", "baseCommit": base, "headCommit": head,
            "snapshotMode": "clean-committed-head", "writesToProduction": False,
            "coverage": {"changedFiles": len(inventory), "classifiedFiles": len(inventory), "unclassifiedFiles": 0},
            "changedFiles": inventory, "sourceChanges": sources, "questionChanges": questions,
            "packageMetadataChanges": metadata_changes, "questionOrderChanges": order_changes,
            "protocolChanges": protocols, "assetChangesForManualReview": assets,
            "removedPackagesForManualReview": removed,
            "otherFilesForManualReview": [item for item in inventory if item["category"] == "repository-file-manual"],
            "nextStep": "Review every changed path, private preimages, fields, membership, order, resources and scoring. Assemble a new candidate separately; this plan applies nothing."}


def write_plan(root: Path, workspace: Path, output: Path, report: dict) -> None:
    root, workspace, output = validate_boundaries(root, workspace, output)
    relative = output.relative_to(root).as_posix()
    git(root, "check-ignore", "-q", "--", relative)
    require_clean_head(root, report["headCommit"])
    output.parent.mkdir(parents=True, exist_ok=True)
    validate_boundaries(root, workspace, output)
    temporary = None
    try:
        descriptor, name = tempfile.mkstemp(prefix="." + output.name + ".", suffix=".tmp", dir=output.parent)
        temporary = Path(name)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            validate_boundaries(root, workspace, output)
            stream.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        require_clean_head(root, report["headCommit"])
        validate_boundaries(root, workspace, output)
        # Atomic replacement does not modify an existing hard-linked inode.
        os.replace(temporary, output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--base-ref", default="initial-import")
    parser.add_argument("--output", type=Path, default=ROOT / "build/workspace-update.json")
    args = parser.parse_args()
    try:
        root, workspace, output = validate_boundaries(ROOT, args.workspace, args.output)
        report = build_plan(root, workspace, args.base_ref)
        write_plan(root, workspace, output, report)
    except (OSError, ValueError, TypeError) as error:
        print(f"[ERROR] Review plan was not written: {error}")
        return 2
    print(f"Prepared review plan for {report['headCommit'][:12]}: {len(report['changedFiles'])} changed Git paths, "
          f"{len(report['sourceChanges'])} source changes, {len(report['questionChanges'])} question entries. Production untouched.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
