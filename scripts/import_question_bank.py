#!/usr/bin/env python3
"""Import a folder of exported practice pages into content/db/ as draft questions.

Each page embeds its questions as `const DATA = {...}`. The pages themselves stay
outside the repository; pass their folder with --raw.

    python3 scripts/import_question_bank.py --raw /path/to/pages
    python3 scripts/import_question_bank.py --raw /path/to/pages --families exam,skill_bank

Third-party prep books (source tier 3) are skipped unless --families names
them. This repository is public and its tables hold none of their questions;
a test fails if a source of such a family is committed.

What it does:
  - records every page as a source (family, year, month, region) with its modules;
  - merges identical questions, and recognises ones already published here;
  - adds the rest as drafts with new ids, classified from the page title where
    the page is a skill file, otherwise by the repository's rule tagger when it
    is confident, otherwise left unclassified;
  - records where each question sat in each source.

Published questions are never rewritten: they only gain source placements and,
when a skill file states one, a difficulty. Running it again changes nothing,
and drafts from other folders are left as they are.
Text is stored as found, HTML included, and remote image addresses are kept as
they are; converting those is part of publishing a draft, not of importing it.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import html
import importlib.util
import json
from pathlib import Path
import re
import sys
import unicodedata

sys.path.insert(0, str(Path(__file__).resolve().parent))
import content_db as db  # noqa: E402


TIERS = [
    {"tier": 1, "name": "真题", "kind": "real_exam"},
    {"tier": 2, "name": "题库与练习套卷", "kind": "practice"},
    {"tier": 3, "name": "第三方教辅", "kind": "practice"},
]
# Order is the order questions are numbered in, and which copy of a repeated
# question supplies the text.
FAMILIES = [
    {"family_id": "exam", "name": "机考真题（回忆版，来源未认证）", "tier": 1},
    {"family_id": "skill_bank", "name": "分类题库", "tier": 2},
    {"family_id": "sat_practice_test", "name": "SAT Practice Test", "tier": 2},
    {"family_id": "practice_set", "name": "SAT机考套卷 Practice", "tier": 2},
    {"family_id": "psat", "name": "PSAT", "tier": 2},
    {"family_id": "prep_test", "name": "Digital SAT Prep Test", "tier": 3},
    {"family_id": "khan", "name": "可汗", "tier": 3},
    {"family_id": "barrons", "name": "BARRON'S Digital SAT", "tier": 3},
]
FAMILY_ORDER = [row["family_id"] for row in FAMILIES]
# Imported when --families is not given: everything but third-party prep books.
DEFAULT_FAMILIES = [row["family_id"] for row in FAMILIES if row["tier"] < 3]
SECTIONS = {"读写": "reading_writing", "数学": "math"}
SECTION_NAMES = {"reading_writing": "Reading and Writing", "math": "Math"}
ID_PREFIX = {"reading_writing": "bank_rw_", "math": "bank_math_"}
DOMAIN_NAMES_ZH = {
    "Information and Ideas": "信息与观点", "Craft and Structure": "技巧与构造",
    "Expression of Ideas": "观点表达", "Standard English Conventions": "标准英语用法",
    "Algebra": "代数", "Advanced Math": "高等数学",
    "Problem-Solving and Data Analysis": "问题解决与数据分析", "Geometry and Trigonometry": "几何与三角",
    "Unclassified (Reading and Writing)": "未分类（读写）", "Unclassified (Math)": "未分类（数学）",
}
SKILL_ALIASES = {
    "quantitative evidence": "Command of Evidence: Quantitative",
    "textual evidence (illustration/quotation)": "Command of Evidence: Textual",
    "textual evidence (strengthen/weaken)": "Command of Evidence: Textual",
    "cross-text connection": "Cross-Text Connections",
    # Khan lists this beside Text Structure and Purpose; the College Board skill covers both.
    "part-to-whole relationships": "Text Structure and Purpose",
}
SKILL_DOMAINS = ("Algebra", "Advanced Math", "Problem-Solving and Data Analysis", "Geometry and Trigonometry",
                 "Craft and Structure", "Expression of Ideas", "Information and Ideas",
                 "Standard English Conventions")
DATA_START = re.compile(r"const DATA\s*=\s*")
TAG = re.compile(r"<[^>]*>")


def family_of(title: str) -> str | None:
    if re.match(r"20\d\d年", title):
        return "exam"
    if title.startswith("BARRON"):
        return "barrons"
    if title.startswith("Digital SAT Prep Test"):
        return "prep_test"
    if title.startswith("SAT机考套卷"):
        return "practice_set"
    if title.startswith("PSAT"):
        return "psat"
    if title.startswith("SAT-Practice"):
        return "sat_practice_test"
    if title.startswith("可汗"):
        return "khan"
    if title.split("/")[0] in SKILL_DOMAINS:
        return "skill_bank"
    return None  # essays and anything unrecognised are left out


def title_labels(title: str, family: str) -> tuple[str | None, str | None, str | None]:
    """(domain, skill, difficulty) stated by a skill file's title."""
    parts = title.split("/")
    if family == "skill_bank":
        return parts[0], "/".join(parts[1:-1]), parts[-1].lower()
    if family == "khan":
        return (parts[1] if len(parts) == 3 else None), parts[-1], None
    return None, None, None


