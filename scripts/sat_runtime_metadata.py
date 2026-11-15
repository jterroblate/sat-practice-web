"""Strict, read-only canonical binding for optional SAT runtime metadata v1.

This module does not trust grading hashes, HTML, summaries, or migration aliases
as their own evidence. Legacy payloads without this optional contract remain
supported. New metadata must be complete and exactly canonical/source-bound.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

REVISION_FIELDS = ("question_id", "passage", "prompt", "choices", "correct_answer", "explanation")
# Exact existing migration source preserved by the authorized Oct 24 release.
# New migration rules require an explicit reviewed contract revision, not an
# automatic acceptance of whatever a mutable workspace currently contains.
APPROVED_MIGRATIONS_SHA256 = "d7c9070bc686d1e3c0ed73c47d5a0b4158ee27ffe356c0e76da7cd2a9706147f"


def grading_revision(question: dict[str, Any]) -> str:
    value = {key: question.get(key) for key in REVISION_FIELDS}
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).replace("</", "<\\/")
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def runtime_metadata_present(manifest: dict[str, Any], payloads: dict[str, list[dict]] | None = None,
                             html_sources: list[str] | None = None) -> bool:
    compact = manifest.get("questions")
    compact = compact if isinstance(compact, list) else []
    return ("storageMigrations" in manifest
            or any(isinstance(q, dict) and "gradingRevision" in q for q in compact)
            or any(isinstance(q, dict) and "gradingRevision" in q
                   for questions in (payloads or {}).values() if isinstance(questions, list) for q in questions)
            # Runtime capability is evidence too. Stripping every metadata field
            # must not downgrade an owner-scoped runtime into legacy validation.
            or any(all(marker in html for marker in (
                "function createSatLearningStore()", '"sat-owner:v1:"',
                "const learningStorage = createSatLearningStore();"))
                   for html in (html_sources or [])))


def canonical_revision_contract(root: Path, registry: dict[str, Any]) -> tuple[dict[str, dict[str, str]], dict[str, str]]:
    """Reconstruct per-route + selected canonical revisions without a builder."""
    by_route: dict[str, dict[str, str]] = {}
    canonical: dict[str, dict] = {}
    priorities: dict[str, int] = {}
    entries = registry.get("entries")
    if not isinstance(entries, list) or not all(isinstance(entry, dict) for entry in entries):
        raise ValueError("Invalid registry entries for runtime metadata")
    entries = [entry for entry in entries if entry.get("status") == "published"]
    sources = [entry for entry in entries if entry.get("kind") in {"set", "collection"}]
    for entry in sorted(sources, key=lambda item: (0 if item["kind"] == "set" else 1, item["route"])):
        relative = Path(str(entry["dataPath"]))
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Unsafe canonical dataPath in runtime metadata contract")
        source = root / relative
        if source.is_symlink() or not source.resolve().is_relative_to(root.resolve()):
            raise ValueError("Canonical runtime metadata source escapes root")
        package = json.loads(source.read_text(encoding="utf-8"))
        questions = package.get("questions") if isinstance(package, dict) else package
        if not isinstance(questions, list) or not all(isinstance(q, dict) for q in questions):
            raise ValueError("Canonical runtime metadata questions are invalid")
        route_map = {}
        priority = int(entry.get("canonicalPriority") or 100) + (1000 if entry["kind"] == "set" else 0)
        for question in questions:
            qid = question.get("question_id")
            if not isinstance(qid, str) or not qid or qid in route_map:
                raise ValueError("Duplicate/missing canonical runtime question ID")
            route_map[qid] = grading_revision(question)
            if qid not in canonical or priority > priorities[qid]:
                canonical[qid] = question
                priorities[qid] = priority
        by_route[entry["route"]] = route_map
    for entry in entries:
        if entry.get("kind") == "category":
            by_route[entry["route"]] = {
                qid: grading_revision(q) for qid, q in canonical.items()
                if q.get("section") == "reading_writing" and q.get("classification", {}).get("skill") == entry["title"]
            }
    return by_route, {qid: grading_revision(q) for qid, q in canonical.items()}


def expected_storage_migrations(root: Path, pages: list[dict]) -> dict[str, dict]:
    payload = (root / "sat-practice-migrations.json").read_bytes()
    if hashlib.sha256(payload).hexdigest() != APPROVED_MIGRATIONS_SHA256:
        raise ValueError("Legacy storage migration source is not the approved byte-bound contract")
    migrations = json.loads(payload)
    result = {entry["storageKey"]: {"aliases": entry.get("aliases", {})}
              for entries in migrations.values() for entry in entries}
    result.update({"sat-practice:" + page["section"] + ":" + "|".join(page["questionIds"]): {"aliases": {}}
                   for page in pages})
    return result


def audit_runtime_metadata(root: Path, registry: dict, manifest: dict,
                           payloads: dict[str, list[dict]], gate) -> None:
    try:
        # These paths come from discovered public pages, not an arbitrary URL.
        # Defend the helper independently when called outside the main gate.
        runtime_sources = []
        for route in ["index.html", *payloads]:
            source = root / route
            if not source.resolve().is_relative_to(root.resolve()):
                raise ValueError("Runtime metadata page escapes root")
            if source.is_file():
                runtime_sources.append(source.read_text(encoding="utf-8"))
        if not runtime_metadata_present(manifest, payloads, runtime_sources):
            gate.detail("runtime_metadata_binding", mode="legacy-without-runtime-metadata")
            return
        route_revisions, compact_revisions = canonical_revision_contract(root, registry)
        expected_migrations = expected_storage_migrations(root, manifest["pages"])
    except (ValueError, TypeError, KeyError, OSError) as error:
        gate.fail("runtime_metadata_binding", "runtime_contract_source_invalid", str(error))
        return
    checked = 0
    for route, questions in payloads.items():
        expected = route_revisions.get(route, {})
        for question in questions:
            if not isinstance(question, dict):
                continue
            qid = question.get("question_id")
            if not qid or qid not in expected or question.get("gradingRevision") != expected[qid]:
                gate.fail("runtime_metadata_binding", "question_grading_revision_not_canonical",
                          "Question gradingRevision differs from the canonical question/answer/explanation hash",
                          path=route, context={"questionId": qid})
            checked += 1
    for question in manifest.get("questions", []) or []:
        if not isinstance(question, dict):
            continue
        qid = question.get("questionId")
        if not qid or qid not in compact_revisions or question.get("gradingRevision") != compact_revisions[qid]:
            gate.fail("runtime_metadata_binding", "manifest_grading_revision_not_canonical",
                      "Manifest gradingRevision differs from its selected canonical source",
                      path="sat-practice-manifest.json", context={"questionId": qid})
    if manifest.get("storageMigrations") != expected_migrations:
        gate.fail("runtime_metadata_binding", "storage_migrations_not_source_bound",
                  "Manifest storageMigrations differs from the approved exact-key migration contract",
                  path="sat-practice-manifest.json")
    gate.detail("runtime_metadata_binding", mode="canonical-bound-v1", checkedQuestionOccurrences=checked,
                migrationSourceSha256=APPROVED_MIGRATIONS_SHA256)
