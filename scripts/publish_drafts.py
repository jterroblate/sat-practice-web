#!/usr/bin/env python3
"""Turn draft Reading and Writing questions in content/db/ into published set files.

    python3 scripts/publish_drafts.py --dry-run          show what would be published
    python3 scripts/publish_drafts.py                    write set files, registry and tables
    python3 scripts/publish_drafts.py --tiers 1,2,3      include third-party material too

A draft is published only when all of this holds:
  - it is a four-choice Reading and Writing question with a skill, not flagged for review;
  - its best source is in an allowed tier (default 1 and 2: exam papers, question
    bank and practice tests);
  - its text converts cleanly to the site's markup: italics, underline and bold
    become markers, one simple table becomes table_data, anything else (images,
    superscripts, nested or merged tables, notes in another language) keeps it a draft;
  - it is not a near copy of a question that is already published or of another
    draft being published.

Each published draft goes into exactly one new material: the source it comes
from first. A source with fewer than MIN_MATERIAL new questions puts them in a
shared "supplement" collection for its family. Existing materials are never
changed. Nothing is checked for correctness here: answers and text are as
imported, and an explanation is present only where the source had one.
"""

from __future__ import annotations

import argparse
import collections
import html
import importlib.util
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import content_db as db  # noqa: E402


SECTION = "reading_writing"
MIN_MATERIAL = 10
FAMILY_ORDER = ["exam", "skill_bank", "sat_practice_test", "practice_set", "psat", "prep_test", "khan", "barrons"]
REGISTRY = "sat-content-registry.json"
IMPORT_FAMILY = "question-bank-import-unverified"
SKILL_FILE_FAMILY = "SAT机考题库/skill-difficulty"
SUPPLEMENT_TITLES = {
    "exam": "机考真题补充题", "skill_bank": "分类题库补充题", "sat_practice_test": "SAT Practice Test 补充题",
    "practice_set": "SAT机考套卷 Practice 补充题", "psat": "PSAT 补充题", "prep_test": "Digital SAT Prep Test 补充题",
    "khan": "可汗补充题", "barrons": "BARRON'S 补充题",
}
INLINE = {"i": "i", "em": "i", "u": "u", "b": "b", "strong": "b"}
BREAKING = {"p", "div", "br", "li", "ul", "ol"}
DROPPED = {"span", "font", "o"}
TAG = re.compile(r"<\s*(/?)\s*([a-zA-Z][a-zA-Z0-9]*)\b[^>]*>")
TABLE = re.compile(r"<table\b.*?</table\s*>", re.IGNORECASE | re.DOTALL)
OTHER_SCRIPT = re.compile(r"[㐀-鿿　-〿＀-￯]")


class NotPublishable(ValueError):
    """Why one draft stays a draft."""


