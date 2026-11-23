#!/usr/bin/env python3
"""Explain the hard words of Reading and Writing questions in content/db.

    python3 scripts/annotate_vocabulary.py                          report how far the explanations reach
    python3 scripts/annotate_vocabulary.py --entries FILE --apply   add or replace explanations, then link them
    python3 scripts/annotate_vocabulary.py --scope all --apply      link the known words in more questions
    python3 scripts/annotate_vocabulary.py --candidates             list the words of the scope nothing explains yet
    python3 scripts/annotate_vocabulary.py --export FILE            write every explanation to a file, to read and correct

Four tables hold the result:

vocabulary
    One word in one sense: a plain English definition and a Chinese one. The
    wording is written for a learner, so the English definition comes first
    and uses easier words than the one it explains. It is written for this
    bank; nothing is copied from a dictionary.
vocabulary_example
    At least three sentences that show the word in use, each with its
    translation, in the order the learner reads them. None comes from a
    question.
vocabulary_form
    The spellings an explanation is found by, in lower case ("contend",
    "contends", "contended"). A spelling belongs to one explanation.
question_vocabulary
    Which explained words a question contains, in which field, spelled as
    they stand there, and when the learner may open them.

An entries file is JSON Lines, one explanation per line:

    {"forms": ["abut", "abuts"], "headword": "abut", "part_of_speech": "v.",
     "definition_en": "...", "definition_zh": "...",
     "examples": [{"en": "...", "zh": "..."}, {"en": "...", "zh": "..."}, {"en": "...", "zh": "..."}]}

"id" may be given; otherwise it is made from the headword. "basis" is
'ai_draft' unless the line says 'human_checked'.

--export writes the same format, so the list can be read through, corrected
and brought back with --entries: an entry that comes back replaces the stored
one, its "forms" replace its spellings and its "examples" replace its example
sentences. A link whose spelling no entry
claims any more is removed. Exporting and importing unchanged changes nothing.

Linking reads the question text the way the practice page does and follows
three rules, so running it again changes nothing:

  - A word counts when it is written in lower case, or capitalised at the
    start of a sentence, a choice or a list item. A capitalised word inside a
    sentence is a name ("Clementina Rind") and is left alone.
  - Words in the answer choices open only after the answer is submitted. So
    does the word a "most nearly mean" question asks about, in the passage and
    in the prompt. Everything else in the passage and the prompt is open from
    the start.
  - A link that is already recorded is never changed, so a reader can point a
    word in one question at another sense by editing question_vocabulary.

--scope chooses the questions to link: 'core' (the default), 'important'
(core and important), 'all', or question ids. Only published Reading and
Writing questions are linked.
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

ENTRY_FIELDS = ("headword", "part_of_speech", "definition_en", "definition_zh")
EXAMPLE_FIELDS = ("en", "zh")
BASES = ("ai_draft", "human_checked")
SCOPES = {"core": ("core",), "important": ("core", "important"), "all": ("core", "important", "basic")}
QUOTED_WORD = re.compile(r"[\"“]([A-Za-z'’\-]+)[\"”]")


slug = db.vocabulary_id
spelling = db.gloss_spelling


def read_entries(path: Path) -> list[dict[str, Any]]:
    entries = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        entry = json.loads(line)
        missing = [field for field in ENTRY_FIELDS if not str(entry.get(field) or "").strip()]
        forms = [spelling(str(form)) for form in entry.get("forms") or []]
        if missing or not forms:
            raise db.ContentError(f"{path}:{number}: needs forms and {', '.join(missing or ENTRY_FIELDS)}")
        examples = entry.get("examples")
        if (not isinstance(examples, list) or len(examples) < db.VOCABULARY_EXAMPLES
                or any(not isinstance(example, dict) or set(example) != set(EXAMPLE_FIELDS)
                       or not all(str(example[field] or "").strip() for field in EXAMPLE_FIELDS)
                       for example in examples)
                or len({example["en"].strip() for example in examples}) != len(examples)):
            raise db.ContentError(f"{path}:{number}: needs at least {db.VOCABULARY_EXAMPLES} different examples, "
                                  "each with en and zh")
        texts = [str(entry[field]) for field in ENTRY_FIELDS]
        texts += [str(example[field]) for example in examples for field in EXAMPLE_FIELDS]
        if any("|" in text or "<" in text for text in texts):
            raise db.ContentError(f"{path}:{number}: explanations are plain text")
        basis = entry.get("basis", "ai_draft")
        if basis not in BASES:
            raise db.ContentError(f"{path}:{number}: basis must be one of {', '.join(BASES)}")
        entries.append({"vocab_id": entry.get("id") or slug(entry["headword"]), "forms": forms, "basis": basis,
                        "examples": [{field: str(example[field]).strip() for field in EXAMPLE_FIELDS}
                                     for example in examples],
                        **{field: str(entry[field]).strip() for field in ENTRY_FIELDS}})
    return entries


def add_entries(tables: db.Tables, entries: list[dict[str, Any]]) -> int:
    """Add or replace explanations with their examples and spellings; return how many links lost their spelling.

    The spellings and the examples of an entry that comes in replace the ones
    it had. A link whose spelling no entry claims afterwards is removed, so
    that linking the same questions again would not bring it back under
    another rule.
    """
    rows = {row["vocab_id"]: row for row in tables["vocabulary"]}
    incoming = {entry["vocab_id"] for entry in entries}
    forms = {row["form"]: row["vocab_id"] for row in tables["vocabulary_form"] if row["vocab_id"] not in incoming}
    examples = [row for row in tables["vocabulary_example"] if row["vocab_id"] not in incoming]
    for entry in entries:
        rows[entry["vocab_id"]] = {"vocab_id": entry["vocab_id"], **{field: entry[field] for field in ENTRY_FIELDS},
                                   "basis": entry["basis"]}
        for form in entry["forms"]:
            if forms.get(form, entry["vocab_id"]) != entry["vocab_id"]:
                raise db.ContentError(f"'{form}' already belongs to {forms[form]}, not {entry['vocab_id']}")
            forms[form] = entry["vocab_id"]
        examples += [{"vocab_id": entry["vocab_id"], "position": position,
                      "example_en": example["en"], "example_zh": example["zh"]}
                     for position, example in enumerate(entry["examples"], 1)]
    kept = [row for row in tables["question_vocabulary"] if spelling(row["surface"]) in forms]
    dropped = len(tables["question_vocabulary"]) - len(kept)
    tables["vocabulary"] = list(rows.values())
    tables["vocabulary_example"] = examples
    tables["vocabulary_form"] = [{"form": form, "vocab_id": vocab_id} for form, vocab_id in forms.items()]
    tables["question_vocabulary"] = kept
    return dropped


def export_entries(tables: db.Tables) -> list[dict[str, Any]]:
    """Every explanation with its spellings and examples, in the format read_entries takes, sorted by id."""
    forms: dict[str, list[str]] = collections.defaultdict(list)
    for row in tables["vocabulary_form"]:
        forms[row["vocab_id"]].append(row["form"])
    examples: dict[str, list[dict[str, str]]] = collections.defaultdict(list)
    for row in sorted(tables["vocabulary_example"], key=lambda row: (row["vocab_id"], row["position"])):
        examples[row["vocab_id"]].append({"en": row["example_en"], "zh": row["example_zh"]})
    return [{"id": row["vocab_id"], "forms": sorted(forms[row["vocab_id"]]),
             **{field: row[field] for field in ENTRY_FIELDS}, "examples": examples[row["vocab_id"]],
             "basis": row["basis"]}
            for row in sorted(tables["vocabulary"], key=lambda row: row["vocab_id"])]


def scope_questions(connection, scope: list[str]) -> list[tuple[str, str | None]]:
    """(question_id, task) of the published Reading and Writing questions in scope."""
    rows = connection.execute(
        "SELECT question_id, task_id, importance FROM practice_pool"
        " WHERE status = 'published' AND section = 'reading_writing' ORDER BY question_id").fetchall()
    if len(scope) == 1 and scope[0] in SCOPES:
        return [(question_id, task) for question_id, task, importance in rows if importance in SCOPES[scope[0]]]
    known = {question_id for question_id, _, _ in rows}
    unknown = [question_id for question_id in scope if question_id not in known]
    if unknown:
        raise db.ContentError("not a published Reading and Writing question: " + ", ".join(unknown[:5]))
    return [(question_id, task) for question_id, task, _ in rows if question_id in set(scope)]


def link(tables: db.Tables, questions: list[tuple[str, str | None]]) -> int:
    """Record the explained words of the given questions; return how many links were added."""
    forms = {row["form"]: row["vocab_id"] for row in tables["vocabulary_form"]}
    texts = db.gloss_texts(tables)
    recorded = {(row["question_id"], row["field"], row["surface"]) for row in tables["question_vocabulary"]}
    added = 0
    for question_id, task in questions:
        tested = set()
        if task == "wic_meaning":
            tested = {spelling(word) for word in QUOTED_WORD.findall(texts[question_id]["prompt"])}
        for field in db.GLOSS_FIELDS:
            text = db.GLOSS_MARKUP.sub(lambda found: " " * len(found.group(0)), texts[question_id][field])
            for found in db.gloss_words(text):
                surface = found.group(0)
                vocab_id = forms.get(spelling(surface))
                if not vocab_id or (question_id, field, surface) in recorded or not db.gloss_is_ordinary(text, found):
                    continue
                later = field == "choices" or spelling(surface) in tested
                tables["question_vocabulary"].append({
                    "question_id": question_id, "field": field, "surface": surface, "vocab_id": vocab_id,
                    "reveal": "after_submit" if later else "always"})
                recorded.add((question_id, field, surface))
                added += 1
    return added


def candidates(tables: db.Tables, questions: list[tuple[str, str | None]]) -> list[tuple[int, str]]:
    """Lower-case words of the scope that nothing explains, with the number of bank questions they occur in."""
    forms = {row["form"] for row in tables["vocabulary_form"]}
    texts = db.gloss_texts(tables)
    published = {row["question_id"] for row in tables["question"] if row["status"] == "published"}
    spread: collections.Counter = collections.Counter()
    for question_id in published:
        spread.update({spelling(found.group(0)) for field in db.GLOSS_FIELDS
                       for found in db.gloss_words(texts[question_id][field]) if found.group(0).islower()})
    wanted = set()
    for question_id, _ in questions:
        wanted |= {spelling(found.group(0)) for field in db.GLOSS_FIELDS
                   for found in db.gloss_words(texts[question_id][field]) if found.group(0).islower()}
    return sorted((spread[word], word) for word in wanted - forms if len(word) > 2)


def report(tables: db.Tables, root: Path) -> None:
    connection = db.load_sqlite(tables, root)
    try:
        print(f"{len(tables['vocabulary'])} explanations, {len(tables['vocabulary_example'])} examples, "
              f"{len(tables['vocabulary_form'])} spellings, {len(tables['question_vocabulary'])} words linked in questions")
        for importance, _, questions, annotated in connection.execute(
                "SELECT * FROM vocabulary_coverage ORDER BY level DESC"):
            print(f"  {importance:10s} {annotated:5d} of {questions:5d} questions have explained words")
        for basis, count in connection.execute("SELECT basis, count(*) FROM vocabulary GROUP BY basis ORDER BY basis"):
            print(f"  {count:5d} explanations are {basis}")
    finally:
        connection.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--entries", type=Path, action="append", default=[], help="JSON Lines file of explanations")
    parser.add_argument("--scope", nargs="+", default=["core"], help="core, important, all, or question ids")
    parser.add_argument("--candidates", action="store_true", help="list unexplained words of the scope, rarest first")
    parser.add_argument("--export", type=Path, help="write every explanation to this JSON Lines file")
    parser.add_argument("--apply", action="store_true", help="write the result to content/db")
    parser.add_argument("--root", type=Path, default=db.ROOT)
    args = parser.parse_args(argv)

    tables, problems = db.read_tables(args.root)
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 1
    try:
        connection = db.load_sqlite(tables, args.root)
        questions = scope_questions(connection, args.scope)
        connection.close()
        if args.candidates:
            for count, word in candidates(tables, questions):
                print(f"{count:5d}  {word}")
            return 0
        if args.export:
            entries = export_entries(tables)
            args.export.write_text("".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in entries), encoding="utf-8")
            print(f"wrote {len(entries)} explanations to {args.export}")
            return 0
        dropped = sum(add_entries(tables, read_entries(path)) for path in args.entries)
        if dropped:
            print(f"{dropped} link(s) removed: their spelling belongs to no explanation any more")
        added = link(tables, questions)
        tables = db.normalize(args.root, tables)
        problems = db.vocabulary_problems(tables)
        if problems:
            print("\n".join(problems[:40]), file=sys.stderr)
            return 1
    except db.ContentError as error:
        print(error, file=sys.stderr)
        return 1
    report(tables, args.root)
    print(f"{added} link(s) " + ("added" if args.apply else "would be added") + f" in {len(questions)} question(s) in scope")
    if args.apply:
        for name in db.write_tables(tables, args.root):
            print("wrote", name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