def date_and_region(text: str) -> tuple[int | None, int | None, str | None]:
    found = re.search(r"(20\d\d)\s*[-年 ]\s*(\d{1,2})(?!\d)", text)
    year, month = (int(found.group(1)), int(found.group(2))) if found else (None, None)
    if month is not None and not 1 <= month <= 12:
        year = month = None
    lowered = text.lower()
    region = ("apac" if re.search(r"亚太|apac", lowered)
              else "north_america" if re.search(r"北美|美西|(?<![a-z])(na|us)(?![a-z])", lowered)
              else None)
    return year, month, region


def visible(text: str | None) -> str:
    return html.unescape(TAG.sub(" ", text or ""))


TYPOGRAPHY = str.maketrans({
    "“": '"', "”": '"', "„": '"', "″": '"', "‘": "'", "’": "'", "‚": "'", "′": "'",
    "–": "-", "—": "-", "−": "-", "‐": "-", "‑": "-", "…": "...", "\u00a0": " ",
})


def exact_key(text: str | None) -> str:
    """Same wording, ignoring tags, entities, case, spacing and quote or dash style.

    Punctuation and operators still count, so "x + 2" and "x - 2", or two
    choices that differ by a comma, stay different.
    """
    return re.sub(r"\s+", "", visible(text).translate(TYPOGRAPHY)).lower()


def loose_key(text: str | None) -> str:
    """Letters and digits only, for prose that was re-typeset when it was published."""
    return re.sub(r"[\W_]+", "", re.sub(r"\[/?\w+\]", "", visible(text))).lower()


def comparable_answer(answer: str | None, options: list[str | None]) -> str:
    """How to compare a correct answer with a published one.

    Published text was re-typeset, so letters and digits are compared, unless
    the choices differ only in punctuation, where the punctuation is the answer.
    """
    distinct = len({loose_key(option) for option in options}) == len(options)
    return loose_key(answer) if distinct else exact_key(answer)


def images(text: str | None) -> list[str]:
    return re.findall(r"<img[^>]+src=[\"']([^\"']+)", text or "")


def read_pages(raw: Path, families: set[str]) -> tuple[list[dict], list[str]]:
    decoder = json.JSONDecoder()
    pages, skipped = [], []
    for path in sorted(raw.iterdir(), key=lambda item: unicodedata.normalize("NFC", item.name)):
        if path.name.startswith(".") or path.suffix.lower() != ".html":
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        start = DATA_START.search(text)
        if not start:
            skipped.append(unicodedata.normalize("NFC", path.name))
            continue
        data, _ = decoder.raw_decode(text, start.end())
        title = unicodedata.normalize("NFC", data["title"])
        family = family_of(title)
        if family is None or family not in families:
            skipped.append(title)
            continue
        pages.append({"title": title, "family": family, "modules": data["modules"]})
    pages.sort(key=lambda page: (FAMILY_ORDER.index(page["family"]), page["title"]))
    return pages, skipped


