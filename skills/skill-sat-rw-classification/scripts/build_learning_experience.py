#!/usr/bin/env python3
"""Rebuild SAT practice pages, manifest, and the student learning dashboard."""

from __future__ import annotations

import argparse
import base64
import binascii
import copy
import hashlib
import json
import re
import shutil
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
SKILL = Path(__file__).resolve().parents[1]
PRACTICE_TEMPLATE = SKILL / "assets" / "templates" / "html_template_v1.1.html"
DASHBOARD_TEMPLATE = SKILL / "assets" / "templates" / "learning_index_v2.html"
STORAGE_RUNTIME = SKILL / "assets" / "templates" / "learning_storage.js"
ZEN_KIT = SKILL / "assets" / "zen-interface"
ZEN_ASSETS = ("background.svg", "texture.svg", "pattern.svg")
MIGRATIONS_PATH_NAME = "sat-practice-migrations.json"
CONTENT_REGISTRY_PATH_NAME = "sat-content-registry.json"
CONTENT_REGISTRY_SCHEMA = "sat-content-registry-v1"
CANONICAL_PACKAGE_SCHEMA = "sat-canonical-question-collection-v1"
PARTIAL_ROUTE = "sets/06-2025-06-apac-a-partial.html"
CATEGORY_ROUTES = {
    "Boundaries": "categories/boundaries.html",
    "Central Ideas and Details": "categories/central-ideas-and-details.html",
    "Command of Evidence: Quantitative": "categories/command-of-evidence-quantitative.html",
    "Command of Evidence: Textual": "categories/command-of-evidence-textual.html",
    "Cross-Text Connections": "categories/cross-text-connections.html",
    "Form, Structure, and Sense": "categories/form-structure-and-sense.html",
    "Inferences": "categories/inferences.html",
    "Rhetorical Synthesis": "categories/rhetorical-synthesis.html",
    "Text Structure and Purpose": "categories/text-structure-and-purpose.html",
    "Transitions": "categories/transitions.html",
    "Words in Context": "categories/words-in-context.html",
}
JUNE_PARTIAL_LEGACY_IDS = [
    "jun2025_apac_a_01", "jun2025_apac_a_02", "jun2025_apac_a_03",
    "jun2025_apac_a_04", "jun2025_apac_a_05", "jun2025_apac_a_06",
    "jun2025_apac_a_07", "jun2025_apac_a_08", "jun2025_apac_a_09",
    "jun2025_apac_a_10", "jun2025_apac_a_11", "jun2025_apac_a_12",
    "jun2025_apac_a_14",
]
JUNE_PARTIAL_ALIASES = {
    **{f"jun2025_apac_a_{number:02d}": f"jun2025_apac_a_{number + 1:02d}" for number in range(2, 13)},
    "jun2025_apac_a_14": "jun2025_apac_a_15",
}


def most_common_text(values: list[Any]) -> str:
    normalized = [re.sub(r"\s+", " ", str(value)).strip() for value in values if str(value or "").strip()]
    return Counter(normalized).most_common(1)[0][0] if normalized else ""


def page_title(kind: str, questions: list[dict[str, Any]], page_path: Path) -> str:
    if kind == "set":
        title = most_common_text([
            question.get("_source_set") or question.get("source_set")
            for question in questions
        ])
    else:
        title = most_common_text([
            (question.get("classification") or {}).get("skill")
            for question in questions
            if isinstance(question.get("classification"), dict)
        ])
    return title or page_path.stem.replace("-", " ").title()


def sync_zen_assets(root: Path) -> None:
    target = root / "assets" / "zen-interface"
    target.mkdir(parents=True, exist_ok=True)
    for name in ZEN_ASSETS:
        source = ZEN_KIT / name
        if not source.is_file():
            raise FileNotFoundError(f"Missing Zen Interface production asset: {source}")
        shutil.copy2(source, target / name)


def safe_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")


