#!/usr/bin/env python3
"""Keep content/db/ (normalized tables) and content/canonical/ (set files) in step.

content/db/ holds every question once, as JSON Lines tables defined by
content/db/schema.sql. content/canonical/ holds the published questions arranged
per set, which the site builder reads. Either side can be edited; the other is
then regenerated, and `check` fails while they disagree.

Draft questions (status 'draft') live only in the tables. They are in no set
file and are left alone by normalize and export.

    python3 scripts/content_db.py check       verify tables, constraints and both directions
    python3 scripts/content_db.py normalize   content/canonical/ -> content/db/
    python3 scripts/content_db.py export      content/db/ -> content/canonical/
    python3 scripts/content_db.py sqlite      write build/content.sqlite for queries

Only the Python standard library is used. The SQLite file is a build product.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sqlite3
import sys
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
    changed = []
    for name, text in render_tables(tables, root).items():
        path = root / DB_DIR / (name + ".jsonl")
        if not path.is_file() or path.read_text(encoding="utf-8") != text:
            path.write_text(text, encoding="utf-8")
            changed.append(f"{DB_DIR}/{name}.jsonl")
    return changed


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


def normalize(root: Path = ROOT, existing: Tables | None = None) -> Tables:
    """The tables as they should be: set files for published content, `existing` for the rest.

    Columns the set files determine are taken from them. Draft questions, where
    questions came from (source families, modules, placements in imported
    sources), a recorded difficulty with its basis, the task and the passage
    type are carried over from `existing`, as are the task and passage type
    reference rows. Rows that nothing uses any more are dropped.
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
    return {name: [json.loads(line) for line in text.splitlines()]
            for name, text in render_tables(tables, root).items()}


# ---------------------------------------------------------------- tables -> canonical

def export(tables: Tables) -> dict[str, bytes]:
    """Rebuild every canonical set file from the tables, as bytes."""
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

METADATA_SCHEMA = "sat-question-metadata-v2"
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
    task's share of real-exam questions (view task_frequency).
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
            "SELECT i.question_id, 1, t.tier, t.kind, s.label, s.year, s.month, s.region,"
            "       i.module_id, i.question_number"
            "  FROM material_item i"
            "  JOIN material m USING (content_id)"
            "  JOIN source s ON s.source_id = coalesce(i.source_id, m.source_id)"
            "  JOIN source_family f USING (family_id)"
            "  JOIN source_tier t USING (tier)"
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
    finally:
        connection.close()
    return {"schemaVersion": METADATA_SCHEMA, "taxonomy": taxonomy, "questions": questions}


# ---------------------------------------------------------------- checks

def compare_with_canonical(tables: Tables, root: Path = ROOT) -> list[str]:
    try:
        exported = export(tables)
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
    problems = compare_with_canonical(tables, root)
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
            changed = []
            for name, payload in export(tables).items():
                path = root / name
                if not path.is_file() or path.read_bytes() != payload:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(payload)
                    changed.append(name)
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