def load_tagger(root: Path):
    path = root / "skills/skill-sat-rw-classification/scripts/tag_question_types.py"
    spec = importlib.util.spec_from_file_location("import_bank_tagger", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.rule_based_classify


def run(root: Path, raw: Path, families: set[str]) -> dict:
    tables, problems = db.read_tables(root)
    if problems:
        raise db.ContentError("content/db is not clean; run check first:\n" + "\n".join(problems[:10]))
    connection = db.open_schema(root)
    layout = db.schema_layout(connection)
    connection.close()
    rows = {name: {tuple(row[column] for column in shape["key"]): row for row in tables[name]}
            for name, shape in layout.items()}
    pages, skipped = read_pages(raw, families)
    classify = load_tagger(db.ROOT)
    report = collections.Counter()
    conflicts: list[dict] = []

    # ---- reference rows
    for row in TIERS:
        rows["source_tier"][(row["tier"],)] = row
    for row in FAMILIES:
        rows["source_family"][(row["family_id"],)] = row

    skill_by_name = {row["name"].lower(): row for row in rows["skill"].values()}
    domain_by_name = {row["name"].lower(): row for row in rows["domain"].values()}

    def ensure_skill(section: str, domain: str | None, skill: str | None) -> str:
        """Subtype id of the skill's 'no subtype recorded' row, creating taxonomy rows as needed."""
        if skill is None:
            domain = skill = f"Unclassified ({SECTION_NAMES[section]})"
        skill = SKILL_ALIASES.get(skill.lower(), skill)
        found = skill_by_name.get(skill.lower())
        if found is None:
            if domain is None:
                raise db.ContentError(f"skill {skill!r} is not known and its page names no domain")
            domain_row = domain_by_name.get(domain.lower())
            if domain_row is None:
                domain_row = {"domain_id": db.slug(domain), "section": section, "name": domain, "name_zh": None}
                rows["domain"][(domain_row["domain_id"],)] = domain_by_name[domain.lower()] = domain_row
            found = {"skill_id": db.slug(skill), "domain_id": domain_row["domain_id"], "name": skill}
            rows["skill"][(found["skill_id"],)] = skill_by_name[skill.lower()] = found
        rows["subtype"].setdefault((found["skill_id"],), {
            "subtype_id": found["skill_id"], "skill_id": found["skill_id"], "name": None})
        return found["skill_id"]

    # ---- sort the sources already here: set files are exam papers, collections are skill files
    source_by_label = {row["label"]: row for row in rows["source"].values()}
    used_source_ids = {row["source_id"] for row in rows["source"].values()}

    def ensure_source(label: str, family: str) -> dict:
        row = source_by_label.get(label)
        if row is None:
            row = {"source_id": db.next_id("src", used_source_ids), "label": label,
                   "family_id": None, "year": None, "month": None, "region": None}
            rows["source"][(row["source_id"],)] = source_by_label[label] = row
        if row["family_id"] is None:
            year, month, region = date_and_region(label)
            row.update(family_id=family, year=year, month=month, region=region)
        return row

    placed: dict[str, set] = collections.defaultdict(set)
    for item in rows["material_item"].values():
        placed[item["content_id"]].add(item["source_id"])
    for material in rows["material"].values():
        family = "skill_bank" if "/collections/" in material["data_path"] else "exam"
        for source_id in placed[material["content_id"]] - {None}:
            ensure_source(rows["source"][(source_id,)]["label"], family)
        if material["source_id"] is None:
            named = placed[material["content_id"]] - {None}
            if len(named) == 1 and None not in placed[material["content_id"]]:
                material["source_id"] = next(iter(named))
            else:
                source = ensure_source(material["title"], family)
                if source["year"] is None:
                    year, month, region = date_and_region(material["data_path"].rsplit("/", 1)[1])
                    source.update(year=year, month=month, region=source["region"] or region)
                material["source_id"] = source["source_id"]

    # ---- what is already here
    published_choices: dict[str, list[dict]] = collections.defaultdict(list)
    for choice in rows["choice"].values():
        published_choices[choice["question_id"]].append(choice)
    section_of = {}
    for subtype in rows["subtype"].values():
        skill = rows["skill"][(subtype["skill_id"],)]
        section_of[subtype["subtype_id"]] = rows["domain"][(skill["domain_id"],)]["section"]
    published_by_content: dict[str, list[tuple[str, str]]] = collections.defaultdict(list)
    for question in rows["question"].values():
        if question["status"] != "published":
            continue
        options = sorted(published_choices[question["question_id"]], key=lambda row: row["label"])
        answer = next(row["text"] for row in options if row["label"] == question["correct_answer"])
        key = loose_key(question["passage"])[:400] + "|" + "".join(sorted(loose_key(row["text"]) for row in options))
        published_by_content[key].append((question["question_id"], comparable_answer(answer, [row["text"] for row in options])))
    known_item = {row["raw_item_id"]: row["question_id"] for row in rows["question_source"].values()}

    # ---- read every record and group identical ones
    records = []
    for page in pages:
        domain, skill, difficulty = title_labels(page["title"], page["family"])
        source = ensure_source(page["title"], page["family"])
        for module_index, module in enumerate(page["modules"], 1):
            section = SECTIONS.get(module["sec"])
            if section is None:
                report["records left out: not Reading and Writing or Math"] += len(module["questions"])
                continue
            rows["source_module"][(source["source_id"], module_index)] = {
                "source_id": source["source_id"], "module_index": module_index,
                "name": module["name"], "minutes": module.get("time")}
            for number, item in enumerate(module["questions"], 1):
                options = item["options"] or []
                numeric = bool(item["isSpr"])
                if numeric:
                    answers = [part.strip() for part in str(item["answer"]).split(" 或 ") if part.strip()]
                    answer_key = "|".join(sorted(exact_key(value) for value in answers))
                else:
                    answer_key = exact_key(options[item["answer"]].get("text")) + "".join(images(options[item["answer"]].get("text"))) + (options[item["answer"]].get("pic") or "")
                content = "\x1f".join([
                    section, exact_key(item["material"]), exact_key(item["stem"]),
                    *sorted(exact_key(option.get("text")) + (option.get("pic") or "") for option in options),
                    *images(item["material"]), *images(item["stem"]),
                    *[address for option in options for address in images(option.get("text"))]])
                records.append({
                    "page": page, "source": source, "module_index": module_index, "number": number,
                    "item": item, "section": section, "numeric": numeric, "options": options,
                    "answers": answers if numeric else None, "answer_key": answer_key,
                    "group": hashlib.sha256((content + "\x1e" + answer_key).encode("utf-8")).hexdigest(),
                    "content": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                    "labels": (domain, skill, difficulty),
                })
    report["records read"] = len(records)

    groups: dict[str, list[dict]] = collections.defaultdict(list)
    for record in records:
        groups[record["group"]].append(record)
    answers_per_content: dict[str, set] = collections.defaultdict(set)
    for record in records:
        answers_per_content[record["content"]].add(record["group"])

    # ---- give each group a question
    counters = collections.Counter()
    for question_id in rows["question"]:
        for section, prefix in ID_PREFIX.items():
            if question_id[0].startswith(prefix):
                counters[section] = max(counters[section], int(question_id[0][len(prefix):]))
    gold: list[tuple[str, str]] = []

    for members in groups.values():
        first = members[0]
        item, section = first["item"], first["section"]
        labelled = next((record["labels"] for record in members if record["labels"][1]), (None, None, None))
        difficulty = next((record["labels"][2] for record in members if record["labels"][2]), None)
        disputed = len(answers_per_content[first["content"]]) > 1

        question_id = next((known_item[record["item"]["id"]] for record in members
                            if record["item"]["id"] in known_item), None)
        if section == "reading_writing" and not first["numeric"]:
            key = (loose_key(item["material"])[:400] + "|"
                   + "".join(sorted(loose_key(option.get("text")) for option in first["options"])))
            matches = published_by_content.get(key, []) if loose_key(item["material"]) else []
            answer = comparable_answer(first["options"][item["answer"]].get("text"),
                                       [option.get("text") for option in first["options"]])
            same = sorted(qid for qid, text in matches if text == answer)
            if same:
                question_id = question_id or same[0]
            elif matches:
                disputed = True
                conflicts.append({"published": sorted(qid for qid, _ in matches),
                                  "source": first["source"]["label"], "module": first["module_index"],
                                  "number": first["number"], "reason": "same text, different answer"})

        existing = rows["question"].get((question_id,)) if question_id else None
        if existing is not None and existing["status"] == "published":
            report["groups matched to a published question"] += 1
            if difficulty and existing["difficulty_basis"] != "source_file":
                existing.update(difficulty=difficulty, difficulty_basis="source_file")
        else:
            if question_id is None:
                counters[section] += 1
                question_id = f"{ID_PREFIX[section]}{counters[section]:06d}"
            basis = "source_file" if labelled[1] else "none"
            subtype_id = ensure_skill(section, labelled[0], labelled[1])
            plain_question = {"prompt": visible(item["stem"]), "passage": visible(item["material"]),
                              "choices": [{"label": "ABCD"[i], "text": visible(option.get("text"))}
                                          for i, option in enumerate(first["options"])]}
            if section == "reading_writing" and not first["numeric"]:
                guess = classify(plain_question) or {}
                confident = guess.get("confidence") == "high" and guess.get("skill", "").lower() in skill_by_name
                if labelled[1]:
                    gold.append((rows["skill"][(subtype_id,)]["name"],
                                 guess.get("skill") if confident else None))
                elif confident:
                    subtype_id, basis = ensure_skill(section, None, guess["skill"]), "rule"
            marked_up = any("<" in (text or "") or "&" in (text or "")
                            for text in [item["material"], item["stem"],
                                         *[option.get("text") for option in first["options"]]])
            rows["question"][(question_id,)] = {
                "question_id": question_id, "passage": item["material"] or "", "prompt": item["stem"] or "",
                "correct_answer": None if first["numeric"] else "ABCD"[item["answer"]],
                "subtype_id": subtype_id, "needs_manual_review": disputed,
                "difficulty_estimate": None, "skills_tested": None, "status": "draft",
                "format": "numeric" if first["numeric"] else "choice",
                "markup": "source_html" if marked_up or any(option.get("pic") for option in first["options"]) else "canonical",
                "difficulty": difficulty, "skill_basis": basis,
                "difficulty_basis": "source_file" if difficulty else None,
            }
            for name in ("choice", "numeric_answer"):
                for key in [key for key in rows[name] if key[0] == question_id]:
                    del rows[name][key]
            for position, option in enumerate(first["options"]):
                rows["choice"][(question_id, "ABCD"[position])] = {
                    "question_id": question_id, "label": "ABCD"[position], "text": option.get("text") or "",
                    "analysis": None, "image": option.get("pic") or None}
            for position, value in enumerate(dict.fromkeys(first["answers"] or []), 1):
                rows["numeric_answer"][(question_id, position)] = {
                    "question_id": question_id, "position": position, "value": value}
            explained = next((record["item"]["parse"] for record in members if record["item"]["parse"]), None)
            rows["explanation"].pop((question_id,), None)
            if explained:
                rows["explanation"][(question_id,)] = {
                    "question_id": question_id, "short": None, "key_evidence": None,
                    "why_correct": None, "detailed": explained}
        for record in members:
            rows["question_source"][(record["source"]["source_id"], record["module_index"], record["number"])] = {
                "source_id": record["source"]["source_id"], "module_index": record["module_index"],
                "number": record["number"], "question_id": question_id, "raw_item_id": record["item"]["id"]}

    for row in rows["domain"].values():
        row["name_zh"] = row.get("name_zh") or DOMAIN_NAMES_ZH.get(row["name"])

    merged = {name: list(table.values()) for name, table in rows.items()}
    merged = {name: [json.loads(line) for line in text.splitlines()]
              for name, text in db.render_tables(merged, root).items()}
    result = db.normalize(root, merged)
    db.load_sqlite(result, root).close()
    problems = db.compare_with_canonical(result, root)
    if problems:
        raise db.ContentError("the import would change published set files:\n" + "\n".join(problems[:10]))
    changed = db.write_tables(result, root)

    drafts = [row for row in result["question"] if row["status"] == "draft"]
    report["pages read"] = len(pages)
    report["distinct questions in the pages"] = len(groups)
    report["draft questions in content/db"] = len(drafts)
    report["drafts flagged: same text with different answers"] = sum(row["needs_manual_review"] for row in drafts)
    checked = [pair for pair in gold if pair[1]]
    return {
        "counts": dict(report), "changed": changed, "skipped_pages": skipped, "conflicts": conflicts,
        "rule_tagger_on_labelled": {
            "labelled": len(gold), "confident": len(checked),
            "confident_and_right": sum(left == right for left, right in checked)},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--raw", type=Path, required=True, help="folder of exported practice pages")
    parser.add_argument("--families", default=",".join(DEFAULT_FAMILIES),
                        help="comma-separated source families to import (default: "
                             + ", ".join(DEFAULT_FAMILIES) + "; third-party prep books only when named)")
    parser.add_argument("--report", type=Path, help="write the full report as JSON")
    parser.add_argument("--root", type=Path, default=db.ROOT, help=argparse.SUPPRESS)
    arguments = parser.parse_args(argv)
    families = {name.strip() for name in arguments.families.split(",") if name.strip()}
    unknown = families - set(FAMILY_ORDER)
    if unknown:
        parser.error("unknown families: " + ", ".join(sorted(unknown)))
    try:
        result = run(arguments.root.resolve(), arguments.raw, families)
    except db.ContentError as error:
        print(str(error), file=sys.stderr)
        return 1
    for name, value in result["counts"].items():
        print(f"{value:7d}  {name}")
    tagger = result["rule_tagger_on_labelled"]
    print(f"Rule tagger on {tagger['labelled']} questions whose skill file states the skill: "
          f"confident on {tagger['confident']}, right on {tagger['confident_and_right']} of those.")
    print(f"{len(result['conflicts'])} source question(s) share text with a published question but give "
          "another answer; they were added as flagged drafts.")
    print("Changed: " + (", ".join(result["changed"]) or "nothing"))
    if arguments.report:
        arguments.report.parent.mkdir(parents=True, exist_ok=True)
        arguments.report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
