#!/usr/bin/env python3
"""Export explicitly supplied maintained content into this public development repo.

This is a one-way, field-whitelisted export. It does not modify the supplied
source, preserve private review evidence, or deploy the resulting pages.
"""

from __future__ import annotations

import argparse
import base64
from collections import Counter
import copy
import hashlib
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
REGISTRY_FIELDS = {
    "contentId", "kind", "section", "title", "sourceFamily", "difficulty",
    "dataPath", "route", "moduleSpec", "examEligible", "examVariants", "status",
    "baselineQuestionIds", "canonicalPriority", "questionIds",
}
QUESTION_FIELDS = {
    "question_id", "section", "question_number", "passage", "prompt", "choices",
    "correct_answer", "needs_manual_review", "source_set", "source_question_number",
    "difficulty_estimate", "skills_tested", "table_data", "chart_display_scale",
    "chart_image_width", "chart_image_height", "math_metadata", "module_id",
    "module_position", "chart_image", "chart_image_data_uri", "public_visual_alt_text",
}
EXPLANATION_FIELDS = {"short", "key_evidence", "why_correct", "detailed", "choice_analysis"}
CLASSIFICATION_FIELDS = {"domain", "skill", "subtype"}
VISUAL_FIELDS = {
    "type", "kind", "title", "label", "description", "alt_text", "caption",
    "chart_type", "chart_description", "columns", "rows", "structured_data",
    "chart_data", "image_data_uri", "material_panel_crop_data_uri",
}
GRADING_FIELDS = ("question_id", "passage", "prompt", "choices", "correct_answer", "explanation")
WATERMARK_IDS = {
    "apac_c_022", "apac_c_023", "apac_c_024", "apac_c_025",
    "apac_c_027", "apac_c_028", "apac_c_029", "apac_c_030",
    "apac_c_049", "apac_c_050", "apac_c_051", "apac_c_052",
    "apac_c_053", "apac_c_054",
}
WATERMARK_SUFFIX = re.compile(r" \([^()\s]+@[^()\s]+\.[A-Za-z]{2,}\)$")
BOUND_IMAGE = re.compile(
    r'<img\s+class=["\']q_mat_img_here["\']\s+src=["\'](?P<src>[^"\']+)["\']\s*/?>',
    re.IGNORECASE,
)
DATA_IMAGE = re.compile(r"data:(image/(?:png|jpeg|webp));base64,([A-Za-z0-9+/=\s]+)", re.IGNORECASE)
EMAIL_LIKE = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")
LOCAL_PATH = re.compile(r"(?:/Users/|/private/tmp/|/tmp/|[A-Za-z]:\\Users\\)")


def encoded(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def grading_revision(question: dict) -> str:
    payload = {key: question.get(key) for key in GRADING_FIELDS}
    value = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).replace("</", "<\\/")
    return sha(value.encode("utf-8"))


