#!/usr/bin/env python3
"""Build original development examples with the unchanged SAT page generator.

Inputs are repository-owned synthetic fixtures. Output stays in build/ and has
no production authentication, cloud credentials, or deployment step.
"""

from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import sys

from build_output import fresh_output

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
BUILDER_PATH = (
    ROOT / "skills" / "skill-sat-rw-classification" / "scripts"
    / "build_learning_experience.py"
)
FIXTURE_PATH = ROOT / "fixtures" / "original-demo.json"
OUTPUT = ROOT / "build/demo"
SET_ROUTE = "sets/demo-original.html"
COLLECTION_ROUTE = "collections/demo-words-in-context.html"
SOURCE_FAMILY = "original-development-fixture"


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_builder():
    spec = importlib.util.spec_from_file_location("sat_demo_builder", BUILDER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("The repository page generator is missing")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _build_into(output: Path) -> dict:
    builder = load_builder()
    package = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    questions = package["questions"]
    if len(questions) != 11 or len({q["question_id"] for q in questions}) != 11:
        raise ValueError("The demo must have exactly 11 distinct original questions")
    if {q["classification"]["skill"] for q in questions} != set(builder.CATEGORY_ROUTES):
        raise ValueError("The demo must cover every registered skill exactly once")
    if any(not q["question_id"].startswith("demo_original_") for q in questions):
        raise ValueError("Only original development fixtures belong in this demo")

    write_json(output / "content/canonical/sets/demo-original.json", package)
    collection_questions = [copy.deepcopy(q) for q in questions
                            if q["classification"]["skill"] == "Words in Context"]
    collection = {
        "schemaVersion": package["schemaVersion"],
        "contentId": "demo-original-words-in-context",
        "questionCount": 1,
        "fixtureNotice": package["fixtureNotice"],
        "questions": collection_questions,
    }
    write_json(output / "content/canonical/collections/demo-words-in-context.json", collection)

    def entry(content_id: str, kind: str, title: str, route: str,
              data_path: str | None, ids: list[str] | None = None) -> dict:
        value = {
            "contentId": content_id,
            "kind": kind,
            "section": "reading_writing",
            "title": title,
            "sourceFamily": SOURCE_FAMILY,
            "difficulty": "mixed",
            "dataPath": data_path,
            "route": route,
            "moduleSpec": None,
            "examEligible": False,
            "examVariants": [],
            "status": "published",
        }
        if ids is not None:
            value["questionIds"] = ids
        return value

    entries = [
        entry("demo-original-set", "set", "Original development examples (11 skills)",
              SET_ROUTE, "content/canonical/sets/demo-original.json",
              [q["question_id"] for q in questions]),
        entry("demo-original-words-in-context", "collection",
              "Original Words in Context development example", COLLECTION_ROUTE,
              "content/canonical/collections/demo-words-in-context.json",
              [q["question_id"] for q in collection_questions]),
    ]
    entries.extend(
        entry("demo-category-" + Path(route).stem, "category", skill, route, None)
        for skill, route in builder.CATEGORY_ROUTES.items()
    )
    write_json(output / "sat-content-registry.json", {
        "schemaVersion": "sat-content-registry-v1",
        "fixtureNotice": package["fixtureNotice"],
        "entries": entries,
    })
    # The existing builder supplies its compatibility defaults. These are code
    # contracts, not imported student state or production content.
    write_json(output / "sat-practice-migrations.json", {})
    return builder.build(output)


def build_demo() -> dict:
    with fresh_output(ROOT, "demo") as output:
        return _build_into(output)


def main() -> int:
    manifest = build_demo()
    print(f"Built original demo: {manifest['uniqueQuestionCount']} unique examples, "
          f"{manifest['pageCount']} practice routes (1 set, 1 collection, 11 categories).")
    print("Preview: python3 -m http.server --bind 127.0.0.1 --directory build/demo 8000")
    print("Open http://127.0.0.1:8000/ — development fixtures, not an official examination.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
