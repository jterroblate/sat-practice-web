#!/usr/bin/env python3
"""Offer real-exam sets as fixed two-module Reading and Writing mock exams.

    python3 scripts/exam_modules.py            # report which sets qualify
    python3 scripts/exam_modules.py --apply    # write moduleSpec, examVariants and examEligible to the registry
    python3 scripts/exam_modules.py --check    # fail when the registry differs from the tables

A set qualifies when it reproduces a real-exam source (tier 1) whose Reading and
Writing Module 1 and second module each still have at least MIN_QUESTIONS of
their questions published in the set. The second module is the one the source
calls "Harder" when it names one, otherwise its only "Module 2"; a module the
source calls "Easier" is never used. The exam is fixed: Module 1, then the
second module, with no routing between them and no scaled score.

A set whose registry entry already carries a hand-made moduleSpec with a
Module 1 ("m1") and a fixed hard second module ("m2_hard") is offered as it is:
its modules are never rewritten here. If such a module names a question the set
no longer publishes (left out, withdrawn to a draft, listed twice), the entry
is closed: examEligible becomes false and examVariants empty, while its
moduleSpec is kept for whoever repairs it.

Every other entry that is open but no longer backed by the tables (its source,
its module or the set itself is gone or unpublished) is closed the same way, so
a mock exam never stays open by default. Opening one is still the owner's
decision: the site shows it only when the release switch for fixed mock exams
is on (sat-release.json).

Questions keep their order in the source. Questions the set does not publish
(figures, unsupported markup, near copies) are left out, and the time limit
shrinks with them: 32 minutes for 27 questions, rounded up to a whole minute.
Everything here is read off content/db, so running it again changes nothing.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import content_db as db  # noqa: E402


REGISTRY = "sat-content-registry.json"
MIN_QUESTIONS = 22
FULL_MODULE = (27, 32 * 60)  # questions, seconds
MODULE_ONE = "读写 · Module 1"
MODULE_TWO = "读写 · Module 2"
HARDER = "Harder"
EASIER = "Easier"


def duration(count: int) -> int:
    questions, seconds = FULL_MODULE
    return math.ceil(count * seconds / questions / 60) * 60


def exam_specs(tables: db.Tables, root: Path = db.ROOT) -> dict[str, dict[str, Any]]:
    """moduleSpec and examVariants by contentId for every set that reproduces a real exam.

    The value is None for a set that does not qualify.
    """
    connection = db.load_sqlite(tables, root)
    try:
        modules: dict[str, list[tuple[int, str, list[str]]]] = {}
        rows = connection.execute(
            "SELECT m.content_id, sm.module_index, sm.name, p.question_id"
            "  FROM material m"
            "  JOIN source s USING (source_id)"
            "  JOIN source_family f USING (family_id)"
            "  JOIN source_tier t USING (tier)"
            "  JOIN source_module sm USING (source_id)"
            "  JOIN question_source p ON p.source_id = sm.source_id AND p.module_index = sm.module_index"
            "  JOIN material_item i ON i.content_id = m.content_id AND i.question_id = p.question_id"
            " WHERE t.kind = 'real_exam' AND m.section = 'reading_writing' AND sm.name LIKE '读写%'"
            " ORDER BY m.content_id, sm.module_index, p.number").fetchall()
    finally:
        connection.close()
    for content_id, index, name, question_id in rows:
        found = modules.setdefault(content_id, [])
        if not found or found[-1][0] != index:
            found.append((index, name, []))
        found[-1][2].append(question_id)

    specs: dict[str, dict[str, Any] | None] = {}
    for content_id, found in sorted(modules.items()):
        specs[content_id] = None
        first = [ids for _, name, ids in found if name.startswith(MODULE_ONE)]
        harder = [ids for _, name, ids in found if HARDER in name]
        plain = [ids for _, name, ids in found if name == MODULE_TWO]
        second = harder or plain
        if len(first) != 1 or len(second) != 1:
            continue
        first_ids = list(dict.fromkeys(first[0]))
        # Some recalled papers repeat Module 1 questions in the second module.
        second_ids = [question_id for question_id in dict.fromkeys(second[0]) if question_id not in set(first_ids)]
        if min(len(first_ids), len(second_ids)) < MIN_QUESTIONS:
            continue
        second_label = "Module 2 · Hard" if harder else "Module 2"
        specs[content_id] = {
            "moduleSpec": {
                "m1": {"label": "Module 1", "durationSeconds": duration(len(first_ids)), "questionIds": first_ids},
                "m2": {"label": second_label, "durationSeconds": duration(len(second_ids)), "questionIds": second_ids},
            },
            "examVariants": [{"id": "fixed", "label": f"Module 1 + {second_label}", "branch": "hard",
                              "modules": ["m1", "m2"]}],
        }
    return specs


CURATED_MODULES = ["m1", "m2_hard"]
CURATED_VARIANT = {"id": "fixed-hard", "label": "Module 1 + Module 2 · Hard", "branch": "hard",
                   "modules": CURATED_MODULES}
OFF = {"examEligible": False, "moduleSpec": None, "examVariants": []}


CLOSED = {"examEligible": False, "examVariants": []}


def published_members(tables: db.Tables) -> dict[str, set[str]]:
    """The published questions each material places, by contentId."""
    status = {row["question_id"]: row["status"] for row in tables["question"]}
    members: dict[str, set[str]] = {}
    for row in tables["material_item"]:
        if status.get(row["question_id"]) == "published":
            members.setdefault(row["content_id"], set()).add(row["question_id"])
    return members


def apply(registry: dict[str, Any], specs: dict[str, dict[str, Any] | None],
          members: dict[str, set[str]] | None = None) -> list[str]:
    """Make the registry say what the tables support; return the contentIds that changed.

    Entries for sets that reproduce a real exam are written from `specs`. An
    entry with a hand-made m1 + m2_hard moduleSpec keeps it and is switched on
    while every question it lists is a published member of the set (`members`,
    from the tables; the entry's own questionIds when not given), and switched
    off, moduleSpec kept, when one is not. Any other entry that is open is
    closed. Entries that are closed and not real exams are left as they are.
    """
    changed = []
    for entry in registry["entries"]:
        offered = entry.get("status") == "published" and entry["kind"] == "set"
        if entry["contentId"] in specs:
            spec = specs[entry["contentId"]] if offered else None
            wanted = {"examEligible": True, **spec} if spec else OFF
        elif list(entry.get("moduleSpec") or {}) == CURATED_MODULES:
            listed = [question_id for key in CURATED_MODULES
                      for question_id in (entry["moduleSpec"][key] or {}).get("questionIds") or []]
            present = (members.get(entry["contentId"], set()) if members is not None
                       else set(entry.get("questionIds") or []))
            sound = offered and listed and len(listed) == len(set(listed)) and set(listed) <= present
            wanted = {"examEligible": True, "examVariants": [dict(CURATED_VARIANT)]} if sound else CLOSED
        elif entry.get("examEligible") or entry.get("examVariants"):
            # Open without anything in the tables to back it: the source, the
            # module or the set is gone. Close it rather than keep serving it.
            wanted = CLOSED
        else:
            continue
        if any(entry.get(key) != value for key, value in wanted.items()):
            entry.update(wanted)
            changed.append(entry["contentId"])
    return changed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--apply", action="store_true", help="write the registry")
    group.add_argument("--check", action="store_true", help="fail when the registry is out of step")
    parser.add_argument("--root", type=Path, default=db.ROOT)
    args = parser.parse_args(argv)

    tables, problems = db.read_tables(args.root)
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 1
    specs = exam_specs(tables, args.root)
    path = args.root / REGISTRY
    registry = json.loads(path.read_text(encoding="utf-8"))
    changed = apply(registry, specs, published_members(tables))
    offered = [entry for entry in registry["entries"] if entry["examEligible"]]
    for entry in offered:
        first, second = (entry["moduleSpec"][key] for key in entry["examVariants"][0]["modules"])
        print(f"{entry['contentId']:32} {len(first['questionIds']):2} + {len(second['questionIds']):2}  "
              f"{second['label']:26} {entry['title']}")
    print(f"{len(offered)} set(s) offered as mock exams, {sum(spec is None for spec in specs.values())} real-exam "
          f"set(s) kept out; {len(changed)} registry entr{'y' if len(changed) == 1 else 'ies'} "
          + ("changed" if args.apply else "out of step" if args.check else "to change"))
    if args.apply and changed:
        path.write_text(json.dumps(registry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 1 if args.check and changed else 0


if __name__ == "__main__":
    raise SystemExit(main())