def identity_answers(questions: list[dict]) -> str:
    value = [[q["question_id"], q["correct_answer"]] for q in questions]
    return sha(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def safe_relative(value: str) -> str:
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts or "\\" in value:
        raise ValueError("Export input contains an unsafe relative path")
    return relative.as_posix()


def export_content(source: Path) -> dict:
    source = source.resolve()
    if source == ROOT or source.is_relative_to(ROOT):
        raise ValueError("The maintained source must be separate from this export checkout")
    source_registry_bytes = (source / "sat-content-registry.json").read_bytes()
    registry = json.loads(source_registry_bytes)
    entries = [copy.deepcopy(e) for e in registry["entries"] if e["status"] == "published"]
    counts = Counter(e["kind"] for e in entries)
    if counts != {"set": 34, "collection": 3, "category": 11}:
        raise ValueError("This reviewed export expects 34 sets, 3 collections, and 11 categories")
    if any(e["examEligible"] or e["examVariants"] for e in entries):
        raise ValueError("This development export must not enable formal examinations")

    outputs: dict[str, bytes] = {}
    resource_manifest: dict[str, dict] = {}
    package_reports = []
    grading_changes = []
    watermark_changes = []
    removed_fields = Counter()
    source_occurrences = []
    public_occurrences = []

    def add_resource(relative: str, payload: bytes) -> str:
        relative = safe_relative(relative)
        previous = outputs.get(relative)
        if previous is not None and previous != payload:
            raise ValueError("Two exported resources claim the same public path")
        outputs[relative] = payload
        resource_manifest[relative] = {"publicPath": relative, "sha256": sha(payload), "sizeBytes": len(payload)}
        return relative

    def source_bytes(relative: str) -> bytes:
        candidate = source / safe_relative(relative)
        if candidate.is_symlink() or not candidate.resolve().is_relative_to(source):
            raise ValueError("Export resource escapes the maintained source")
        return candidate.read_bytes()

    def externalize_reference(reference: str) -> str:
        if reference.startswith("data:"):
            match = DATA_IMAGE.fullmatch(reference)
            if match is None:
                raise ValueError("Unsupported embedded content image")
            payload = base64.b64decode(re.sub(r"\s+", "", match.group(2)), validate=True)
            suffix = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}[match.group(1).lower()]
            target = add_resource("assets/question-media/" + sha(payload) + suffix, payload)
            return "../" + target
        if reference.startswith(("https://", "http://", "//")):
            raise ValueError("Public content must not depend on remote images")
        relative = reference[3:] if reference.startswith("../") else reference
        relative = safe_relative(relative)
        if not relative.startswith(("assets/", "categories/chart_images/")):
            raise ValueError("A visible resource is outside the public content directories")
        add_resource(relative, source_bytes(relative))
        return "../" + relative

    for entry in entries:
        if entry["kind"] not in {"set", "collection"}:
            continue
        public_path = safe_relative(entry["dataPath"])
        if not public_path.startswith("content/canonical/"):
            raise ValueError("Expected a registered canonical JSON package")
        original_bytes = source_bytes(public_path)
        original_package = json.loads(original_bytes)
        original_questions = original_package["questions"]
        public_questions = []
        for original in original_questions:
            qid = original["question_id"]
            question = {key: copy.deepcopy(value) for key, value in original.items() if key in QUESTION_FIELDS}
            removed_fields.update(set(original) - QUESTION_FIELDS - {"explanation", "classification", "visual_assets"})
            question["explanation"] = {key: copy.deepcopy(value) for key, value in original["explanation"].items() if key in EXPLANATION_FIELDS}
            question["classification"] = {key: copy.deepcopy(value) for key, value in original["classification"].items() if key in CLASSIFICATION_FIELDS}
            reasons = []
            private_explanation_keys = sorted(set(original["explanation"]) - EXPLANATION_FIELDS)
            if private_explanation_keys:
                reasons.append("internal-explanation-metadata-removed")
            if qid in WATERMARK_IDS:
                prompt, replacements = WATERMARK_SUFFIX.subn("", question["prompt"])
                if replacements != 1:
                    raise ValueError("An approved source watermark is missing or has changed")
                question["prompt"] = prompt
                reasons.append("trailing-source-email-watermark-removed")
                watermark_changes.append({"questionId": qid, "publicPromptSha256": sha(prompt.encode("utf-8"))})

            bound = BOUND_IMAGE.search(question["passage"])
            raw_assets = original.get("visual_assets") or []
            public_assets = []
            if bound:
                reference = bound.group("src")
                matches = [asset for asset in raw_assets if asset.get("path") == reference]
                if len(matches) != 1:
                    raise ValueError("Visible bound material requires one exact resource binding")
                asset = matches[0]
                payload = source_bytes(reference)
                digest = sha(payload)
                if payload[:8] != b"\x89PNG\r\n\x1a\n" or asset.get("sha256") != "sha256:" + digest:
                    raise ValueError("Visible bound material SHA or PNG signature mismatch")
                dimensions = (int.from_bytes(payload[16:20], "big"), int.from_bytes(payload[20:24], "big"))
                if dimensions != (asset.get("width"), asset.get("height")):
                    raise ValueError("Visible bound material dimensions mismatch")
                target = add_resource("assets/question-media/" + digest + ".png", payload)
                question["passage"] = question["passage"][:bound.start("src")] + target + question["passage"][bound.end("src"):]
                public_assets.append({"type": asset.get("type", "chart"), "path": target,
                                      "sha256": "sha256:" + digest,
                                      "width": dimensions[0], "height": dimensions[1]})
                reasons.append("visible-material-reference-relocated")
            for asset in raw_assets:
                # Historical source-page/crop references were never rendered by
                # the maintained generator. Only the exact visible binding above
                # and the generator's non-path display fields are exported.
                if asset.get("path"):
                    continue
                public_asset = {key: copy.deepcopy(value) for key, value in asset.items() if key in VISUAL_FIELDS}
                for field in ("image_data_uri", "material_panel_crop_data_uri"):
                    if public_asset.get(field):
                        public_asset[field] = externalize_reference(public_asset[field])
                if public_asset:
                    public_assets.append(public_asset)
            if public_assets:
                question["visual_assets"] = public_assets
            if question.get("chart_image_data_uri"):
                question["chart_image_data_uri"] = externalize_reference(question["chart_image_data_uri"])
            chart = question.get("chart_image", "")
            if re.fullmatch(r"chart_images/[A-Za-z0-9._-]+\.png", chart):
                relative = "categories/" + chart
                add_resource(relative, source_bytes(relative))

            source_grade, public_grade = grading_revision(original), grading_revision(question)
            if source_grade != public_grade:
                grading_changes.append({
                    "contentId": entry["contentId"], "questionId": qid,
                    "sourceGradingRevision": source_grade, "publicGradingRevision": public_grade,
                    "reasons": reasons, "removedExplanationFieldNames": private_explanation_keys,
                })
            if question["question_id"] != original["question_id"] or question["correct_answer"] != original["correct_answer"]:
                raise ValueError("Export must preserve all question IDs and answer keys")
            if question["choices"] != original["choices"]:
                raise ValueError("Export must preserve every answer choice")
            for key in EXPLANATION_FIELDS:
                if question["explanation"].get(key) != original["explanation"].get(key):
                    raise ValueError("Export must preserve all student-facing explanation fields")
            public_questions.append(question)
        public_package = {
            "schemaVersion": "sat-canonical-question-collection-v1",
            "contentId": entry["contentId"], "title": entry["title"],
            "section": entry["section"], "questionCount": len(public_questions),
            "questions": public_questions,
        }
        payload = encoded(public_package)
        source_identity, public_identity = identity_answers(original_questions), identity_answers(public_questions)
        if source_identity != public_identity:
            raise ValueError("Ordered question identity or answer keys changed")
        outputs[public_path] = payload
        package_reports.append({
            "contentId": entry["contentId"], "publicDataPath": public_path,
            "questionCount": len(public_questions),
            "sourcePackageSha256": sha(original_bytes), "publicPackageSha256": sha(payload),
            "sourceIdentityAnswerSha256": source_identity, "publicIdentityAnswerSha256": public_identity,
        })
        source_occurrences.extend(original_questions)
        public_occurrences.extend(public_questions)

    unique = {q["question_id"] for q in public_occurrences}
    if len(unique) != 1752 or len(public_occurrences) != 1765:
        raise ValueError("The reviewed current content count changed")
    if {item["questionId"] for item in watermark_changes} != WATERMARK_IDS:
        raise ValueError("Approved source-watermark cleanup coverage changed")
    public_registry = {
        "schemaVersion": registry["schemaVersion"],
        "entries": [{key: copy.deepcopy(value) for key, value in entry.items() if key in REGISTRY_FIELDS}
                    for entry in entries],
    }
    outputs["sat-content-registry.json"] = encoded(public_registry)
    migrations = (source / "sat-practice-migrations.json").read_bytes()
    outputs["sat-practice-migrations.json"] = migrations
    resource_catalog_path = ROOT / "assets/content-resources.json"
    catalog = {}
    if resource_catalog_path.is_file():
        existing = json.loads(resource_catalog_path.read_text(encoding="utf-8"))
        for item in existing["resources"]:
            catalog[item["publicPath"]] = item
    for relative, item in resource_manifest.items():
        previous = catalog.get(relative)
        if previous is not None and previous != item:
            raise ValueError("A maintained resource catalog entry differs from this export; review it explicitly before replacing content")
        catalog[relative] = item
    # Seed the first import and add genuinely new paths without overwriting
    # resource entries maintained by later reviewed content changes.
    outputs["assets/content-resources.json"] = encoded({"resources": [catalog[key] for key in sorted(catalog)]})
    report = {
        "schemaVersion": "sat-public-content-export-v1",
        "purpose": "Public collaborative development copy; not a production review attestation or deployable authenticated release.",
        "summary": {
            "sets": 34, "collections": 3, "categories": 11, "routes": 48,
            "canonicalPackages": 37, "questionOccurrences": 1765, "uniqueQuestions": 1752,
            "formalExamEligibleRoutes": 0, "contentResources": len(resource_manifest),
            "sourceWatermarksRemoved": len(watermark_changes),
            "gradingChangedOccurrences": len(grading_changes),
            "gradingChangedUniqueQuestions": len({item["questionId"] for item in grading_changes}),
            "answerKeysChanged": 0, "questionIdsChanged": 0,
        },
        "sourceRegistrySha256": sha(source_registry_bytes),
        "publicRegistrySha256": sha(outputs["sat-content-registry.json"]),
        "migrationSha256": sha(migrations), "migrationsPreservedByteForByte": True,
        "fieldPolicy": {
            "questionFieldNames": sorted(QUESTION_FIELDS | {"explanation", "classification", "visual_assets"}),
            "studentExplanationFieldNames": sorted(EXPLANATION_FIELDS),
            "classificationFieldNames": sorted(CLASSIFICATION_FIELDS),
            "excluded": ["source files", "source PDFs", "review results", "audit images", "private model metadata", "local absolute paths", "user records", "credentials"],
            "sourceFieldRemovalCounts": dict(sorted(removed_fields.items())),
        },
        "packages": package_reports,
        "resources": [resource_manifest[key] for key in sorted(resource_manifest)],
        "sourceWatermarkCleanup": watermark_changes,
        "gradingRevisionChanges": grading_changes,
        "compatibilityNote": "Public grading revisions are recomputed from the sanitized canonical copy. Merge by stable ID and reviewed field-level diffs; do not overwrite private production source/evidence or force old grading hashes.",
    }
    outputs["docs/content-export.json"] = encoded(report)

    for relative, payload in outputs.items():
        if relative.endswith(".json"):
            text = payload.decode("utf-8")
            if LOCAL_PATH.search(text) or EMAIL_LIKE.search(text):
                raise ValueError("A public JSON still contains a local path or email-shaped text")
        destination = ROOT / relative
        if destination.is_symlink():
            raise ValueError("An export target is a symlink")
    for relative, payload in outputs.items():
        destination = ROOT / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(payload)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path,
                        help="Explicit local maintained workspace; this path is not written to the export report")
    args = parser.parse_args()
    report = export_content(args.source)
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print("Public content and path-free export report written. Maintained source was read only.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
