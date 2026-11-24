#!/usr/bin/env python3
"""Build the public content copy into ignored build/site/ (or build/site-preview/).

    python3 scripts/build-site.py                 what sat-release.json releases: build/site/
    python3 scripts/build-site.py --preview-all   every release feature on, for review: build/site-preview/

sat-release.json switches on what goes beyond the reviewed baseline: the
expanded question bank, the fixed mock exams, the AI-drafted word explanations
and the AI-drafted walkthroughs. Switching one on is the content owner's
decision; the preview build exists so the owner can look before deciding.
"""

from __future__ import annotations

import argparse
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
RELEASE = "sat-release.json"


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


def load_builder():
    spec = importlib.util.spec_from_file_location("sat_public_site_builder", BUILDER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("The repository page generator is missing")
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    return builder


def _build_into(output: Path, preview_all: bool = False) -> dict:
    builder = load_builder()
    registry = json.loads((ROOT / "sat-content-registry.json").read_text(encoding="utf-8"))
    shutil.copyfile(ROOT / "sat-content-registry.json", output / "sat-content-registry.json")
    shutil.copyfile(ROOT / "sat-practice-migrations.json", output / "sat-practice-migrations.json")
    if preview_all:
        release = {"schemaVersion": builder.RELEASE_SCHEMA,
                   "features": {name: True for name in builder.RELEASE_FEATURES}}
        (output / RELEASE).write_text(json.dumps(release, indent=2) + "\n", encoding="utf-8")
    elif (ROOT / RELEASE).is_file():
        shutil.copyfile(ROOT / RELEASE, output / RELEASE)
    features = builder.load_release(output)
    resources = set()
    for entry in registry["entries"]:
        if entry["status"] != "published" or entry["kind"] not in {"set", "collection"}:
            continue
        if not builder.released(entry, features):
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
    manifest = builder.build(output)
    if metadata is not None:
        metadata.unlink()  # an input to the pages, not part of the site
    (output / RELEASE).unlink(missing_ok=True)  # likewise; the manifest states the features
    return manifest


def build_site(preview_all: bool = False) -> dict:
    with fresh_output(ROOT, "site-preview" if preview_all else "site") as output:
        return _build_into(output, preview_all)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--preview-all", action="store_true",
                        help="switch every release feature on and build into build/site-preview/")
    arguments = parser.parse_args(argv)
    manifest = build_site(arguments.preview_all)
    exams = sum(1 for page in manifest["pages"] if page.get("examEligible") is True)
    shown = [name for name, on in manifest["features"].items() if on]
    directory = "build/site-preview" if arguments.preview_all else "build/site"
    print(f"Built {'preview' if arguments.preview_all else 'release'} site: {manifest['uniqueQuestionCount']} unique "
          f"questions, {manifest['pageCount']} routes, {exams} fixed two-module mock exam(s); "
          f"release features on: {', '.join(shown) or 'none'}.")
    print(f"Preview: python3 -m http.server --bind 127.0.0.1 --directory {directory} 8000")
    print("This build has no production authentication or deployment step.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