def content_fingerprint(question: dict[str, Any]) -> str:
    """Punctuation-sensitive identity that intentionally excludes answer state."""
    payload = {
        "passage": question.get("passage") or "",
        "prompt": question.get("prompt") or "",
        "choices": [
            {
                "label": str(choice.get("label") or ""),
                "text": str(choice.get("text") or ""),
            }
            for choice in question.get("choices") or []
            if isinstance(choice, dict)
        ],
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def storage_key(question_ids: list[str]) -> str:
    return "sat-practice:reading_writing:" + "|".join(question_ids)


def load_migrations(root: Path) -> dict[str, list[dict[str, Any]]]:
    base = {
        PARTIAL_ROUTE: [{
            "storageKey": storage_key(JUNE_PARTIAL_LEGACY_IDS),
            "aliases": JUNE_PARTIAL_ALIASES,
        }]
    }
    path = root / MIGRATIONS_PATH_NAME
    if not path.is_file():
        return base
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        return base
    for route, entries in base.items():
        current = value.setdefault(route, [])
        for entry in entries:
            if entry not in current:
                current.append(entry)
    return value


def add_page_migration(
    migrations: dict[str, list[dict[str, Any]]],
    route: str,
    old_questions: list[dict[str, Any]],
    new_questions: list[dict[str, Any]],
) -> None:
    old_ids = [str(question.get("question_id") or "") for question in old_questions]
    new_ids = [str(question.get("question_id") or "") for question in new_questions]
    if old_ids == new_ids:
        return
    canonical_ids_by_fingerprint: dict[str, set[str]] = {}
    for question in new_questions:
        canonical_ids_by_fingerprint.setdefault(content_fingerprint(question), set()).add(
            str(question.get("question_id") or "")
        )
    targets_by_old: dict[str, set[str]] = {}
    for question in old_questions:
        old_id = str(question.get("question_id") or "")
        canonical_ids = canonical_ids_by_fingerprint.get(content_fingerprint(question), set())
        target = next(iter(canonical_ids)) if len(canonical_ids) == 1 else ""
        if old_id and target:
            targets_by_old.setdefault(old_id, set()).add(target)
    # A state already shared by two different questions cannot be split reliably.
    aliases = {
        old_id: next(iter(targets))
        for old_id, targets in targets_by_old.items()
        if len(targets) == 1 and next(iter(targets)) != old_id
    }
    entry = {"storageKey": storage_key(old_ids), "aliases": aliases}
    existing = migrations.setdefault(route, [])
    if entry not in existing:
        existing.append(entry)


def load_content_registry(root: Path) -> dict[str, Any]:
    path = root / CONTENT_REGISTRY_PATH_NAME
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schemaVersion") != CONTENT_REGISTRY_SCHEMA:
        raise ValueError(f"Unsupported SAT content registry: {path}")
    entries = value.get("entries")
    if not isinstance(entries, list) or not all(isinstance(entry, dict) for entry in entries):
        raise ValueError("SAT content registry entries must be an array of objects")
    required = {
        "contentId", "kind", "section", "title", "sourceFamily", "difficulty",
        "dataPath", "route", "moduleSpec", "examEligible", "examVariants", "status",
    }
    seen_ids: set[str] = set()
    seen_routes: set[str] = set()
    for entry in entries:
        missing = sorted(required - set(entry))
        if missing:
            raise ValueError(f"Registry entry is missing {missing}: {entry.get('contentId')}")
        content_id = str(entry.get("contentId") or "")
        route = str(entry.get("route") or "")
        kind = str(entry.get("kind") or "")
        if not content_id or content_id in seen_ids:
            raise ValueError(f"Duplicate or empty contentId: {content_id}")
        if not re.fullmatch(r"(?:sets|collections|categories)/[a-z0-9][a-z0-9-]*\.html", route):
            raise ValueError(f"Unsafe public route in registry: {route}")
        if route in seen_routes:
            raise ValueError(f"Duplicate public route in registry: {route}")
        if kind not in {"set", "collection", "category"}:
            raise ValueError(f"Unsupported content kind for {content_id}: {kind}")
        if entry.get("section") not in {"reading_writing", "math"}:
            raise ValueError(f"Unsupported section for {content_id}: {entry.get('section')}")
        if not isinstance(entry.get("examEligible"), bool):
            raise ValueError(f"examEligible must be boolean for {content_id}")
        if not isinstance(entry.get("examVariants"), list):
            raise ValueError(f"examVariants must be an array for {content_id}")
        if entry.get("examEligible") and not entry.get("moduleSpec"):
            raise ValueError(f"Exam-eligible content requires moduleSpec: {content_id}")
        seen_ids.add(content_id)
        seen_routes.add(route)
    return value


def load_question_package(root: Path, entry: dict[str, Any]) -> list[dict[str, Any]]:
    relative = entry.get("dataPath")
    if not isinstance(relative, str) or not relative:
        raise ValueError(f"Published {entry.get('kind')} has no canonical dataPath: {entry.get('contentId')}")
    if Path(relative).is_absolute() or ".." in Path(relative).parts:
        raise ValueError(f"Canonical dataPath must stay inside the workspace: {relative}")
    path = root / relative
    value = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(value, dict):
        schema = value.get("schemaVersion")
        if schema != CANONICAL_PACKAGE_SCHEMA:
            raise ValueError(f"Unsupported canonical package schema in {relative}: {schema}")
        questions = value.get("questions")
    else:
        # Read-only compatibility for an explicitly registered legacy JSON source.
        questions = value
    if not isinstance(questions, list) or not all(isinstance(item, dict) for item in questions):
        raise ValueError(f"Canonical question source must contain an array of objects: {relative}")

    route = str(entry["route"])
    title = str(entry["title"])
    section = str(entry["section"])
    normalized: list[dict[str, Any]] = []
    for position, raw_question in enumerate(questions, start=1):
        question = copy.deepcopy(raw_question)
        question_id = str(question.get("question_id") or "")
        if not question_id:
            raise ValueError(f"Missing question_id in {relative} at position {position}")
        if question.get("section") != section:
            raise ValueError(
                f"Section mismatch for {question_id}: {question.get('section')} != {section}"
            )
        choices = question.get("choices")
        labels = [str(choice.get("label") or "") for choice in choices or [] if isinstance(choice, dict)]
        if len(labels) != 4 or labels != ["A", "B", "C", "D"]:
            raise ValueError(f"Published question must have four A-D choices: {question_id}")
        if question.get("correct_answer") not in labels:
            raise ValueError(f"Published question has an invalid answer: {question_id}")
        if question.get("needs_manual_review"):
            raise ValueError(f"Review question cannot enter a published package: {question_id}")
        question["_source_set_path"] = route
        question["_source_set"] = title
        question["_display_source_set"] = (
            title
            if entry.get("kind") == "set"
            else str(question.get("source_set") or title)
        )
        question["source_set"] = str(question.get("source_set") or title)
        question["source_family"] = str(entry.get("sourceFamily") or "")
        if not question.get("difficulty_estimate") and entry.get("difficulty") != "mixed":
            question["difficulty_estimate"] = entry.get("difficulty")
        normalized.append(question)
    return normalized


def load_published_content(
    root: Path, registry: dict[str, Any]
) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]], dict[str, dict[str, Any]]]:
    entries = [
        entry for entry in registry["entries"]
        if entry.get("status") == "published"
    ]
    source_entries = [entry for entry in entries if entry.get("kind") in {"set", "collection"}]
    source_pages: dict[str, list[dict[str, Any]]] = {}
    canonical: dict[str, dict[str, Any]] = {}
    fingerprints_by_id: dict[str, str] = {}
    grading_by_id: dict[str, str] = {}
    first_routes_by_id: dict[str, str] = {}
    priorities_by_id: dict[str, int] = {}
    source_kind_order = {"set": 0, "collection": 1}
    for entry in sorted(
        source_entries,
        key=lambda item: (source_kind_order[str(item.get("kind"))], str(item.get("route"))),
    ):
        route = str(entry["route"])
        questions = load_question_package(root, entry)
        ids = [str(question["question_id"]) for question in questions]
        if len(ids) != len(set(ids)):
            raise ValueError(f"Duplicate question ID inside {route}")
        source_pages[route] = questions
        priority = int(entry.get("canonicalPriority") or 100) + (1000 if entry.get("kind") == "set" else 0)
        for question in questions:
            question_id = str(question["question_id"])
            fingerprint = content_fingerprint(question)
            prior_fingerprint = fingerprints_by_id.get(question_id)
            if prior_fingerprint and prior_fingerprint != fingerprint:
                raise ValueError(f"Question ID collision with different content: {question_id}")
            fingerprints_by_id[question_id] = fingerprint
            revision = grading_revision(question)
            prior_revision = grading_by_id.get(question_id)
            if prior_revision is not None and prior_revision != revision:
                raise ValueError(
                    f"Shared question grading fields differ for {question_id}: "
                    f"{first_routes_by_id[question_id]} and {route}. "
                    "Update all occurrences of this question together."
                )
            grading_by_id[question_id] = revision
            first_routes_by_id.setdefault(question_id, route)
            if question_id not in canonical or priority > priorities_by_id[question_id]:
                canonical[question_id] = copy.deepcopy(question)
                priorities_by_id[question_id] = priority
    return entries, source_pages, canonical


