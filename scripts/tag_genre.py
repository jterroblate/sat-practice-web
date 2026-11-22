#!/usr/bin/env python3
"""Record the literary genre of Reading and Writing passages in content/db.

    python3 scripts/tag_genre.py            # report what would change
    python3 scripts/tag_genre.py --apply    # write content/db/question.jsonl

question.genre is 'poetry', 'fiction' or 'drama' when the passage says so itself,
and empty otherwise (informational texts, and every Math question). Only two
wordings count, both written by the test's own editors:

  - the introduction of an excerpt: "The following text is from Jane Austen's
    1811 novel Sense and Sensibility." (also "Text 1 is ... poem ...");
  - the opening of a quotation question: "Cane is a 1923 novel by Jean Toomer."

A text that merely discusses a novel or a poem is informational and stays empty.
The rule is deterministic, so running it again changes nothing.
"""

from __future__ import annotations

import argparse
import collections
import html
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import content_db as db  # noqa: E402


GENRES = ("poetry", "fiction", "drama")
SECTION = "reading_writing"
WORDS = {
    "poem": "poetry", "poems": "poetry", "poetry": "poetry", "sonnet": "poetry", "ode": "poetry",
    "novel": "fiction", "novella": "fiction", "short story": "fiction", "story": "fiction",
    "short-story": "fiction", "tale": "fiction", "fable": "fiction",
    "play": "drama",
}
WORD = r"(poems?|poetry|sonnet|ode|novel(?:la)?|short[ -]story|story|tale|fable|play)"
# "The following text is [adapted] from <author>'s <year> <kind> ..." and the paired-text form.
INTRODUCTION = re.compile(
    r"^(?:Text 1\s*)?(?:The following text is|This text is|is)\b(?:(?!\bText 2\b)[^\n]){0,160}?\b" + WORD + r"\b",
    re.IGNORECASE)
# "<Title> is a[n] [year] <kind> by <author>."
TITLE = re.compile(
    r"^[^\n]{1,90}?\bis an? (?:\d{4} |[a-z-]+ ){0,3}" + WORD + r"\b[^\n.]{0,40}\bby\b", re.IGNORECASE)
# Kinds that make the excerpt non-literary even when a literary word follows
# ("from a book about the novel", "an essay on poetry").
NOT_LITERARY = re.compile(r"\b(essay|article|book|memoir|autobiography|speech|letter|study|review|lecture|address|history|biography)\b",
                          re.IGNORECASE)


def plain(text: str | None) -> str:
    text = re.sub(r"</(?:p|div|tr|caption|li|h\d)>|<br\s*/?>", "\n", text or "", flags=re.IGNORECASE)
    text = html.unescape(re.sub(r"<[^>]*>", "", text))
    text = re.sub(r"\[/?(?:vocab|i|u|b)\]", "", text)
    return re.sub(r"[ \t ]+", " ", re.sub(r"\s*\n\s*", " ", text)).strip()


def genre_of(passage: str | None) -> str | None:
    # An editor's note in front of the passage is not part of its wording.
    text = re.sub(r"^校订说明：.*?。\s*(?=The following|This text|Text 1)", "", plain(passage))[:400]
    for pattern in (INTRODUCTION, TITLE):
        match = pattern.match(text)
        if match and not NOT_LITERARY.search(match.group(0)):
            return WORDS[match.group(1).lower()]
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="write the result to content/db")
    parser.add_argument("--root", type=Path, default=db.ROOT)
    args = parser.parse_args(argv)

    tables, problems = db.read_tables(args.root)
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 1
    skills = {row["skill_id"]: row for row in tables["skill"]}
    domains = {row["domain_id"]: row for row in tables["domain"]}
    section = {row["subtype_id"]: domains[skills[row["skill_id"]]["domain_id"]]["section"]
               for row in tables["subtype"]}

    counts: collections.Counter = collections.Counter()
    changed = 0
    for row in tables["question"]:
        genre = genre_of(row["passage"]) if section[row["subtype_id"]] == SECTION else None
        counts[(row["status"], genre or "-")] += 1
        if row.get("genre") != genre:
            row["genre"] = genre
            changed += 1
    for (status, genre), count in sorted(counts.items()):
        print(f"{status:10} {genre:8} {count}")
    print(f"{changed} question(s) would change" if not args.apply else f"{changed} question(s) changed")
    if args.apply:
        db.load_sqlite(tables, args.root).close()
        for name in db.write_tables(tables, args.root):
            print("wrote", name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
