#!/usr/bin/env python3
"""Keep content/db/ (normalized tables) and content/canonical/ (set files) in step.

content/db/ holds every question once, as JSON Lines tables defined by
content/db/schema.sql. content/canonical/ holds the published questions arranged
per set, which the site builder reads. Either side can be edited; the other is
then regenerated, and `check` fails while they disagree.

Draft questions (status 'draft') live only in the tables. They are in no set
file and are left alone by normalize and export; a placement of a draft is an
error, never a reason to write it into a set file.

export writes only content/canonical/sets/<name>.json and
content/canonical/collections/<name>.json, never through a symbolic link, and
checks every target before it writes any. Both normalize and export write each
changed file beside itself first and move them all into place together; when
that fails the files are left as they were.

    python3 scripts/content_db.py check       verify tables, constraints and both directions
    python3 scripts/content_db.py normalize   content/canonical/ -> content/db/
    python3 scripts/content_db.py export      content/db/ -> content/canonical/
    python3 scripts/content_db.py sqlite      write build/content.sqlite for queries

Only the Python standard library is used. The SQLite file is a build product.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import tempfile
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DB_DIR = "content/db"
CANONICAL_GLOB = "content/canonical/*/*.json"
SCHEMA_NAME = "schema.sql"
SQLITE_OUTPUT = "build/content.sqlite"

Tables = dict[str, list[dict[str, Any]]]

HEADER_KEYS = ["schemaVersion", "contentId", "title", "section", "questionCount", "questions"]
QUESTION_KEYS = ["passage", "prompt", "correct_answer", "needs_manual_review",
                 "difficulty_estimate", "skills_tested"]
QUESTION_REQUIRED = {"passage", "prompt", "correct_answer", "needs_manual_review"}
VISUAL_KEYS = ["table_data", "visual_assets", "chart_image", "public_visual_alt_text"]
CHART_KEYS = {"chart_image_data_uri": "data_uri", "chart_image_width": "width",
              "chart_image_height": "height"}
ITEM_KEYS = ["question_number", "source_question_number", "module_id", "module_position"]
EXPLANATION_TEXT_KEYS = ["short", "key_evidence", "why_correct", "detailed"]
KNOWN_KEYS = {"question_id", "section", "choices", "explanation", "classification", "source_set",
              *QUESTION_KEYS, *VISUAL_KEYS, *CHART_KEYS, *ITEM_KEYS}


class ContentError(ValueError):
    """The content cannot be represented or the two forms disagree."""


# ---------------------------------------------------------------- schema

def open_schema(root: Path = ROOT, target: str = ":memory:") -> sqlite3.Connection:
    if sqlite3.sqlite_version_info < (3, 37):
        raise ContentError("SQLite 3.37 or newer is required for STRICT tables")
    connection = sqlite3.connect(target)
    connection.executescript((root / DB_DIR / SCHEMA_NAME).read_text(encoding="utf-8"))
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def schema_layout(connection: sqlite3.Connection) -> dict[str, dict[str, list[str]]]:
    """Table order as declared, with each table's columns and primary key."""
    names = [row[0] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY rowid")]
    layout = {}
    for name in names:
        info = connection.execute(f"PRAGMA table_info({name})").fetchall()
        keyed = sorted((row for row in info if row[5]), key=lambda row: row[5])
        layout[name] = {"columns": [row[1] for row in info], "key": [row[1] for row in keyed]}
    return layout


# ---------------------------------------------------------------- table files

def dump_row(row: dict[str, Any]) -> str:
    return json.dumps(row, ensure_ascii=False)


def sort_rows(rows: list[dict[str, Any]], key: list[str]) -> list[dict[str, Any]]:
    return sorted(rows, key=lambda row: tuple(row[column] for column in key))


def read_tables(root: Path = ROOT) -> tuple[Tables, list[str]]:
    """Read every table file; report rows that are not in the fixed written form."""
    connection = open_schema(root)
    layout = schema_layout(connection)
    connection.close()
    directory = root / DB_DIR
    problems = []
    expected = {name + ".jsonl" for name in layout} | {SCHEMA_NAME}
    for path in sorted(directory.iterdir()):
        if path.name not in expected and path.name != ".DS_Store":
            problems.append(f"{DB_DIR}/{path.name}: not a table in {SCHEMA_NAME}")
    tables: Tables = {}
    for name, shape in layout.items():
        path = directory / (name + ".jsonl")
        label = f"{DB_DIR}/{name}.jsonl"
        if not path.is_file():
            problems.append(f"{label}: missing")
            tables[name] = []
            continue
        text = path.read_text(encoding="utf-8")
        rows = []
        for number, line in enumerate(text.splitlines(), 1):
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                problems.append(f"{label}:{number}: not valid JSON")
                continue
            if not isinstance(row, dict) or list(row) != shape["columns"]:
                problems.append(f"{label}:{number}: columns must be {', '.join(shape['columns'])}")
                continue
            if dump_row(row) != line:
                problems.append(f"{label}:{number}: not in the written form; run normalize or rewrite the line")
            rows.append(row)
        if rows and not text.endswith("\n"):
            problems.append(f"{label}: must end with a newline")
        try:
            if rows != sort_rows(rows, shape["key"]):
                problems.append(f"{label}: rows must be sorted by {', '.join(shape['key'])}")
        except (KeyError, TypeError):
            problems.append(f"{label}: primary key values cannot be ordered")
        tables[name] = rows
    return tables, problems


def render_tables(tables: Tables, root: Path = ROOT) -> dict[str, str]:
    connection = open_schema(root)
    layout = schema_layout(connection)
    connection.close()
    rendered = {}
    for name, shape in layout.items():
        rows = [{column: row.get(column) for column in shape["columns"]} for row in tables[name]]
        rendered[name] = "".join(dump_row(row) + "\n" for row in sort_rows(rows, shape["key"]))
    return rendered


def write_tables(tables: Tables, root: Path = ROOT) -> list[str]:
    files = {}
    for name, text in render_tables(tables, root).items():
        relative = f"{DB_DIR}/{name}.jsonl"
        files[checked_path(root, relative)] = text.encode("utf-8")
    return [path.relative_to(root).as_posix() for path in replace_files(files)]


def checked_path(root: Path, relative: str) -> Path:
    """root / relative, refusing a path that leaves the checkout or passes a symbolic link.

    Only the components below root are looked at: root itself may sit under a
    linked directory (macOS keeps temporary folders under /var -> /private/var).
    """
    parts = Path(relative).parts
    if Path(relative).is_absolute() or not parts or any(part in ("", ".", "..") for part in parts):
        raise ContentError(f"{relative!r}: not a plain path inside the checkout")
    current = root
    for part in parts:
        current = current / part
        if current.is_symlink():
            raise ContentError(f"{relative}: {current.relative_to(root).as_posix()} is a symbolic link")
    if current.exists() and not current.is_file():
        raise ContentError(f"{relative}: not a regular file")
    if not current.resolve().is_relative_to(root.resolve()):
        raise ContentError(f"{relative}: leaves the checkout")
    return current


def replace_files(files: dict[Path, bytes]) -> list[Path]:
    """Write every file that changes, or none of them; return the ones written.

    Each file goes to a temporary file in its own directory first, and only when
    all of them are written are they moved into place. If a move fails, the
    files already moved get their old bytes back.
    """
    changed = {path: data for path, data in files.items() if not path.is_file() or path.read_bytes() != data}
    temporary: dict[Path, Path] = {}
    try:
        for path, data in changed.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            handle, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
            with os.fdopen(handle, "wb") as stream:
                stream.write(data)
            os.chmod(name, path.stat().st_mode & 0o777 if path.exists() else 0o644)
            temporary[path] = Path(name)
        moved: list[tuple[Path, bytes | None]] = []
        try:
            for path, name in temporary.items():
                previous = path.read_bytes() if path.exists() else None
                os.replace(name, path)
                moved.append((path, previous))
        except OSError:
            for path, previous in reversed(moved):
                if previous is None:
                    path.unlink(missing_ok=True)
                else:
                    path.write_bytes(previous)
            raise
    finally:
        for name in temporary.values():
            name.unlink(missing_ok=True)
    return sorted(changed)


def load_sqlite(tables: Tables, root: Path = ROOT, target: str = ":memory:") -> sqlite3.Connection:
    """Insert every row under the schema's constraints; raise on the first violation."""
    connection = open_schema(root, target)
    layout = schema_layout(connection)

    def stored(value: Any) -> Any:
        return json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value

    # References are checked after loading, so the report can name the row and
    # the table it points at instead of SQLite's bare "FOREIGN KEY constraint failed".
    connection.execute("PRAGMA foreign_keys = OFF")
    with connection:
        for name, shape in layout.items():
            marks = ", ".join("?" for _ in shape["columns"])
            for row in tables[name]:
                try:
                    connection.execute(
                        f"INSERT INTO {name} VALUES ({marks})",
                        [stored(row.get(column)) for column in shape["columns"]])
                except sqlite3.Error as error:
                    key = ", ".join(str(row.get(column)) for column in shape["key"])
                    raise ContentError(f"{name} [{key}]: {error}") from error
    broken = connection.execute("PRAGMA foreign_key_check").fetchall()
    if broken:
        table, rowid, parent, reference = broken[0]
        columns = [row[3] for row in connection.execute(f"PRAGMA foreign_key_list({table})")
                   if row[0] == reference]
        key = connection.execute(
            f"SELECT {', '.join(layout[table]['key'])} FROM {table} WHERE rowid = ?", (rowid,)).fetchone()
        more = f" ({len(broken) - 1} more like this)" if len(broken) > 1 else ""
        raise ContentError(
            f"{table} [{', '.join(map(str, key))}]: {', '.join(columns)} has no matching row in {parent}{more}")
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


# ---------------------------------------------------------------- canonical -> tables

def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def canonical_files(root: Path = ROOT) -> list[Path]:
    return sorted(root.glob(CANONICAL_GLOB))


def next_id(prefix: str, used: set[str]) -> str:
    numbers = [int(value.rsplit("-", 1)[1]) for value in used]
    value = f"{prefix}-{max(numbers, default=0) + 1:03d}"
    used.add(value)
    return value


def derive(root: Path = ROOT, existing: Tables | None = None) -> Tables:
    """Split the canonical set files into rows, with only the columns they determine.

    Source and layout ids are taken from `existing` when the same label or key
    order is already there, so an edit does not renumber unrelated rows.
    """
    existing = existing or {}
    source_ids = {row["label"]: row["source_id"] for row in existing.get("source", [])}
    layout_ids = {(tuple(row["record_keys"]), tuple(row["explanation_keys"])): row["layout_id"]
                  for row in existing.get("legacy_layout", [])}
    used_sources, used_layouts = set(source_ids.values()), set(layout_ids.values())

    domains: dict[str, dict] = {}
    skills: dict[str, dict] = {}
    subtypes: dict[str, dict] = {}
    questions: dict[str, dict] = {}
    choices: dict[tuple, dict] = {}
    explanations: dict[str, dict] = {}
    charts: dict[str, dict] = {}
    visuals: dict[str, dict] = {}
    sources: dict[str, dict] = {}
    layouts: dict[str, dict] = {}
    materials, items = [], []
    seen_at: dict[str, str] = {}

    def merge(store: dict, key: Any, row: dict, where: str, what: str) -> None:
        """One row per key. A value another occurrence leaves out is kept; two values must agree."""
        current = store.setdefault(key, row)
        for column, value in row.items():
            if current[column] is None:
                current[column] = value
            elif value is not None and current[column] != value:
                raise ContentError(
                    f"{what} {key!r} has two different values for {column}: "
                    f"{where} and {seen_at.get(str(key), 'an earlier file')}")

    for path in canonical_files(root):
        data_path = path.relative_to(root).as_posix()
        document = json.loads(path.read_text(encoding="utf-8"))
        if list(document) != HEADER_KEYS:
            raise ContentError(f"{data_path}: top-level keys must be {', '.join(HEADER_KEYS)}")
        if document["questionCount"] != len(document["questions"]):
            raise ContentError(f"{data_path}: questionCount does not match the number of questions")
        content_id = document["contentId"]
        materials.append({"content_id": content_id, "data_path": data_path,
                          "schema_version": document["schemaVersion"], "title": document["title"],
                          "section": document["section"]})

        for position, record in enumerate(document["questions"], 1):
            question_id = record.get("question_id")
            where = f"{data_path} question {position} ({question_id})"
            unknown = set(record) - KNOWN_KEYS
            if unknown:
                raise ContentError(f"{where}: no column for field(s) {', '.join(sorted(unknown))}")
            missing = ({"question_id", "section", "question_number", "choices", "explanation",
                        "classification"} | QUESTION_REQUIRED) - set(record)
            if missing:
                raise ContentError(f"{where}: missing field(s) {', '.join(sorted(missing))}")

            classification = record["classification"]
            if list(classification) not in (["domain", "skill"], ["domain", "skill", "subtype"]):
                raise ContentError(f"{where}: classification must be domain, skill and optional subtype")
            domain_id, skill_id = slug(classification["domain"]), slug(classification["skill"])
            subtype_name = classification.get("subtype")
            if subtype_name == "":
                raise ContentError(f"{where}: subtype must have a name or be left out")
            # The skill's own id is its "no subtype recorded" row.
            subtype_id = skill_id if subtype_name is None else f"{skill_id}:{subtype_name}"
            merge(domains, domain_id, {"domain_id": domain_id, "section": record["section"],
                                       "name": classification["domain"]}, where, "domain")
            merge(skills, skill_id, {"skill_id": skill_id, "domain_id": domain_id,
                                     "name": classification["skill"]}, where, "skill")
            subtypes.setdefault(subtype_id, {"subtype_id": subtype_id, "skill_id": skill_id,
                                             "name": subtype_name})

            merge(questions, question_id, {
                "question_id": question_id,
                **{key: record.get(key) for key in QUESTION_KEYS[:3]},
                "subtype_id": subtype_id,
                **{key: record.get(key) for key in QUESTION_KEYS[3:]},
            }, where, "question")

            explanation = record["explanation"]
            labels = [choice.get("label") for choice in record["choices"]]
            if (any(list(choice) != ["label", "text"] for choice in record["choices"])
                    or labels != sorted(set(labels))):
                raise ContentError(f"{where}: choices must be label and text, in label order")
            if (set(explanation) != {*EXPLANATION_TEXT_KEYS, "choice_analysis"}
                    or list(explanation["choice_analysis"]) != labels):
                raise ContentError(f"{where}: explanation must hold short, key_evidence, why_correct, "
                                   "detailed and one choice_analysis entry per choice")
            for choice in record["choices"]:
                merge(choices, (question_id, choice["label"]), {
                    "question_id": question_id, "label": choice["label"], "text": choice["text"],
                    "analysis": explanation["choice_analysis"][choice["label"]],
                }, where, "choice")
            merge(explanations, question_id, {
                "question_id": question_id, **{key: explanation[key] for key in EXPLANATION_TEXT_KEYS},
            }, where, "explanation")

            chart_image = record.get("chart_image")
            if any(key in record for key in CHART_KEYS):
                if chart_image is None:
                    raise ContentError(f"{where}: chart size or address given without chart_image")
                merge(charts, chart_image, {
                    "chart_image": chart_image,
                    **{column: record.get(key) for key, column in CHART_KEYS.items()},
                }, where, "chart")
            if any(record.get(key) is not None for key in VISUAL_KEYS):
                merge(visuals, question_id, {
                    "question_id": question_id, **{key: record.get(key) for key in VISUAL_KEYS},
                }, where, "question_visual")

            source_id = None
            if record.get("source_set") is not None:
                label = record["source_set"]
                source_id = source_ids.get(label) or next_id("src", used_sources)
                source_ids[label] = source_id
                sources[source_id] = {"source_id": source_id, "label": label}
            order = (tuple(record), tuple(explanation))
            layout_id = layout_ids.get(order) or next_id("layout", used_layouts)
            layout_ids[order] = layout_id
            layouts[layout_id] = {"layout_id": layout_id, "record_keys": list(order[0]),
                                  "explanation_keys": list(order[1])}
            items.append({
                "content_id": content_id, "position": position, "question_id": question_id,
                "question_number": record["question_number"], "source_id": source_id,
                "source_question_number": record.get("source_question_number"),
                "module_id": record.get("module_id"), "module_position": record.get("module_position"),
                "layout_id": layout_id,
            })
            seen_at.setdefault(str(question_id), where)

    return {
        "domain": list(domains.values()), "skill": list(skills.values()),
        "subtype": list(subtypes.values()), "question": list(questions.values()),
        "choice": list(choices.values()), "explanation": list(explanations.values()),
        "chart": list(charts.values()), "question_visual": list(visuals.values()),
        "source": list(sources.values()), "material": materials,
        "legacy_layout": list(layouts.values()), "material_item": items,
    }


PUBLISHED = {"status": "published", "format": "choice", "markup": "canonical"}
# Set by scripts/classify_questions.py, not by the set files.
CLASSIFIED = ("passage_type", "passage_type_basis", "task_id", "task_basis")
WALKTHROUGH_TABLES = ("walkthrough", "walkthrough_segment", "walkthrough_choice")


def normalize(root: Path = ROOT, existing: Tables | None = None) -> Tables:
    """The tables as they should be: set files for published content, `existing` for the rest.

    Columns the set files determine are taken from them. Draft questions, where
    questions came from (source families, modules, placements in imported
    sources), a recorded difficulty with its basis, the task and the passage
    type are carried over from `existing`, as are the task and passage type
    reference rows, the word explanations (scripts/annotate_vocabulary.py) and
    the walkthroughs (scripts/write_walkthrough.py). Rows that nothing uses any
    more are dropped.
    """
    existing = existing or {}
    derived = derive(root, existing)

    def old(name: str) -> list[dict]:
        return existing.get(name, [])

    def index(rows: list[dict], key: str) -> dict:
        return {row[key]: row for row in rows}

    previous = index(old("question"), "question_id")
    questions = {row["question_id"]: {
        **row, **PUBLISHED,
        # A draft that is published keeps the record of how it was classified.
        "skill_basis": previous.get(row["question_id"], {}).get("skill_basis") or "curated",
        "difficulty": previous.get(row["question_id"], {}).get("difficulty"),
        "difficulty_basis": previous.get(row["question_id"], {}).get("difficulty_basis"),
        **{column: previous.get(row["question_id"], {}).get(column) for column in CLASSIFIED}}
        for row in derived["question"]}
    drafts = {row["question_id"] for row in old("question")
              if row["status"] == "draft" and row["question_id"] not in questions}
    questions.update((key, previous[key]) for key in drafts)

    def with_drafts(name: str) -> list[dict]:
        return derived.get(name, []) + [row for row in old(name) if row["question_id"] in drafts]

    occurrences = [row for row in old("question_source") if row["question_id"] in questions]
    modules = {(row["source_id"], row["module_index"]) for row in occurrences}
    reproduced = index(old("material"), "content_id")
    materials = [{**row, "source_id": reproduced.get(row["content_id"], {}).get("source_id")}
                 for row in derived["material"]]

    known_sources, named_sources = index(old("source"), "source_id"), index(derived["source"], "source_id")
    used_sources = ({row["source_id"] for row in occurrences} | set(named_sources)
                    | {row["source_id"] for row in materials if row["source_id"]})
    sources = [{"source_id": key, **known_sources.get(key, {}), **named_sources.get(key, {})}
               for key in used_sources]

    def reachable(name: str, key: str, wanted: set) -> list[dict]:
        known, stated = index(old(name), key), index(derived[name], key)
        return [{**known.get(value, {}), **stated.get(value, {})}
                for value in wanted if value in known or value in stated]

    subtypes = reachable("subtype", "subtype_id", {row["subtype_id"] for row in questions.values()})
    skills = reachable("skill", "skill_id", {row["skill_id"] for row in subtypes})
    domains = reachable("domain", "domain_id", {row["domain_id"] for row in skills})

    tables: Tables = {
        "domain": domains, "skill": skills, "subtype": subtypes,
        "task": [row for row in old("task") if row["skill_id"] in {skill["skill_id"] for skill in skills}],
        "passage_type": old("passage_type"),
        "source_tier": old("source_tier"), "source_family": old("source_family"),
        "source": sources,
        "source_module": [row for row in old("source_module")
                          if (row["source_id"], row["module_index"]) in modules],
        "question": list(questions.values()),
        "choice": with_drafts("choice"),
        "numeric_answer": with_drafts("numeric_answer"),
        "explanation": with_drafts("explanation"),
        "chart": derived["chart"], "question_visual": derived["question_visual"],
        "question_source": occurrences,
        "material": materials,
        "legacy_layout": derived["legacy_layout"], "material_item": derived["material_item"],
    }
    glosses = [row for row in old("question_vocabulary") if row["question_id"] in questions]
    explained = {row["vocab_id"] for row in glosses}
    for name in ("vocabulary", "vocabulary_example", "vocabulary_form"):
        tables[name] = [row for row in old(name) if row["vocab_id"] in explained]
    tables["question_vocabulary"] = glosses
    for name in WALKTHROUGH_TABLES:
        tables[name] = [row for row in old(name) if row["question_id"] in questions]
    return {name: [json.loads(line) for line in text.splitlines()]
            for name, text in render_tables(tables, root).items()}


# ---------------------------------------------------------------- tables -> canonical

CANONICAL_TARGET = re.compile(r"content/canonical/(?:sets|collections)/[a-z0-9][a-z0-9-]*\.json")


def placement_problems(tables: Tables) -> list[str]:
    """A set file may hold only published questions, and each set file has one material."""
    status = {row["question_id"]: row["status"] for row in tables["question"]}
    problems = [f"material_item [{row['content_id']}, {row['position']}]: {row['question_id']} is "
                f"{status.get(row['question_id'], 'unknown')}, not published; withdraw it from the set first"
                for row in tables["material_item"] if status.get(row["question_id"]) != "published"]
    seen: dict[str, str] = {}
    for row in tables["material"]:
        path = row["data_path"]
        if not isinstance(path, str) or not CANONICAL_TARGET.fullmatch(path):
            problems.append(f"material [{row['content_id']}]: data_path {path!r} is not "
                            "content/canonical/sets/<name>.json or content/canonical/collections/<name>.json")
        elif path in seen:
            problems.append(f"material [{row['content_id']}]: data_path {path} is also {seen[path]}'s")
        else:
            seen[path] = row["content_id"]
    return problems


def export_files(tables: Tables, root: Path = ROOT) -> dict[Path, bytes]:
    """Every set file the tables describe, keyed by its checked path; nothing is written.

    Raises ContentError before anything could be written when a placement holds
    a draft or a target is not a set or collection file of this checkout.
    """
    problems = placement_problems(tables)
    if problems:
        raise ContentError("\n".join(problems))
    files = {}
    for name, payload in export(tables).items():
        files[checked_path(root, name)] = payload
    return files


def export(tables: Tables) -> dict[str, bytes]:
    """Rebuild every canonical set file from the tables, as bytes."""
    problems = placement_problems(tables)
    if problems:
        raise ContentError("\n".join(problems))

    def index(name: str, key: str) -> dict:
        return {row[key]: row for row in tables[name]}

    domains, skills, subtypes = index("domain", "domain_id"), index("skill", "skill_id"), index("subtype", "subtype_id")
    questions, explanations = index("question", "question_id"), index("explanation", "question_id")
    charts, visuals = index("chart", "chart_image"), index("question_visual", "question_id")
    sources, layouts = index("source", "source_id"), index("legacy_layout", "layout_id")
    choices: dict[str, list[dict]] = {}
    for row in tables["choice"]:
        choices.setdefault(row["question_id"], []).append(row)
    items: dict[str, list[dict]] = {}
    for row in tables["material_item"]:
        items.setdefault(row["content_id"], []).append(row)

    def record(item: dict) -> dict:
        question = questions[item["question_id"]]
        subtype = subtypes[question["subtype_id"]]
        skill = skills[subtype["skill_id"]]
        domain = domains[skill["domain_id"]]
        visual = visuals.get(question["question_id"], {})
        chart = charts.get(visual.get("chart_image"), {})
        layout = layouts[item["layout_id"]]
        options = sorted(choices[question["question_id"]], key=lambda row: row["label"])
        explanation = explanations[question["question_id"]]
        classification = {"domain": domain["name"], "skill": skill["name"]}
        if subtype["name"] is not None:
            classification["subtype"] = subtype["name"]
        values = {
            "question_id": question["question_id"], "section": domain["section"],
            "choices": [{"label": row["label"], "text": row["text"]} for row in options],
            "explanation": {
                key: ({row["label"]: row["analysis"] for row in options}
                      if key == "choice_analysis" else explanation[key])
                for key in layout["explanation_keys"]},
            "classification": classification,
            "source_set": sources[item["source_id"]]["label"] if item["source_id"] else None,
            **{key: question[key] for key in QUESTION_KEYS},
            **{key: visual.get(key) for key in VISUAL_KEYS},
            **{key: chart.get(column) for key, column in CHART_KEYS.items()},
            **{key: item[key] for key in ITEM_KEYS},
        }
        values["needs_manual_review"] = bool(values["needs_manual_review"])
        return {key: values[key] for key in layout["record_keys"]}

    files = {}
    for material in tables["material"]:
        ordered = sorted(items.get(material["content_id"], []), key=lambda row: row["position"])
        document = {
            "schemaVersion": material["schema_version"], "contentId": material["content_id"],
            "title": material["title"], "section": material["section"],
            "questionCount": len(ordered), "questions": [record(item) for item in ordered],
        }
        text = json.dumps(document, ensure_ascii=False, indent=2) + "\n"
        files[material["data_path"]] = text.encode("utf-8")
    return files


# ---------------------------------------------------------------- what the site shows about a question

METADATA_SCHEMA = "sat-question-metadata-v3"
DIFFICULTY_LEVELS = ("easy", "medium", "hard")


def question_metadata(tables: Tables, root: Path = ROOT) -> dict[str, Any]:
    """Origin, difficulty, classification and explanation level of every published question, for the site builder.

    kind and tier come from the best source the question is known from, year is
    the latest year it appeared in a source of that tier. difficultyBasis says
    where the difficulty comes from: 'source_file', 'estimate' or 'model'. refs lists every
    known appearance, best first: imported sources before the labels carried by
    the set files, then most recent. explanation is 'full', 'brief' or 'none'
    (view question_explanation). task and passageType are the ids recorded by
    scripts/classify_questions.py; skill and domain are the ones the task
    belongs to. importance is 'core', 'important' or 'basic' and sittings the
    number of exam sittings the question came up at (view question_importance).
    taxonomy lists the tasks and passage types in display order, with each
    task's share of real-exam questions (view task_frequency). vocabulary
    holds the word explanations the published questions use, and a question's
    own vocabulary list says which words in which field they belong to. Each
    explanation says in how many published Reading and Writing questions the
    word is written (questions), how many of those are real-exam questions
    (realExamQuestions) and how many times in all (occurrences); see
    vocabulary_usage for what the count does not tell. A question that has a
    walkthrough carries it whole: the approach, the segments in reading order
    and a note per choice, each in English and in Chinese.
    """
    connection = load_sqlite(tables, root)
    try:
        questions: dict[str, dict[str, Any]] = {}
        for (question_id, stated, basis, estimate, kind, tier, year, explanation, task, passage_type,
             skill, domain, importance, sittings) in connection.execute(
                "SELECT q.question_id, q.difficulty, q.difficulty_basis, q.difficulty_estimate,"
                "       p.kind, p.tier, p.latest_year, p.explanation, q.task_id, q.passage_type,"
                "       p.skill, p.domain, p.importance, p.sittings"
                "  FROM question q JOIN practice_pool p USING (question_id)"
                " WHERE q.status = 'published'"):
            estimate = estimate.lower() if isinstance(estimate, str) else None
            # A source's own statement first, then the estimate carried by the
            # set files, then a model's estimate from the text alone.
            if basis != "source_file" and estimate in DIFFICULTY_LEVELS:
                stated, basis = estimate, "estimate"
            questions[question_id] = {
                "kind": kind, "tier": tier, "year": year,
                "difficulty": stated, "difficultyBasis": basis if stated else None,
                "explanation": explanation, "task": task, "passageType": passage_type,
                "skill": skill, "domain": domain, "importance": importance, "sittings": sittings,
                "refs": [],
            }
        appearances = connection.execute(
            "SELECT p.question_id, 0 AS carried, t.tier, t.kind, s.label, s.year, s.month, s.region,"
            "       m.name AS module, p.number"
            "  FROM question_source p"
            "  JOIN source_module m USING (source_id, module_index)"
            "  JOIN source s USING (source_id)"
            "  JOIN source_family f USING (family_id)"
            "  JOIN source_tier t USING (tier)"
            " UNION "
            # A set file's placement names its source only where no imported
            # occurrence already does, and gives the number the question has
            # there: source_question_number when the set renumbered it, the
            # set's own number only when the set reproduces that very source.
            # A position in a collection or in a material of another source
            # is never passed off as one.
            "SELECT i.question_id, 1, t.tier, t.kind, s.label, s.year, s.month, s.region,"
            "       i.module_id,"
            "       CASE WHEN i.source_question_number IS NOT NULL THEN i.source_question_number"
            "            WHEN s.source_id = m.source_id AND m.data_path LIKE 'content/canonical/sets/%'"
            "            THEN i.question_number END"
            "  FROM material_item i"
            "  JOIN material m USING (content_id)"
            "  JOIN source s ON s.source_id = coalesce(i.source_id, m.source_id)"
            "  JOIN source_family f USING (family_id)"
            "  JOIN source_tier t USING (tier)"
            " WHERE NOT EXISTS (SELECT 1 FROM question_source q"
            "                    WHERE q.question_id = i.question_id AND q.source_id = s.source_id)"
            " ORDER BY 1, 3, 2, 6 DESC, 7 DESC, 5, 9, 10").fetchall()
        for question_id, _, tier, kind, label, year, month, region, module, number in appearances:
            if question_id in questions:
                questions[question_id]["refs"].append({
                    "label": label, "kind": kind, "tier": tier, "year": year, "month": month,
                    "region": region, "module": module, "number": number})
        taxonomy = {
            "passageTypes": [
                {"id": key, "name": name, "family": family} for key, name, family in connection.execute(
                    "SELECT passage_type, name_zh, family FROM passage_type ORDER BY position")],
            "tasks": [
                {"id": key, "name": name, "english": english, "skill": skill, "domain": domain, "examShare": share}
                for key, name, english, skill, domain, share in connection.execute(
                    "SELECT k.task_id, k.name_zh, k.name, s.name, d.name, f.percent"
                    "  FROM task k JOIN skill s USING (skill_id) JOIN domain d USING (domain_id)"
                    "  JOIN task_frequency f USING (task_id)"
                    " ORDER BY d.name, s.name, k.position")],
            "importance": [{"id": "core", "name": "核心"}, {"id": "important", "name": "重要"},
                           {"id": "basic", "name": "基础"}],
        }
        used: set[str] = set()
        for question_id, field, surface, vocab_id, reveal in connection.execute(
                "SELECT question_id, field, surface, vocab_id, reveal FROM question_vocabulary"
                " ORDER BY question_id, field, surface"):
            if question_id in questions:
                questions[question_id].setdefault("vocabulary", []).append(
                    {"field": field, "surface": surface, "id": vocab_id, "reveal": reveal})
                used.add(vocab_id)
        reading = {question_id for (question_id,) in connection.execute(
            "SELECT question_id FROM practice_pool WHERE status = 'published' AND section = 'reading_writing'")}
        usage = vocabulary_usage(tables, reading)
        examples: dict[str, list[dict[str, str]]] = {}
        for vocab_id, english, chinese in connection.execute(
                "SELECT vocab_id, example_en, example_zh FROM vocabulary_example ORDER BY vocab_id, position"):
            examples.setdefault(vocab_id, []).append({"english": english, "chinese": chinese})
        vocabulary = {
            vocab_id: {"word": headword, "partOfSpeech": part, "english": english, "chinese": chinese,
                       "examples": examples.get(vocab_id, []), "basis": basis,
                       "questions": len(usage[vocab_id]["questions"]),
                       "realExamQuestions": sum(questions[question_id]["kind"] == "real_exam"
                                                for question_id in usage[vocab_id]["questions"]),
                       "occurrences": usage[vocab_id]["occurrences"]}
            for vocab_id, headword, part, english, chinese, basis in connection.execute(
                "SELECT vocab_id, headword, part_of_speech, definition_en, definition_zh, basis"
                "  FROM vocabulary ORDER BY vocab_id")
            if vocab_id in used}
        for question_id, english, chinese, basis in connection.execute(
                "SELECT question_id, approach_en, approach_zh, basis FROM walkthrough ORDER BY question_id"):
            if question_id in questions:
                questions[question_id]["walkthrough"] = {
                    "approach": {"english": english, "chinese": chinese}, "basis": basis,
                    "segments": [], "choices": {}}
        for question_id, field, excerpt, english, chinese in connection.execute(
                "SELECT question_id, field, excerpt, note_en, note_zh FROM walkthrough_segment"
                " ORDER BY question_id, position"):
            if question_id in questions:
                questions[question_id]["walkthrough"]["segments"].append(
                    {"field": field, "excerpt": excerpt, "english": english, "chinese": chinese})
        for question_id, label, english, chinese in connection.execute(
                "SELECT question_id, label, note_en, note_zh FROM walkthrough_choice ORDER BY question_id, label"):
            if question_id in questions:
                questions[question_id]["walkthrough"]["choices"][label] = {"english": english, "chinese": chinese}
    finally:
        connection.close()
    return {"schemaVersion": METADATA_SCHEMA, "taxonomy": taxonomy, "vocabulary": vocabulary, "questions": questions}


# ---------------------------------------------------------------- word explanations

GLOSS_WORD = re.compile(r"[A-Za-z][A-Za-z'’\-]*[A-Za-z]|[A-Za-z]")
GLOSS_MARKUP = re.compile(r"<[^>]*>|\[/?[A-Za-z]+\]")
GLOSS_FIELDS = ("passage", "prompt", "choices")


def gloss_words(value: Any) -> list[re.Match]:
    """The words of a passage, prompt or choice, found the way the practice page finds them.

    Inline markers such as [u] end a word, as the element boundary does on the
    page. The page reads the same pattern off every text node.
    """
    return list(GLOSS_WORD.finditer(GLOSS_MARKUP.sub(lambda found: " " * len(found.group(0)), str(value or ""))))


# What may stand before a capitalised word that merely starts a sentence, a choice or a list item.
GLOSS_SENTENCE_START = set(".?!:\"“”‘—–-●•·|(\n")


# A capitalised word after one of these is a surname, not the start of a sentence.
GLOSS_TITLES = ("Mr.", "Mrs.", "Ms.", "Dr.", "St.", "Prof.", "Jr.", "Sr.", "Mt.")


def gloss_spelling(word: str) -> str:
    return word.replace("’", "'").lower()


def gloss_is_ordinary(text: str, found: re.Match) -> bool:
    """Lower case, or capitalised only because it starts a sentence, a choice or a list item.

    A capitalised word inside a sentence is a name and is never read as vocabulary.
    """
    word = found.group(0)
    if word == word.lower():
        return True
    if not (word[0].isupper() and word[1:] == word[1:].lower()):
        return False
    before = text[:found.start()].rstrip(" \t")
    if before.endswith(GLOSS_TITLES):
        return False
    return not before or before[-1] in GLOSS_SENTENCE_START


def gloss_texts(tables: Tables) -> dict[str, dict[str, str]]:
    """question_id -> the text of each field a word explanation can sit in."""
    texts = {row["question_id"]: {"passage": row["passage"] or "", "prompt": row["prompt"] or "", "choices": ""}
             for row in tables["question"]}
    for row in sort_rows(tables["choice"], ["question_id", "label"]):
        texts[row["question_id"]]["choices"] += str(row["text"] or "") + "\n"
    return texts


def vocabulary_id(headword: str) -> str:
    """The id an explanation of this headword gets: "in accordance with" -> "in-accordance-with"."""
    return re.sub(r"[^a-z0-9]+", "-", headword.lower()).strip("-")


VOCABULARY_EXAMPLES = 3


def vocabulary_problems(tables: Tables) -> list[str]:
    """What the schema cannot say about the word explanations.

    - an explained word stands in the question text exactly as recorded;
    - its spelling is one the list knows, so linking can be repeated;
    - every explanation is found by at least one spelling;
    - every explanation has at least three example sentences, numbered from 1;
    - an id is made from its headword ("abut", or "abut-2" for a second sense).
    """
    texts = gloss_texts(tables)
    present: dict[tuple[str, str], set[str]] = {}
    spellings = {row["form"] for row in tables.get("vocabulary_form", [])}
    problems = []
    for row in tables.get("question_vocabulary", []):
        key = (row["question_id"], row["field"])
        if key not in present:
            present[key] = {found.group(0) for found in gloss_words(texts.get(row["question_id"], {}).get(row["field"]))}
        if row["surface"] not in present[key]:
            problems.append(f"{DB_DIR}/question_vocabulary.jsonl: '{row['surface']}' is not a word in the "
                            f"{row['field']} of {row['question_id']}")
        elif gloss_spelling(row["surface"]) not in spellings:
            problems.append(f"{DB_DIR}/question_vocabulary.jsonl: '{row['surface']}' in {row['question_id']} is not a "
                            f"spelling in vocabulary_form")
    found_by = {row["vocab_id"] for row in tables.get("vocabulary_form", [])}
    shown: dict[str, list[int]] = {}
    for row in tables.get("vocabulary_example", []):
        shown.setdefault(row["vocab_id"], []).append(row["position"])
    for row in tables.get("vocabulary", []):
        if row["vocab_id"] not in found_by:
            problems.append(f"{DB_DIR}/vocabulary.jsonl: '{row['vocab_id']}' has no spelling in vocabulary_form")
        positions = sorted(shown.get(row["vocab_id"], []))
        if len(positions) < VOCABULARY_EXAMPLES or positions != list(range(1, len(positions) + 1)):
            problems.append(f"{DB_DIR}/vocabulary_example.jsonl: '{row['vocab_id']}' needs at least "
                            f"{VOCABULARY_EXAMPLES} examples, numbered from 1 without a gap")
        made = vocabulary_id(row["headword"])
        if not made or not re.fullmatch(re.escape(made) + r"(-[2-9])?", row["vocab_id"]):
            problems.append(f"{DB_DIR}/vocabulary.jsonl: the id '{row['vocab_id']}' is not made from the headword "
                            f"'{row['headword']}'")
    return problems


# ---------------------------------------------------------------- walkthroughs

WALKTHROUGH_MARKUP = re.compile(r"<[^>]*>|\[/?(?:i|u|b|vocab)\]", re.IGNORECASE)
WALKTHROUGH_FIELDS = ("passage", "prompt")


def walkthrough_text(value: Any) -> str:
    """A passage or a prompt as the learner reads it: no inline markers, single spaces."""
    return " ".join(WALKTHROUGH_MARKUP.sub("", str(value or "")).split())


def walkthrough_problems(tables: Tables) -> list[str]:
    """What the schema cannot say about the walkthroughs.

    - a walkthrough has at least one segment, numbered from 1 without a gap,
      the passage before the prompt;
    - every excerpt stands in the field it names, after the excerpt before it;
    - every answer choice of the question has a note.
    """
    questions = {row["question_id"]: row for row in tables.get("question", [])}
    labels: dict[str, set[str]] = {}
    for row in tables.get("choice", []):
        labels.setdefault(row["question_id"], set()).add(row["label"])
    segments: dict[str, list[dict]] = {}
    for row in tables.get("walkthrough_segment", []):
        segments.setdefault(row["question_id"], []).append(row)
    noted: dict[str, set[str]] = {}
    for row in tables.get("walkthrough_choice", []):
        noted.setdefault(row["question_id"], set()).add(row["label"])
    problems = []
    for row in tables.get("walkthrough", []):
        question_id = row["question_id"]
        where = f"{DB_DIR}/walkthrough_segment.jsonl: {question_id}"
        parts = sorted(segments.get(question_id, []), key=lambda part: part["position"])
        if not parts or [part["position"] for part in parts] != list(range(1, len(parts) + 1)):
            problems.append(f"{where} needs segments numbered from 1 without a gap")
            continue
        fields = [part["field"] for part in parts]
        if fields != sorted(fields):
            problems.append(f"{where} must read the passage before the prompt")
        for field in WALKTHROUGH_FIELDS:
            text, start = walkthrough_text(questions.get(question_id, {}).get(field)), 0
            for part in parts:
                if part["field"] != field:
                    continue
                at = text.find(walkthrough_text(part["excerpt"]), start)
                if at < 0 or part["excerpt"] != walkthrough_text(part["excerpt"]):
                    problems.append(f"{where} segment {part['position']} is not a part of the {field}, "
                                    "or not in reading order")
                    break
                start = at + len(part["excerpt"])
        if noted.get(question_id, set()) != labels.get(question_id, set()):
            problems.append(f"{DB_DIR}/walkthrough_choice.jsonl: {question_id} needs one note for each of its choices")
    return problems


def vocabulary_usage(tables: Tables, question_ids: set[str]) -> dict[str, dict[str, Any]]:
    """How often each explained word turns up in the given questions, by spelling.

    vocab_id -> the questions whose passage, prompt or choices contain one of
    the word's spellings, and the number of times it is written in them. The
    count goes by spelling alone: it does not tell one sense of a word from
    another, and it covers questions the word is not linked in.
    """
    forms = {row["form"]: row["vocab_id"] for row in tables.get("vocabulary_form", [])}
    usage = {row["vocab_id"]: {"questions": set(), "occurrences": 0} for row in tables.get("vocabulary", [])}
    texts = gloss_texts(tables)
    for question_id in sorted(question_ids):
        for field in GLOSS_FIELDS:
            text = GLOSS_MARKUP.sub(lambda found: " " * len(found.group(0)), texts[question_id][field])
            for found in gloss_words(text):
                vocab_id = forms.get(gloss_spelling(found.group(0)))
                if vocab_id in usage and gloss_is_ordinary(text, found):
                    usage[vocab_id]["questions"].add(question_id)
                    usage[vocab_id]["occurrences"] += 1
    return usage


# ---------------------------------------------------------------- checks

def compare_with_canonical(tables: Tables, root: Path = ROOT) -> list[str]:
    try:
        exported = {path.relative_to(root).as_posix(): payload
                    for path, payload in export_files(tables, root).items()}
    except ContentError as error:
        return str(error).splitlines()
    except (KeyError, TypeError) as error:
        return [f"{DB_DIR}: cannot rebuild the set files, a row is missing or incomplete ({error!r})"]
    on_disk = {path.relative_to(root).as_posix(): path.read_bytes() for path in canonical_files(root)}
    problems = [f"{name}: in content/canonical/ but not in {DB_DIR}/material.jsonl"
                for name in sorted(set(on_disk) - set(exported))]
    problems += [f"{name}: in {DB_DIR}/material.jsonl but the file does not exist"
                 for name in sorted(set(exported) - set(on_disk))]
    problems += [f"{name}: differs from what {DB_DIR}/ produces"
                 for name in sorted(set(on_disk) & set(exported)) if on_disk[name] != exported[name]]
    return problems


def check(root: Path = ROOT) -> list[str]:
    tables, problems = read_tables(root)
    if problems:
        return problems
    try:
        load_sqlite(tables, root).close()
    except ContentError as error:
        return [f"{DB_DIR}: {error}"]
    problems = compare_with_canonical(tables, root) or vocabulary_problems(tables) + walkthrough_problems(tables)
    if problems:
        return problems
    try:
        expected = render_tables(normalize(root, tables), root)
    except ContentError as error:
        return [str(error)]
    current = render_tables(tables, root)
    return [f"{DB_DIR}/{name}.jsonl: has rows nothing uses, is missing rows, "
            "or marks a question published that is in no set file"
            for name in current if current[name] != expected[name]]


HINT = ("content/canonical/ and content/db/ must describe the same content.\n"
        "  Edited a set file under content/canonical/?  python3 scripts/content_db.py normalize\n"
        "  Edited a table under content/db/?            python3 scripts/content_db.py export")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("command", choices=["check", "normalize", "export", "sqlite"])
    parser.add_argument("--root", type=Path, default=ROOT, help=argparse.SUPPRESS)
    arguments = parser.parse_args(argv)
    root = arguments.root.resolve()
    try:
        if arguments.command == "check":
            problems = check(root)
            if problems:
                print("\n".join(problems[:40]), file=sys.stderr)
                if len(problems) > 40:
                    print(f"... and {len(problems) - 40} more", file=sys.stderr)
                print("\n" + HINT, file=sys.stderr)
                return 1
            tables, _ = read_tables(root)
            drafts = sum(row["status"] == "draft" for row in tables["question"])
            print(f"content/db matches content/canonical: {len(tables['question']) - drafts} published "
                  f"questions, {len(tables['material_item'])} placements in {len(tables['material'])} sets; "
                  f"{drafts} drafts kept in the tables only.")
        elif arguments.command == "normalize":
            existing, _ = read_tables(root)
            tables = normalize(root, existing)
            load_sqlite(tables, root).close()
            problems = compare_with_canonical(tables, root)
            if problems:
                raise ContentError("the tables would not reproduce the set files:\n" + "\n".join(problems))
            changed = write_tables(tables, root)
            print("\n".join(changed) if changed else "content/db is already up to date.")
        elif arguments.command == "export":
            tables, problems = read_tables(root)
            if problems:
                raise ContentError("\n".join(problems))
            load_sqlite(tables, root).close()
            # Every target is checked before the first byte is written.
            changed = [path.relative_to(root).as_posix() for path in replace_files(export_files(tables, root))]
            print("\n".join(changed) if changed else "content/canonical is already up to date.")
        else:
            tables, problems = read_tables(root)
            if problems:
                raise ContentError("\n".join(problems))
            target = root / SQLITE_OUTPUT
            target.parent.mkdir(parents=True, exist_ok=True)
            target.unlink(missing_ok=True)
            connection = load_sqlite(tables, root, str(target))
            connection.commit()
            connection.close()
            print(f"Wrote {SQLITE_OUTPUT}")
    except ContentError as error:
        print(str(error), file=sys.stderr)
        return 1
    except OSError as error:
        print(f"nothing was changed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
