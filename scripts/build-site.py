#!/usr/bin/env python3
"""Build the complete public content copy into ignored build/site/."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import re
import shutil
import sys

from build_output import fresh_output

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "build/site"
BUILDER_PATH = ROOT / "skills/skill-sat-rw-classification/scripts/build_learning_experience.py"
QUESTION_METADATA = "sat-question-metadata.json"


def write_question_metadata(output: Path) -> Path | None:
    """Give the builder each question's origin and difficulty from content/db.

    The set files alone still build the site; without the tables the pages just
    carry no origin.
    """
    if not (ROOT / "content/db/schema.sql").is_file() or not (ROOT / "scripts/content_db.py").is_file():
        return None
    import content_db
    tables, problems = content_db.read_tables(ROOT)
    if problems:
        raise ValueError("content/db is not readable: " + problems[0])
    target = output / QUESTION_METADATA
    target.write_text(json.dumps(content_db.question_metadata(tables, ROOT), ensure_ascii=False), encoding="utf-8")
    return target


def content_resources(value: object):
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "chart_image" and isinstance(item, str) and item.startswith("chart_images/"):
                yield "categories/" + item
            elif key in {"path", "image_data_uri", "chart_image_data_uri", "material_panel_crop_data_uri"} and isinstance(item, str) and item:
                if not item.startswith("data:"):
                    yield item[3:] if item.startswith("../") else item
            else:
                yield from content_resources(item)
    elif isinstance(value, list):
        for item in value:
            yield from content_resources(item)


def _build_into(output: Path) -> dict:
    registry = json.loads((ROOT / "sat-content-registry.json").read_text(encoding="utf-8"))
    shutil.copyfile(ROOT / "sat-content-registry.json", output / "sat-content-registry.json")
    shutil.copyfile(ROOT / "sat-practice-migrations.json", output / "sat-practice-migrations.json")
    resources = set()
    for entry in registry["entries"]:
        if entry["status"] != "published" or entry["kind"] not in {"set", "collection"}:
            continue
        relative = Path(entry["dataPath"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Canonical data path must stay inside the checkout")
        destination = output / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, destination)
        resources.update(content_resources(json.loads((ROOT / relative).read_text(encoding="utf-8"))))
    for relative in sorted(resources):
        path = Path(relative)
        if path.is_absolute() or ".." in path.parts or not re.fullmatch(
            r"(?:assets/question-media|categories/chart_images)/[A-Za-z0-9._-]+\.(?:png|jpe?g|webp|svg)", relative
        ):
            raise ValueError("Content resource is outside the public asset directories")
        source = ROOT / path
        if source.is_symlink() or not source.resolve().is_relative_to(ROOT):
            raise ValueError("Content resource escapes the checkout")
        destination = output / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
    metadata = write_question_metadata(output)
    spec = importlib.util.spec_from_file_location("sat_public_site_builder", BUILDER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("The repository page generator is missing")
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    manifest = builder.build(output)
    if metadata is not None:
        metadata.unlink()  # an input to the pages, not part of the site
    return manifest


def build_site() -> dict:
    with fresh_output(ROOT, "site") as output:
        return _build_into(output)


def main() -> int:
    manifest = build_site()
    print(f"Built full development site: {manifest['uniqueQuestionCount']} unique questions, "
          f"{manifest['pageCount']} routes. Formal examinations remain disabled.")
    print("Preview: python3 -m http.server --bind 127.0.0.1 --directory build/site 8000")
    print("This build has no production authentication or deployment step.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