def load_builder():
    path = db.ROOT / "skills/skill-sat-rw-classification/scripts/build_learning_experience.py"
    spec = importlib.util.spec_from_file_location("publish_drafts_builder", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def tidy(text: str) -> str:
    text = html.unescape(text).replace("\r\n", "\n").replace("\r", "\n").replace(" ", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" ?\n ?", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def to_markup(text: str | None, what: str) -> str:
    """Site inline markup for one piece of imported text, or NotPublishable."""
    def replace(match: re.Match) -> str:
        closing, name = match.group(1), match.group(2).lower()
        if name in INLINE:
            return f"[{closing}{INLINE[name]}]"
        if name in BREAKING:
            return "\n"
        if name in DROPPED:
            return ""
        raise NotPublishable(f"{what} uses <{name}>")

    converted = tidy(TAG.sub(replace, text or ""))
    # Markers left empty or wrapped around nothing but space add nothing.
    converted = re.sub(r"\[(i|u|b)\](\s*)\[/\1\]", r"\2", converted)
    if OTHER_SCRIPT.search(converted):
        raise NotPublishable(f"{what} has a note that is not in English")
    return converted


def table_data(block: str) -> dict:
    if re.search(r"(?:col|row)span\s*=\s*[\"']?(?!1\b)\d", block, re.IGNORECASE) or len(TABLE.findall(block[1:])) > 0:
        raise NotPublishable("table has merged or nested cells")
    caption = re.search(r"<caption\b[^>]*>(.*?)</caption\s*>", block, re.IGNORECASE | re.DOTALL)
    rows = []
    for row in re.findall(r"<tr\b[^>]*>(.*?)</tr\s*>", block, re.IGNORECASE | re.DOTALL):
        cells = re.findall(r"<t([dh])\b[^>]*>(.*?)</t[dh]\s*>", row, re.IGNORECASE | re.DOTALL)
        if cells:
            rows.append([re.sub(r"\s*\n\s*", " ", to_markup(re.sub(r"</?(?:strong|b)\b[^>]*>", "", cell, flags=re.IGNORECASE), "table cell"))
                         for _, cell in cells])
    if len(rows) < 2 or len({len(row) for row in rows}) != 1:
        raise NotPublishable("table rows do not line up")
    result = {"title": re.sub(r"\s*\n\s*", " ", to_markup(caption.group(1), "table title")) if caption else "",
              "columns": rows[0], "rows": rows[1:]}
    if any("[" in cell and re.search(r"\[/?[iub]\]", cell) for row in rows for cell in row):
        raise NotPublishable("table cell has inline markup")
    return result


def convert(question: dict, choices: list[dict], explanation: str | None, builder) -> dict:
    passage = question["passage"]
    tables = TABLE.findall(passage)
    if len(tables) > 1:
        raise NotPublishable("more than one table")
    table = table_data(tables[0]) if tables else None
    if "<img" in passage.lower() or "<img" in question["prompt"].lower():
        raise NotPublishable("has an image")
    record = {
        "passage": to_markup(TABLE.sub("\n", passage), "passage"),
        "prompt": to_markup(question["prompt"], "prompt"),
        "choices": [{"label": row["label"], "text": to_markup(row["text"], "choice " + row["label"])} for row in choices],
    }
    if [row["label"] for row in choices] != ["A", "B", "C", "D"] or any(row["image"] for row in choices):
        raise NotPublishable("does not have four text choices")
    if any(not choice["text"] for choice in record["choices"]) or not record["prompt"]:
        raise NotPublishable("has an empty prompt or choice")
    if not record["passage"] and table is None:
        raise NotPublishable("has no passage")
    try:
        for name, text in [("passage", record["passage"]), ("prompt", record["prompt"]),
                           *[("choice", choice["text"]) for choice in record["choices"]]]:
            builder.normalize_public_inline_markup(text, name)
    except ValueError as error:
        raise NotPublishable("text the site markup does not allow") from error
    if table is not None:
        record["table_data"] = table
    detailed = ""
    if explanation and "<img" not in explanation.lower():
        try:
            detailed = to_markup(re.sub(r"</?(?:table|tr|td|th|tbody|thead|caption|sup|sub|small|center|a|var|hr)\b[^>]*>", " ", explanation, flags=re.IGNORECASE), "explanation")
            builder.normalize_public_inline_markup(detailed, "explanation")
        except (NotPublishable, ValueError):
            detailed = ""
    record["explanation"] = {"short": "", "key_evidence": "", "why_correct": "", "detailed": detailed,
                             "choice_analysis": {row["label"]: "" for row in choices}}
    return record


def words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", re.sub(r"\[/?\w+\]", "", html.unescape(re.sub(r"<[^>]*>", " ", text or ""))).lower())


def shingles(passage: str, prompt: str) -> frozenset:
    tokens = words(passage) + ["|"] + words(prompt)
    return frozenset(zip(tokens, tokens[1:], tokens[2:])) or frozenset([tuple(tokens)])


def near_copies(items: list[tuple[str, frozenset, tuple, str]]) -> tuple[dict[str, str], set[str]]:
    """Find questions that nearly repeat an earlier one: same text and same choices.

    Returns the later-to-earlier mapping, and the ids on either side of a pair
    whose correct answers differ: those need a person, not a merge.
    """
    index: dict[tuple, list[int]] = collections.defaultdict(list)
    copies: dict[str, str] = {}
    disputed: set[str] = set()
    for position, (key, grams, options, answer) in enumerate(items):
        seen = collections.Counter(other for gram in grams for other in index.get(gram, ()))
        match = None
        for other, shared in seen.most_common(8):
            other_key, other_grams, other_options, other_answer = items[other]
            overlap = shared / (len(grams) + len(other_grams) - shared)
            same_options = sum((collections.Counter(options) & collections.Counter(other_options)).values())
            if overlap >= 0.8 and same_options >= 3:
                match = other_key
                if answer != other_answer:
                    disputed.update((key, other_key))
                break
        if match is not None:
            copies[key] = match
            continue
        for gram in grams:
            index[gram].append(position)
    return copies, disputed


def answer_text(answer: str, options: list[str]) -> str:
    """The correct choice in a form that survives re-typesetting but not a changed answer."""
    loose = [" ".join(words(option)) for option in options]
    exact = re.sub(r"\s+", "", html.unescape(re.sub(r"<[^>]*>|\[/?\w+\]", "", answer))).lower()
    return " ".join(words(answer)) if len(set(loose)) == len(loose) else exact


def ascii_slug(text: str) -> str:
    text = text.split("/")[0].replace("亚太", " apac ").replace("北美", " na ")
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def material_identity(source: dict, family: str) -> tuple[str, str, str, str]:
    """(kind, slug, title, difficulty) for the material a source becomes."""
    label = source["label"]
    if family == "exam":
        number = re.search(r"第(\d+)套", label)
        inside = re.search(r"\(([^)]*)\)", label)
        parts = ["exam", str(source["year"] or "undated"), f"{source['month']:02d}" if source["month"] else "",
                 f"s{number.group(1)}" if number else "", ascii_slug(inside.group(1).replace("卷", " ")) if inside else ""]
        title = re.sub(r"\(([^)/]*)/[^)]*\)", r"(\1)", label)
        title = re.sub(r"\s*读写\d+个Module$", "", title)
        return "set", "-".join(part for part in parts if part), title, "mixed"
    if family == "skill_bank":
        _, *skill, level = label.split("/")
        name = "/".join(skill)
        return "collection", f"bank-{ascii_slug(name)}-{level.lower()}", f"{name} · {level}", level.lower()
    return "set", f"practice-{ascii_slug(label)}", label, "mixed"


def plan(root: Path, tiers: set[int]) -> dict:
    tables, problems = db.read_tables(root)
    if problems:
        raise db.ContentError("content/db is not clean; run check first:\n" + "\n".join(problems[:10]))
    builder = load_builder()

    def index(name: str, key: str) -> dict:
        return {row[key]: row for row in tables[name]}

    subtypes, skills, domains = index("subtype", "subtype_id"), index("skill", "skill_id"), index("domain", "domain_id")
    sources, families = index("source", "source_id"), index("source_family", "family_id")
    choices: dict[str, list[dict]] = collections.defaultdict(list)
    for row in tables["choice"]:
        choices[row["question_id"]].append(row)
    for rows in choices.values():
        rows.sort(key=lambda row: row["label"])
    explanations = {row["question_id"]: row["detailed"] for row in tables["explanation"]}
    placements: dict[str, list[dict]] = collections.defaultdict(list)
    for row in tables["question_source"]:
        placements[row["question_id"]].append(row)

    def taxonomy(question: dict) -> tuple[dict, dict, dict]:
        subtype = subtypes[question["subtype_id"]]
        skill = skills[subtype["skill_id"]]
        return subtype, skill, domains[skill["domain_id"]]

    def first_placement(question_id: str) -> dict | None:
        rows = [row for row in placements[question_id] if sources[row["source_id"]]["family_id"]]
        return min(rows, key=lambda row: (FAMILY_ORDER.index(sources[row["source_id"]]["family_id"]),
                                          sources[row["source_id"]]["label"], row["module_index"], row["number"]),
                   default=None)

    kept_out = collections.Counter()
    ready = []
    for question in tables["question"]:
        subtype, skill, domain = taxonomy(question)
        if question["status"] != "draft" or domain["section"] != SECTION:
            continue
        placement = first_placement(question["question_id"])
        if placement is None:
            kept_out["no known source"] += 1
            continue
        best_tier = min(families[sources[row["source_id"]]["family_id"]]["tier"]
                        for row in placements[question["question_id"]])
        try:
            if best_tier not in tiers:
                raise NotPublishable(f"source tier {best_tier} is not selected")
            if question["format"] != "choice":
                raise NotPublishable("is not a multiple-choice question")
            if question["needs_manual_review"]:
                raise NotPublishable("is flagged for review")
            if question["skill_basis"] == "none":
                raise NotPublishable("has no skill yet")
            record = convert(question, choices[question["question_id"]], explanations.get(question["question_id"]), builder)
        except NotPublishable as reason:
            kept_out[re.sub(r" <\w+>| [A-D]$", "", str(reason))] += 1
            continue
        ready.append({"question": question, "record": record, "placement": placement, "tier": best_tier,
                      "classification": {"domain": domain["name"], "skill": skill["name"],
                                         **({"subtype": subtype["name"]} if subtype["name"] is not None else {})}})

    # Near copies: published questions come first so a draft never displaces one.
    items = []
    for question in tables["question"]:
        if question["status"] == "published":
            options = [row["text"] for row in choices[question["question_id"]]]
            right = next(row["text"] for row in choices[question["question_id"]] if row["label"] == question["correct_answer"])
            items.append((question["question_id"], shingles(question["passage"], question["prompt"]),
                          tuple(sorted(" ".join(words(text)) for text in options)), answer_text(right, options)))
    ready.sort(key=lambda item: (item["tier"], not item["record"]["explanation"]["detailed"], item["question"]["question_id"]))
    for item in ready:
        options = [choice["text"] for choice in item["record"]["choices"]]
        right = next(choice["text"] for choice in item["record"]["choices"] if choice["label"] == item["question"]["correct_answer"])
        items.append((item["question"]["question_id"],
                      shingles(item["record"]["passage"], item["record"]["prompt"]),
                      tuple(sorted(" ".join(words(text)) for text in options)), answer_text(right, options)))
    copies, disputed = near_copies(items)
    published_ids = {q["question_id"] for q in tables["question"] if q["status"] == "published"}
    answer_conflicts = []
    for item in ready:
        question_id = item["question"]["question_id"]
        target = copies.get(question_id)
        if question_id in disputed:
            kept_out["near copy of another question with a different answer"] += 1
            if target in published_ids:
                answer_conflicts.append({"published": target, "draft": question_id,
                                         "source": sources[item["placement"]["source_id"]]["label"],
                                         "number": item["placement"]["number"]})
        elif target is not None:
            kept_out["near copy of a published question" if target in published_ids
                     else "near copy of another draft being published"] += 1
    ready = [item for item in ready
             if item["question"]["question_id"] not in copies and item["question"]["question_id"] not in disputed]

    by_source: dict[str, list[dict]] = collections.defaultdict(list)
    for item in ready:
        by_source[item["placement"]["source_id"]].append(item)
    registry = json.loads((root / REGISTRY).read_text(encoding="utf-8"))
    used_routes = {entry["route"] for entry in registry["entries"]}
    used_titles = {entry["title"] for entry in registry["entries"]}
    used_ids = {entry["contentId"] for entry in registry["entries"]}
    materials = []
    supplements: dict[str, list[dict]] = collections.defaultdict(list)

    def add_material(kind: str, slug: str, title: str, difficulty: str, family: str, source_id: str | None,
                     members: list[dict]) -> None:
        folder = "sets" if kind == "set" else "collections"
        base, counter = slug, 2
        while f"{folder}/{slug}.html" in used_routes or f"{kind}-{slug}" in used_ids:
            slug, counter = f"{base}-{counter}", counter + 1
        while title in used_titles:
            title += " · 补充"
        used_routes.add(f"{folder}/{slug}.html")
        used_ids.add(f"{kind}-{slug}")
        used_titles.add(title)
        materials.append({"kind": kind, "content_id": f"{kind}-{slug}", "route": f"{folder}/{slug}.html",
                          "data_path": f"content/canonical/{folder}/{slug}.json", "title": title,
                          "difficulty": difficulty, "source_id": source_id, "members": members,
                          "source_family": SKILL_FILE_FAMILY if family == "skill_bank" else IMPORT_FAMILY})

    def source_order(source_id: str) -> tuple:
        source = sources[source_id]
        return (FAMILY_ORDER.index(source["family_id"]), source["year"] or 0, source["month"] or 0, source["label"])

    for source_id in sorted(by_source, key=source_order):
        members = sorted(by_source[source_id], key=lambda item: (item["placement"]["module_index"], item["placement"]["number"]))
        source = sources[source_id]
        if len(members) < MIN_MATERIAL:
            supplements[source["family_id"]].extend(members)
            continue
        kind, slug, title, difficulty = material_identity(source, source["family_id"])
        already = sum(1 for row in tables["question_source"]
                      if row["source_id"] == source_id and row["question_id"] in published_ids)
        if source["family_id"] != "skill_bank" and already >= 5:
            title += " · 补充"
        add_material(kind, slug, title, difficulty, source["family_id"], source_id, members)
    for family in FAMILY_ORDER:
        if supplements[family]:
            add_material("collection", f"supplement-{ascii_slug(family)}", SUPPLEMENT_TITLES[family], "mixed", family, None,
                         supplements[family])

    return {"tables": tables, "sources": sources, "registry": registry, "materials": materials,
            "kept_out": dict(kept_out), "answer_conflicts": answer_conflicts,
            "published": sum(len(material["members"]) for material in materials)}


def write(root: Path, result: dict) -> list[str]:
    tables, sources, registry = result["tables"], result["sources"], result["registry"]
    changed = []
    for material in result["materials"]:
        questions = []
        for number, item in enumerate(material["members"], 1):
            record = item["record"]
            source = sources[item["placement"]["source_id"]]
            questions.append({
                "question_id": item["question"]["question_id"], "section": SECTION, "question_number": number,
                "passage": record["passage"], "prompt": record["prompt"], "choices": record["choices"],
                "correct_answer": item["question"]["correct_answer"], "needs_manual_review": False,
                "source_set": source["label"], "source_question_number": item["placement"]["number"],
                **({"table_data": record["table_data"]} if "table_data" in record else {}),
                "explanation": record["explanation"], "classification": item["classification"],
            })
        document = {"schemaVersion": "sat-canonical-question-collection-v1", "contentId": material["content_id"],
                    "title": material["title"], "section": SECTION, "questionCount": len(questions),
                    "questions": questions}
        path = root / material["data_path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        changed.append(material["data_path"])
        ids = [question["question_id"] for question in questions]
        registry["entries"].append({
            "baselineQuestionIds": ids, "canonicalPriority": 100, "contentId": material["content_id"],
            "dataPath": material["data_path"], "difficulty": material["difficulty"], "examEligible": False,
            "examVariants": [], "kind": material["kind"], "moduleSpec": None, "route": material["route"],
            "section": SECTION, "sourceFamily": material["source_family"], "status": "published",
            "title": material["title"], "questionIds": ids,
            # Shown only when the owner switches the expanded bank on (sat-release.json).
            "releaseGate": "expandedQuestionBank",
        })
    (root / REGISTRY).write_text(json.dumps(registry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    changed.append(REGISTRY)

    merged = db.normalize(root, tables)
    reproduced = {material["content_id"]: material["source_id"] for material in result["materials"]}
    for row in merged["material"]:
        if row["content_id"] in reproduced:
            row["source_id"] = reproduced[row["content_id"]]
    merged = db.normalize(root, merged)
    db.load_sqlite(merged, root).close()
    trouble = db.compare_with_canonical(merged, root)
    if trouble:
        raise db.ContentError("the tables would not reproduce the set files:\n" + "\n".join(trouble[:10]))
    return changed + db.write_tables(merged, root)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--tiers", default="1,2", help="source tiers to publish (default: 1,2)")
    parser.add_argument("--dry-run", action="store_true", help="report only; write nothing")
    parser.add_argument("--root", type=Path, default=db.ROOT, help=argparse.SUPPRESS)
    arguments = parser.parse_args(argv)
    try:
        tiers = {int(value) for value in arguments.tiers.split(",")}
        result = plan(arguments.root.resolve(), tiers)
        summary = {
            "questions_to_publish": result["published"],
            "new_materials": collections.Counter(material["kind"] for material in result["materials"]),
            "kept_as_drafts": dict(sorted(result["kept_out"].items(), key=lambda item: -item[1])),
            "answer_differs_from_published": result["answer_conflicts"],
            "materials": [f"{material['route']}  {len(material['members']):3d}  {material['title']}"
                          for material in result["materials"]],
        }
        if not arguments.dry_run and result["materials"]:
            summary["files_changed"] = len(write(arguments.root.resolve(), result))
    except (db.ContentError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