def png_dimensions(path: Path) -> tuple[int, int] | None:
    header = path.read_bytes()[:24]
    if len(header) < 24 or header[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    width = int.from_bytes(header[16:20], "big")
    height = int.from_bytes(header[20:24], "big")
    return (width, height) if width > 0 and height > 0 else None


DATA_IMAGE_RE = re.compile(
    r"^data:(image/(?:png|jpeg|webp));base64,([A-Za-z0-9+/=\s]+)$",
    re.IGNORECASE,
)
LOCAL_ASSET_RE = re.compile(r"^(?:\.\./)?[A-Za-z0-9][A-Za-z0-9._/-]*\.(?:png|jpe?g|webp|svg)$")
PUBLIC_PATH_PATTERNS = ("/Users/", "/tmp/", "/private/tmp/")
PUBLIC_INLINE_HTML_RE = re.compile(
    r"<\s*(?P<closing>/?)\s*(?P<tag>i|em|u|b|strong)\s*>",
    re.IGNORECASE,
)
PUBLIC_HTML_TAG_RE = re.compile(r"<\s*/?\s*[A-Za-z][^>]*>", re.IGNORECASE)
PUBLIC_HTML_DECLARATION_RE = re.compile(r"<\s*(?:!|\?)", re.IGNORECASE)
PUBLIC_INLINE_MARKER_RE = re.compile(r"\[\s*(?P<closing>/?)\s*(?P<tag>i|u|b)\s*\]", re.IGNORECASE)
BOUND_MATERIAL_IMAGE_RE = re.compile(
    r'<img\s+class=["\']q_mat_img_here["\']\s+src=["\'](?P<src>[^"\']+)["\']\s*/?>',
    re.IGNORECASE,
)


def normalize_public_inline_markup(value: Any, context: str) -> str:
    """Convert the small audited inline-HTML subset to inert display markers.

    Student pages never receive executable HTML from question data. Italic,
    underline, and bold tags are converted to markers that the template renders
    only after escaping the surrounding text. Any other tag, attribute, comment,
    or malformed marker fails the build instead of being silently exposed.
    """

    text = str(value or "")

    def replace_tag(match: re.Match[str]) -> str:
        tag = match.group("tag").lower()
        tag = "i" if tag == "em" else "b" if tag == "strong" else tag
        return f"[{'/' if match.group('closing') else ''}{tag}]"

    normalized = PUBLIC_INLINE_HTML_RE.sub(replace_tag, text)
    normalized = PUBLIC_INLINE_MARKER_RE.sub(
        lambda match: f"[{'/' if match.group('closing') else ''}{match.group('tag').lower()}]",
        normalized,
    )
    unsafe = PUBLIC_HTML_TAG_RE.search(normalized) or PUBLIC_HTML_DECLARATION_RE.search(normalized)
    if unsafe:
        snippet = normalized[max(0, unsafe.start() - 30): unsafe.end() + 30]
        raise ValueError(f"Unsafe or unsupported HTML in {context}: {snippet!r}")

    stack: list[str] = []
    for marker in PUBLIC_INLINE_MARKER_RE.finditer(normalized):
        tag = marker.group("tag").lower()
        if marker.group("closing"):
            if not stack or stack[-1] != tag:
                raise ValueError(f"Mismatched public inline marker in {context}: {marker.group(0)!r}")
            stack.pop()
        else:
            stack.append(tag)
    if stack:
        raise ValueError(f"Unclosed public inline marker in {context}: {stack[-1]}")
    return normalized


def normalize_public_value(value: Any, context: str) -> Any:
    if isinstance(value, str):
        return normalize_public_inline_markup(value, context)
    if isinstance(value, dict):
        return {
            str(key): normalize_public_value(item, f"{context}.{key}")
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [normalize_public_value(item, f"{context}[{index}]") for index, item in enumerate(value)]
    return copy.deepcopy(value)


def externalize_data_image(root: Path, value: str) -> str:
    match = DATA_IMAGE_RE.fullmatch(value.strip())
    if not match:
        raise ValueError("Only base64 PNG, JPEG, and WebP data images are supported")
    try:
        payload = base64.b64decode(re.sub(r"\s+", "", match.group(2)), validate=True)
    except (binascii.Error, ValueError) as error:
        raise ValueError("Invalid base64 image payload") from error
    if not payload:
        raise ValueError("Empty data image payload")
    mime = match.group(1).lower()
    suffix = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}[mime]
    digest = hashlib.sha256(payload).hexdigest()
    target = root / "assets" / "question-media" / f"{digest}{suffix}"
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file() and target.read_bytes() != payload:
        raise ValueError(f"Content-addressed asset collision: {target}")
    if not target.is_file():
        target.write_bytes(payload)
    return f"../assets/question-media/{target.name}"


def public_asset_reference(root: Path, value: Any) -> str:
    reference = str(value or "").strip()
    if not reference:
        return ""
    if reference.startswith("data:"):
        return externalize_data_image(root, reference)
    if reference.startswith(("http://", "https://", "//")):
        raise ValueError(f"Published SAT pages cannot depend on a remote asset: {reference[:120]}")
    if any(pattern in reference for pattern in PUBLIC_PATH_PATTERNS) or not LOCAL_ASSET_RE.fullmatch(reference):
        raise ValueError(f"Unsafe SAT asset reference: {reference[:160]}")
    return reference


def externalize_bound_material_image(
    root: Path,
    passage: Any,
    assets: Any,
    alt_text: Any,
    question_id: str,
) -> tuple[str, list[dict[str, Any]]]:
    """Consume a source-bound material placeholder without changing canonical text.

    The reviewed question keeps its original ``<img class=q_mat_img_here>``
    placeholder and run-local resource binding. The student DTO receives only a
    content-addressed public image reference. Any mismatch fails closed.
    """

    source = str(passage or "")
    matches = list(BOUND_MATERIAL_IMAGE_RE.finditer(source))
    if not matches:
        return source, []
    if len(matches) != 1:
        raise ValueError(f"{question_id}: expected exactly one bound material image")
    if not isinstance(assets, list):
        raise ValueError(f"{question_id}: material image has no visual_assets binding")

    match = matches[0]
    reference = match.group("src")
    candidates = [
        item for item in assets
        if isinstance(item, dict) and str(item.get("path") or "") == reference
    ]
    if len(candidates) != 1:
        raise ValueError(f"{question_id}: material image binding does not match its src")
    raw_asset = candidates[0]

    relative = Path(reference)
    if relative.is_absolute() or ".." in relative.parts or relative.suffix.lower() != ".png":
        raise ValueError(f"{question_id}: unsafe material image path")
    source_path = (root / relative).resolve()
    try:
        source_path.relative_to(root.resolve())
    except ValueError as error:
        raise ValueError(f"{question_id}: material image escapes the workspace") from error
    if not source_path.is_file():
        raise FileNotFoundError(f"{question_id}: missing material image {reference}")

    payload = source_path.read_bytes()
    if payload[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"{question_id}: bound material image is not PNG")
    digest = hashlib.sha256(payload).hexdigest()
    if str(raw_asset.get("sha256") or "") != f"sha256:{digest}":
        raise ValueError(f"{question_id}: material image SHA-256 mismatch")
    dimensions = png_dimensions(source_path)
    declared_dimensions = (raw_asset.get("width"), raw_asset.get("height"))
    if dimensions is None or declared_dimensions != dimensions:
        raise ValueError(f"{question_id}: material image dimensions mismatch")

    accessible_label = re.sub(r"\s+", " ", str(alt_text or "")).strip()
    if not accessible_label:
        raise ValueError(f"{question_id}: material image requires public_visual_alt_text")

    target = root / "assets" / "question-media" / f"{digest}.png"
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file() and target.read_bytes() != payload:
        raise ValueError(f"{question_id}: content-addressed asset collision")
    if not target.is_file():
        target.write_bytes(payload)

    public_passage = (source[:match.start()] + source[match.end():]).lstrip("\r\n")
    return public_passage, [{
        "type": "graph",
        "alt_text": accessible_label,
        "image_data_uri": f"../assets/question-media/{target.name}",
        "width": dimensions[0],
        "height": dimensions[1],
    }]


def public_visual_assets(root: Path, assets: Any) -> list[dict[str, Any]]:
    if not isinstance(assets, list):
        return []
    scalar_keys = {
        "type", "kind", "title", "label", "description", "alt_text", "caption",
        "chart_type", "chart_description",
    }
    structured_keys = {"columns", "rows", "structured_data", "chart_data"}
    result: list[dict[str, Any]] = []
    for raw_asset in assets:
        if not isinstance(raw_asset, dict):
            continue
        if raw_asset.get("path"):
            # A path-bearing source asset must be consumed by the strictly
            # bound passage placeholder above; silently dropping it would hide
            # required evidence from students.
            continue
        asset = {
            key: copy.deepcopy(raw_asset[key])
            for key in scalar_keys | structured_keys
            if key in raw_asset
        }
        for field in ("image_data_uri", "material_panel_crop_data_uri"):
            if raw_asset.get(field):
                asset[field] = public_asset_reference(root, raw_asset[field])
        if asset:
            result.append(asset)
    return result


def public_question_assets(
    root: Path,
    assets: Any,
    bound_assets: list[dict[str, Any]],
    question_id: str,
) -> list[dict[str, Any]]:
    """Return public assets and reject any unconsumed source-local binding."""

    raw_assets = assets if isinstance(assets, list) else []
    path_assets = [
        item for item in raw_assets
        if isinstance(item, dict) and str(item.get("path") or "").strip()
    ]
    # Historical canonical records also carry source-page/crop paths as
    # internal provenance after their visible table or graph was transcribed
    # into passage/table_data. Those remain intentionally omitted. A bound
    # placeholder, however, is a public-display contract and must resolve to
    # exactly one source asset.
    if bound_assets and len(path_assets) != 1:
        raise ValueError(f"{question_id}: bound material image requires exactly one path asset")
    safe_assets = [
        item for item in raw_assets
        if not (isinstance(item, dict) and str(item.get("path") or "").strip())
    ]
    return bound_assets + public_visual_assets(root, safe_assets)


def public_explanation(value: Any, question_id: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    fields = {
        "short", "key_evidence", "why_correct", "detailed",
        "choice_analysis",
    }
    return {
        key: normalize_public_value(value[key], f"{question_id}.explanation.{key}")
        for key in fields
        if key in value
    }


def public_classification(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    fields = {"domain", "skill", "subtype"}
    return {key: copy.deepcopy(value[key]) for key in fields if key in value}


def public_question(root: Path, route: str, question: dict[str, Any]) -> dict[str, Any]:
    """Build the only question DTO permitted inside student-facing HTML."""

    question_id = str(question.get("question_id") or "")
    choices = [
        {
            "label": str(choice.get("label") or ""),
            "text": normalize_public_inline_markup(
                choice.get("text"), f"{question_id}.choices.{choice.get('label') or ''}"
            ),
        }
        for choice in question.get("choices") or []
        if isinstance(choice, dict)
    ]
    public_passage, bound_assets = externalize_bound_material_image(
        root,
        question.get("passage"),
        question.get("visual_assets"),
        question.get("public_visual_alt_text"),
        question_id,
    )
    result: dict[str, Any] = {
        "question_id": question_id,
        "section": str(question.get("section") or ""),
        "question_number": question.get("question_number"),
        "passage": normalize_public_inline_markup(public_passage, f"{question_id}.passage"),
        "prompt": normalize_public_inline_markup(question.get("prompt"), f"{question_id}.prompt"),
        "choices": choices,
        "correct_answer": str(question.get("correct_answer") or ""),
        "explanation": public_explanation(question.get("explanation"), question_id),
        "classification": public_classification(question.get("classification")),
        "source_set": str(
            question.get("_display_source_set")
            or question.get("source_set")
            or question.get("_source_set")
            or ""
        ),
    }
    for field in (
        "source_question_number", "difficulty_estimate", "skills_tested", "table_data",
        "chart_display_scale", "chart_image_width", "chart_image_height", "math_metadata",
        "module_id", "module_position",
    ):
        if field in question:
            result[field] = copy.deepcopy(question[field])

    assets = public_question_assets(
        root,
        question.get("visual_assets"),
        bound_assets,
        question_id,
    )
    if assets:
        result["visual_assets"] = assets
    chart_ref = str(question.get("chart_image") or "").strip()
    if re.fullmatch(r"chart_images/[A-Za-z0-9._-]+\.png", chart_ref):
        asset = root / "categories" / chart_ref
        if not asset.is_file():
            raise FileNotFoundError(f"Missing shared SAT chart asset: {asset}")
        result["chart_image"] = chart_ref
        result["chart_image_data_uri"] = f"../categories/{chart_ref}"
        dimensions = png_dimensions(asset)
        if dimensions:
            result["chart_image_width"], result["chart_image_height"] = dimensions
    elif question.get("chart_image_data_uri"):
        result["chart_image"] = chart_ref or "externalized-chart"
        result["chart_image_data_uri"] = public_asset_reference(
            root, question.get("chart_image_data_uri")
        )

    serialized = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
    leaked = [pattern for pattern in PUBLIC_PATH_PATTERNS if pattern in serialized]
    if leaked:
        raise ValueError(f"Student DTO contains local paths {leaked}: {result['question_id']}")
    return result


def question_route(question: dict[str, Any], fallback: str) -> str:
    route = str(question.get("_source_set_path") or "").strip()
    if re.fullmatch(r"(?:sets|collections)/[a-z0-9][a-z0-9-]*\.html", route):
        return route
    return fallback


def compact_question(
    question: dict[str, Any],
    fallback_route: str,
    root: Path = ROOT,
) -> dict[str, Any]:
    classification = question.get("classification") if isinstance(question.get("classification"), dict) else {}
    question_id = str(question.get("question_id") or "")
    choices = [
        {
            "label": str(choice.get("label") or ""),
            "text": normalize_public_inline_markup(
                choice.get("text"), f"{question_id}.choices.{choice.get('label') or ''}"
            ),
        }
        for choice in question.get("choices") or []
        if isinstance(choice, dict)
    ]
    public_passage, bound_assets = externalize_bound_material_image(
        root,
        question.get("passage"),
        question.get("visual_assets"),
        question.get("public_visual_alt_text"),
        question_id,
    )
    # The compact manifest does not expose visual metadata, but it must still
    # prove that every source-local asset was consumed by the same strict
    # placeholder binding used for the full student DTO.
    public_question_assets(root, question.get("visual_assets"), bound_assets, question_id)
    return {
        "contentVersion": "sat-learning-v2",
        "gradingRevision": grading_revision(question),
        "questionId": question_id,
        "questionNumber": question.get("question_number"),
        "route": question_route(question, fallback_route),
        "passage": normalize_public_inline_markup(public_passage, f"{question_id}.passage"),
        "prompt": normalize_public_inline_markup(question.get("prompt"), f"{question_id}.prompt"),
        "choices": choices,
        "skill": str(classification.get("skill") or "未分类"),
        "subtype": str(classification.get("subtype") or "未细分"),
        "domain": str(classification.get("domain") or ""),
        "sourceSet": str(
            question.get("_display_source_set")
            or question.get("_source_set")
            or question.get("source_set")
            or ""
        ),
        "sourceFamily": str(question.get("source_family") or ""),
        "difficulty": question.get("difficulty_estimate") or "",
    }


def render_practice_page(
    template: str,
    questions: list[dict[str, Any]],
    title: str,
    page_meta: dict[str, Any],
    page_migrations: list[dict[str, Any]],
    aliases_by_page: dict[str, dict[str, str]],
) -> str:
    return (
        template.replace("__SECTION__", "reading_writing")
        .replace("__SECTION_TITLE__", title)
        .replace("__QUESTIONS_JSON__", safe_json(questions))
        .replace("__PAGE_META_JSON__", safe_json(page_meta))
        .replace("__PAGE_MIGRATIONS_JSON__", safe_json(page_migrations))
        .replace("__QUESTION_ALIASES_BY_PAGE_JSON__", safe_json(aliases_by_page))
    )


def grading_revision(question: dict[str, Any]) -> str:
    payload = {key: question.get(key) for key in ("question_id", "passage", "prompt", "choices", "correct_answer", "explanation")}
    return hashlib.sha256(safe_json(payload).encode("utf-8")).hexdigest()


def build(root: Path) -> dict[str, Any]:
    storage_runtime = STORAGE_RUNTIME.read_text(encoding="utf-8")
    practice_template = PRACTICE_TEMPLATE.read_text(encoding="utf-8").replace("__SAT_STORAGE_RUNTIME__", storage_runtime)
    dashboard_template = DASHBOARD_TEMPLATE.read_text(encoding="utf-8").replace("__SAT_STORAGE_RUNTIME__", storage_runtime)
    pages: list[dict[str, Any]] = []
    migrations = load_migrations(root)
    registry = load_content_registry(root)
    published_entries, source_pages, canonical_questions = load_published_content(root, registry)
    entries_by_route = {str(entry["route"]): entry for entry in published_entries}
    category_entries = [entry for entry in published_entries if entry.get("kind") == "category"]
    registered_categories = {str(entry["title"]): str(entry["route"]) for entry in category_entries}
    if registered_categories != CATEGORY_ROUTES:
        raise ValueError(
            "Published Reading and Writing categories must match the canonical 11-skill routes"
        )
    grouped_categories: dict[str, list[dict[str, Any]]] = {route: [] for route in CATEGORY_ROUTES.values()}
    for question in canonical_questions.values():
        if question.get("section") != "reading_writing":
            continue
        skill = str((question.get("classification") or {}).get("skill") or "")
        route = CATEGORY_ROUTES.get(skill)
        if not route:
            raise ValueError(f"Unsupported SAT skill for {question.get('question_id')}: {skill}")
        grouped_categories[route].append(copy.deepcopy(question))
    for route, questions in grouped_categories.items():
        questions.sort(key=lambda question: str(question.get("question_id") or ""))

    rendered_pages = {**source_pages, **grouped_categories}
    for route, questions in rendered_pages.items():
        baseline_ids = entries_by_route[route].get("baselineQuestionIds")
        current_ids = [str(question.get("question_id") or "") for question in questions]
        if isinstance(baseline_ids, list) and baseline_ids != current_ids:
            migration = {
                "storageKey": storage_key([str(question_id) for question_id in baseline_ids]),
                "aliases": {},
            }
            existing = migrations.setdefault(route, [])
            if migration not in existing:
                existing.append(migration)
    aliases_by_page = {
        route[:-5]: {
            old_id: target
            for migration in page_migrations
            for old_id, target in migration.get("aliases", {}).items()
        }
        for route, page_migrations in migrations.items()
    }

    kind_order = {"set": 0, "collection": 1, "category": 2}
    ordered_entries = sorted(
        (entry for entry in published_entries if str(entry["route"]) in rendered_pages),
        key=lambda entry: (kind_order[str(entry["kind"])], str(entry["route"])),
    )
    for entry in ordered_entries:
        route = str(entry["route"])
        page_path = root / route
        questions = rendered_pages[route]
        public_questions = [public_question(root, route, question) for question in questions]
        for public, original in zip(public_questions, questions):
            public["gradingRevision"] = grading_revision(original)
        kind = str(entry["kind"])
        label = str(entry["title"])
        page_meta = {
            "contentId": str(entry["contentId"]),
            "kind": kind,
            "route": route,
            "title": label,
            "section": str(entry["section"]),
            "difficulty": entry.get("difficulty"),
            "sourceFamily": str(entry.get("sourceFamily") or ""),
            "moduleSpec": copy.deepcopy(entry.get("moduleSpec")),
            "examEligible": bool(entry.get("examEligible")),
            "examVariants": copy.deepcopy(entry.get("examVariants") or []),
        }
        pages.append({
            # pageId is a public local/cloud storage key and remains route-based.
            "pageId": route[:-5],
            **page_meta,
            "kind": kind,
            "questionCount": len(questions),
            "questionIds": [
                str(question.get("question_id"))
                for question in questions
                if question.get("question_id")
            ],
        })
        page_path.parent.mkdir(parents=True, exist_ok=True)
        page_path.write_text(
            render_practice_page(
                practice_template,
                public_questions,
                label,
                page_meta,
                migrations.get(route, []),
                aliases_by_page,
            ),
            encoding="utf-8",
        )

    canonical = {
        question_id: compact_question(
            question,
            str(question.get("_source_set_path") or ""),
            root,
        )
        for question_id, question in canonical_questions.items()
    }

    manifest = {
        "version": "sat-learning-v3",
        "registrySchemaVersion": registry["schemaVersion"],
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "uniqueQuestionCount": len(canonical),
        "pageCount": len(pages),
        "pages": pages,
        "questions": list(canonical.values()),
        "questionAliasesByPage": aliases_by_page,
        "storageMigrations": {
            **{migration["storageKey"]: {"aliases": migration.get("aliases", {})}
               for entries in migrations.values() for migration in entries},
            **{storage_key(page["questionIds"]): {"aliases": {}} for page in pages},
        },
    }
    (root / "sat-practice-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (root / "index.html").write_text(
        dashboard_template.replace("__SAT_MANIFEST_JSON__", safe_json(manifest)), encoding="utf-8"
    )
    (root / MIGRATIONS_PATH_NAME).write_text(
        json.dumps(migrations, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    sync_zen_assets(root)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    manifest = build(args.root.resolve())
    print(
        f"Built {manifest['pageCount']} practice pages and "
        f"{manifest['uniqueQuestionCount']} unique manifest questions."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
