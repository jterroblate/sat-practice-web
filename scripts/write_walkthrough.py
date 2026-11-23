#!/usr/bin/env python3
"""Read Reading and Writing questions through for learners, in English and in Chinese.

    python3 scripts/write_walkthrough.py                         report how far the walkthroughs reach
    python3 scripts/write_walkthrough.py --template ID [ID ...]  print an entry to fill in for each question
    python3 scripts/write_walkthrough.py --entries FILE --apply  add or replace walkthroughs
    python3 scripts/write_walkthrough.py --export FILE           write every walkthrough to a file, to read and correct

A walkthrough is what a learner reads after submitting an answer. Three tables
hold it:

walkthrough
    How the answer is reached, and who vouches for the whole walkthrough.
walkthrough_segment
    The passage and then the prompt, a part at a time: the part as it stands
    in the question, what it says in plainer English, and what it says in
    Chinese.
walkthrough_choice
    Every answer choice in turn: what its words mean and why it is right or
    wrong.

An entries file is JSON Lines, one question per line:

    {"question_id": "...",
     "approach": {"en": "...", "zh": "..."},
     "segments": [{"field": "passage", "excerpt": "...", "en": "...", "zh": "..."},
                  {"field": "prompt", "excerpt": "...", "en": "...", "zh": "..."}],
     "choices": {"A": {"en": "...", "zh": "..."}, "B": {...}, "C": {...}, "D": {...}}}

"basis" is 'ai_draft' unless the line says 'human_checked'. An excerpt is a
part of the passage or the prompt as the learner reads it: plain text, without
the inline markers. Excerpts follow the text in reading order and may leave
parts out, a table for example. --template prints the passage one sentence
per segment; join neighbouring excerpts with a space to read several
sentences together. An entry that comes in replaces the walkthrough the
question had. --export writes the same format, and exporting and importing
unchanged changes nothing.

Only published Reading and Writing questions take a walkthrough.
"""

from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path
import re
import sys
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import content_db as db  # noqa: E402

BASES = ("ai_draft", "human_checked")
LANGUAGES = ("en", "zh")
# After one of these a full stop does not end a sentence.
ABBREVIATIONS = {"Mr.", "Mrs.", "Ms.", "Dr.", "St.", "Prof.", "Jr.", "Sr.", "Mt.", "v.", "vs.", "etc.", "e.g.", "i.e.",
                 "al.", "ca.", "No."}
SENTENCE_END = re.compile(r"[.!?][\"”’)\]]*\s+")
SENTENCE_START = re.compile(r"[\"“‘(\[]*[A-Z0-9_]")


def split_passage(value: Any) -> list[str]:
    """The sentences and list items of a passage, in reading order, as the learner reads them.

    A table written into the passage is left out: it is read as a figure.
    """
    units = []
    for block in re.split(r"\n+", db.WALKTHROUGH_MARKUP.sub("", str(value or ""))):
        block = " ".join(block.split())
        if not block or block.startswith("|"):
            continue
        start = 0
        for found in SENTENCE_END.finditer(block):
            head = block[start:found.end()].rstrip()
            if (not SENTENCE_START.match(block[found.end():]) or head.split()[-1].lstrip("(\"“‘") in ABBREVIATIONS
                    or re.search(r"\b[A-Z]\.$", head)):
                continue
            units.append(head)
            start = found.end()
        if block[start:].strip():
            units.append(block[start:].strip())
    return units


def noted(value: Any) -> dict[str, str] | None:
    """An English and a Chinese text, both written, as plain text; None when it is anything else."""
    if not isinstance(value, dict) or not all(str(value.get(language) or "").strip() for language in LANGUAGES):
        return None
    texts = {language: " ".join(str(value[language]).split()) for language in LANGUAGES}
    return None if any("<" in text or "|" in text for text in texts.values()) else texts


def read_entries(path: Path) -> list[dict[str, Any]]:
    entries = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        entry = json.loads(line)
        where = f"{path}:{number}"
        question_id = str(entry.get("question_id") or "")
        approach = noted(entry.get("approach"))
        segments = entry.get("segments")
        choices = entry.get("choices")
        basis = entry.get("basis", "ai_draft")
        if not question_id or not approach or not isinstance(segments, list) or not isinstance(choices, dict):
            raise db.ContentError(f"{where}: needs question_id, approach, segments and choices")
        if basis not in BASES:
            raise db.ContentError(f"{where}: basis must be one of {', '.join(BASES)}")
        parts = []
        for segment in segments:
            texts = noted(segment)
            if (not texts or segment.get("field") not in db.WALKTHROUGH_FIELDS
                    or not db.walkthrough_text(segment.get("excerpt"))):
                raise db.ContentError(f"{where}: a segment needs field, excerpt, en and zh as plain text")
            parts.append({"field": segment["field"], "excerpt": db.walkthrough_text(segment["excerpt"]), **texts})
        notes = {str(label): noted(note) for label, note in choices.items()}
        if not all(notes.values()):
            raise db.ContentError(f"{where}: a choice needs en and zh as plain text")
        entries.append({"question_id": question_id, "approach": approach, "segments": parts, "choices": notes,
                        "basis": basis})
    return entries


def reading_questions(tables: db.Tables, root: Path = db.ROOT) -> set[str]:
    """The questions that take a walkthrough: published Reading and Writing questions."""
    connection = db.load_sqlite(tables, root)
    try:
        return {question_id for (question_id,) in connection.execute(
            "SELECT question_id FROM practice_pool WHERE status = 'published' AND section = 'reading_writing'")}
    finally:
        connection.close()


def add_entries(tables: db.Tables, entries: list[dict[str, Any]], takes: set[str]) -> None:
    """Add walkthroughs for questions in `takes`; one that comes in replaces the one its question had."""
    incoming = {entry["question_id"] for entry in entries}
    if len(incoming) != len(entries):
        raise db.ContentError("a question is entered twice")
    unknown = sorted(incoming - takes)
    if unknown:
        raise db.ContentError("not a published Reading and Writing question: " + ", ".join(unknown[:5]))
    for name in db.WALKTHROUGH_TABLES:
        tables[name] = [row for row in tables[name] if row["question_id"] not in incoming]
    for entry in entries:
        question_id = entry["question_id"]
        tables["walkthrough"].append({
            "question_id": question_id, "approach_en": entry["approach"]["en"], "approach_zh": entry["approach"]["zh"],
            "basis": entry["basis"]})
        tables["walkthrough_segment"] += [
            {"question_id": question_id, "position": position, "field": segment["field"],
             "excerpt": segment["excerpt"], "note_en": segment["en"], "note_zh": segment["zh"]}
            for position, segment in enumerate(entry["segments"], 1)]
        tables["walkthrough_choice"] += [
            {"question_id": question_id, "label": label, "note_en": note["en"], "note_zh": note["zh"]}
            for label, note in entry["choices"].items()]


def export_entries(tables: db.Tables) -> list[dict[str, Any]]:
    """Every walkthrough in the format read_entries takes, sorted by question."""
    segments: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for row in sorted(tables["walkthrough_segment"], key=lambda row: (row["question_id"], row["position"])):
        segments[row["question_id"]].append(
            {"field": row["field"], "excerpt": row["excerpt"], "en": row["note_en"], "zh": row["note_zh"]})
    choices: dict[str, dict[str, Any]] = collections.defaultdict(dict)
    for row in sorted(tables["walkthrough_choice"], key=lambda row: (row["question_id"], row["label"])):
        choices[row["question_id"]][row["label"]] = {"en": row["note_en"], "zh": row["note_zh"]}
    return [{"question_id": row["question_id"], "approach": {"en": row["approach_en"], "zh": row["approach_zh"]},
             "segments": segments[row["question_id"]], "choices": choices[row["question_id"]], "basis": row["basis"]}
            for row in sorted(tables["walkthrough"], key=lambda row: row["question_id"])]


def templates(tables: db.Tables, question_ids: list[str]) -> list[dict[str, Any]]:
    """An entry to fill in for each question: its text is in place, every en and zh is empty."""
    questions = {row["question_id"]: row for row in tables["question"]}
    labels: dict[str, list[str]] = collections.defaultdict(list)
    for row in db.sort_rows(tables["choice"], ["question_id", "label"]):
        labels[row["question_id"]].append(row["label"])
    unknown = [question_id for question_id in question_ids if question_id not in questions]
    if unknown:
        raise db.ContentError("not a question: " + ", ".join(unknown[:5]))
    empty = {language: "" for language in LANGUAGES}
    return [{"question_id": question_id, "approach": dict(empty),
             "segments": [{"field": "passage", "excerpt": unit, **empty}
                          for unit in split_passage(questions[question_id]["passage"])]
             + [{"field": "prompt", "excerpt": db.walkthrough_text(questions[question_id]["prompt"]), **empty}],
             "choices": {label: dict(empty) for label in labels[question_id]}}
            for question_id in question_ids]


def report(tables: db.Tables, root: Path) -> None:
    connection = db.load_sqlite(tables, root)
    try:
        print(f"{len(tables['walkthrough'])} walkthroughs, {len(tables['walkthrough_segment'])} segments, "
              f"{len(tables['walkthrough_choice'])} choice notes")
        for importance, _, questions, walked in connection.execute(
                "SELECT * FROM walkthrough_coverage ORDER BY level DESC"):
            print(f"  {importance:10s} {walked:5d} of {questions:5d} questions have a walkthrough")
        for basis, count in connection.execute("SELECT basis, count(*) FROM walkthrough GROUP BY basis ORDER BY basis"):
            print(f"  {count:5d} walkthroughs are {basis}")
    finally:
        connection.close()


def write_lines(path: Path, entries: list[dict[str, Any]]) -> None:
    path.write_text("".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in entries), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--entries", type=Path, action="append", default=[], help="JSON Lines file of walkthroughs")
    parser.add_argument("--template", nargs="+", metavar="ID", help="print an entry to fill in for these questions")
    parser.add_argument("--export", type=Path, help="write every walkthrough to this JSON Lines file")
    parser.add_argument("--apply", action="store_true", help="write the result to content/db")
    parser.add_argument("--root", type=Path, default=db.ROOT)
    args = parser.parse_args(argv)

    tables, problems = db.read_tables(args.root)
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 1
    try:
        if args.template:
            for entry in templates(tables, args.template):
                print(json.dumps(entry, ensure_ascii=False))
            return 0
        if args.export:
            entries = export_entries(tables)
            write_lines(args.export, entries)
            print(f"wrote {len(entries)} walkthroughs to {args.export}")
            return 0
        entries = [entry for path in args.entries for entry in read_entries(path)]
        add_entries(tables, entries, reading_questions(tables, args.root))
        tables = db.normalize(args.root, tables)
        problems = db.walkthrough_problems(tables)
        if problems:
            print("\n".join(problems[:40]), file=sys.stderr)
            return 1
    except db.ContentError as error:
        print(error, file=sys.stderr)
        return 1
    report(tables, args.root)
    print(f"{len(entries)} walkthrough(s) " + ("written" if args.apply else "would be written"))
    if args.apply:
        for name in db.write_tables(tables, args.root):
            print("wrote", name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
