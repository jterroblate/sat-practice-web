#!/usr/bin/env python3
"""Prepare Reading and Writing questions for labelling by a model, and apply the result.

    python3 scripts/label_questions.py export --work /path/outside/the/repo
    python3 scripts/label_questions.py merge  --work /path/outside/the/repo [--apply]

export writes, under the work folder:
  todo/batch-NNN.jsonl   one question per line: id, passage, prompt, choices
  examples.jsonl         a few questions whose skill and difficulty a source states
  key.json               which ids are check questions (not for the labeller)
The labeller writes done/batch-NNN.jsonl with one line per question:
  {"id": ..., "skill": ..., "difficulty": "easy|medium|hard", "confidence": "high|medium|low"}

Questions without a difficulty are the work. Mixed in are check questions whose
difficulty and skill a source file states; merge compares the labeller's answers
with those and prints the agreement, so the labels can be judged before use.

merge --apply then records, in content/db:
  - difficulty for the work questions, with difficulty_basis 'model';
  - a skill for questions that had none, with skill_basis 'model'.
Skills that were curated, stated by a source file or set by the rule tagger are
kept; where the labeller disagrees they are only listed in the report.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import html
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import content_db as db  # noqa: E402


BATCH_SIZE = 40
CHECK_QUESTIONS = 300
EXAMPLES_PER_LEVEL = 3
LEVELS = ("easy", "medium", "hard")
CONFIDENCE = ("high", "medium", "low")
SECTION = "reading_writing"


def plain(text: str | None) -> str:
    text = re.sub(r"</(?:p|div|tr|caption|li|h\d)>|<br\s*/?>", "\n", text or "", flags=re.IGNORECASE)
    text = re.sub(r"</t[dh]>", " | ", text, flags=re.IGNORECASE)
    text = html.unescape(re.sub(r"<[^>]*>", "", text))
    text = re.sub(r"\[/?(?:vocab|i|u|b)\]", "", text)
    return re.sub(r"[ \t ]+", " ", re.sub(r"\s*\n\s*", "\n", text)).strip()


def stable_order(question_id: str) -> str:
    return hashlib.sha256(question_id.encode("utf-8")).hexdigest()


def load(root: Path):
    tables, problems = db.read_tables(root)
    if problems:
        raise db.ContentError("content/db is not clean; run check first:\n" + "\n".join(problems[:10]))
    subtypes = {row["subtype_id"]: row for row in tables["subtype"]}
    skills = {row["skill_id"]: row for row in tables["skill"]}
    domains = {row["domain_id"]: row for row in tables["domain"]}
    choices: dict[str, list[dict]] = collections.defaultdict(list)
    for row in tables["choice"]:
        choices[row["question_id"]].append(row)
    questions = []
    for row in tables["question"]:
        skill = skills[subtypes[row["subtype_id"]]["skill_id"]]
        if domains[skill["domain_id"]]["section"] == SECTION and row["format"] == "choice":
            questions.append({**row, "skill": skill["name"],
                              "choices": sorted(choices[row["question_id"]], key=lambda item: item["label"])})
    names = sorted(skill["name"] for skill in skills.values()
                   if domains[skill["domain_id"]]["section"] == SECTION and not skill["name"].startswith("Unclassified"))
    return tables, questions, names


def as_task(question: dict) -> dict:
    return {"id": question["question_id"], "passage": plain(question["passage"]),
            "prompt": plain(question["prompt"]),
            "choices": {row["label"]: plain(row["text"]) for row in question["choices"]}}


def export(root: Path, work: Path) -> dict:
    _, questions, _ = load(root)
    stated = sorted((q for q in questions if q["difficulty_basis"] == "source_file"
                     and q["skill_basis"] in ("source_file", "curated")),
                    key=lambda q: stable_order(q["question_id"]))
    examples, used = [], collections.Counter()
    for question in stated:
        if used[question["difficulty"]] < EXAMPLES_PER_LEVEL:
            used[question["difficulty"]] += 1
            examples.append(question)
    example_ids = {q["question_id"] for q in examples}
    checks = [q for q in stated if q["question_id"] not in example_ids][:CHECK_QUESTIONS]
    todo = [q for q in questions if q["difficulty"] is None]
    tasks = sorted([*todo, *checks], key=lambda q: stable_order("order:" + q["question_id"]))

    (work / "todo").mkdir(parents=True, exist_ok=True)
    (work / "done").mkdir(parents=True, exist_ok=True)
    for stale in (work / "todo").glob("batch-*.jsonl"):
        stale.unlink()
    batches = [tasks[start:start + BATCH_SIZE] for start in range(0, len(tasks), BATCH_SIZE)]
    for number, batch in enumerate(batches, 1):
        (work / "todo" / f"batch-{number:03d}.jsonl").write_text(
            "".join(json.dumps(as_task(q), ensure_ascii=False) + "\n" for q in batch), encoding="utf-8")
    (work / "examples.jsonl").write_text("".join(
        json.dumps({**as_task(q), "skill": q["skill"], "difficulty": q["difficulty"]}, ensure_ascii=False) + "\n"
        for q in sorted(examples, key=lambda q: LEVELS.index(q["difficulty"]))), encoding="utf-8")
    (work / "key.json").write_text(json.dumps(
        {"checks": sorted(q["question_id"] for q in checks)}, indent=2) + "\n", encoding="utf-8")
    return {"batches": len(batches), "work": len(todo), "checks": len(checks), "examples": len(examples)}


def merge(root: Path, work: Path, apply: bool) -> dict:
    tables, questions, skill_names = load(root)
    by_id = {q["question_id"]: q for q in questions}
    checks = set(json.loads((work / "key.json").read_text(encoding="utf-8"))["checks"])
    labels: dict[str, dict] = {}
    problems, missing = [], []
    for todo in sorted((work / "todo").glob("batch-*.jsonl")):
        expected = [json.loads(line)["id"] for line in todo.read_text(encoding="utf-8").splitlines()]
        done = work / "done" / todo.name
        if not done.is_file():
            missing.append(todo.name)
            continue
        rows = []
        for number, line in enumerate(done.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                problems.append(f"{todo.name}:{number}: not valid JSON")
        bad = [row for row in rows if not isinstance(row, dict) or row.get("skill") not in skill_names
               or row.get("difficulty") not in LEVELS or row.get("confidence") not in CONFIDENCE]
        if bad or [row.get("id") for row in rows if isinstance(row, dict)] != expected:
            problems.append(f"{todo.name}: {len(bad)} line(s) with values outside the lists, "
                            f"{len(rows)} of {len(expected)} lines, ids "
                            f"{'match' if [r.get('id') for r in rows if isinstance(r, dict)] == expected else 'do not match'}")
            continue
        labels.update((row["id"], row) for row in rows)

    def agreement(pairs: list[tuple[str, str]]) -> dict:
        right = sum(left == right for left, right in pairs)
        return {"compared": len(pairs), "same": right,
                "rate": round(right / len(pairs), 3) if pairs else None}

    checked = [by_id[qid] for qid in checks if qid in labels]
    difficulty_pairs = [(q["difficulty"], labels[q["question_id"]]["difficulty"]) for q in checked]
    confusion = collections.Counter(difficulty_pairs)
    near = sum(abs(LEVELS.index(a) - LEVELS.index(b)) <= 1 for a, b in difficulty_pairs)
    rule = [by_id[qid] for qid in labels if by_id[qid]["skill_basis"] == "rule"]
    curated = [by_id[qid] for qid in labels if by_id[qid]["skill_basis"] == "curated"]
    report = {
        "batches": {"labelled": 0, "missing": missing, "rejected": problems},
        "questions_labelled": len(labels),
        "difficulty_on_checks": {**agreement(difficulty_pairs),
                                 "within_one_level": round(near / len(difficulty_pairs), 3) if difficulty_pairs else None,
                                 "stated_vs_labelled": {f"{a}->{b}": n for (a, b), n in sorted(confusion.items())}},
        "skill_on_checks": agreement([(q["skill"], labels[q["question_id"]]["skill"]) for q in checked]),
        "skill_vs_rule_tagger": agreement([(q["skill"], labels[q["question_id"]]["skill"]) for q in rule]),
        "skill_vs_curated": agreement([(q["skill"], labels[q["question_id"]]["skill"]) for q in curated]),
    }
    report["batches"]["labelled"] = len(list((work / "todo").glob("batch-*.jsonl"))) - len(missing) - len(problems)
    report["skill_disagreements"] = [
        {"id": q["question_id"], "basis": q["skill_basis"], "recorded": q["skill"],
         "labelled": labels[q["question_id"]]["skill"], "confidence": labels[q["question_id"]]["confidence"]}
        for q in sorted([*rule, *curated], key=lambda q: q["question_id"])
        if q["skill"] != labels[q["question_id"]]["skill"]]

    applied = collections.Counter()
    if apply:
        skill_ids = {row["name"]: row["skill_id"] for row in tables["skill"]}
        subtype_ids = {row["subtype_id"] for row in tables["subtype"]}
        for row in tables["question"]:
            label = labels.get(row["question_id"])
            if label is None or row["question_id"] in checks:
                continue
            if row["difficulty"] is None:
                row["difficulty"], row["difficulty_basis"] = label["difficulty"], "model"
                applied["difficulty set from the model"] += 1
            if row["skill_basis"] == "none":
                skill_id = skill_ids[label["skill"]]
                if skill_id not in subtype_ids:
                    tables["subtype"].append({"subtype_id": skill_id, "skill_id": skill_id, "name": None})
                    subtype_ids.add(skill_id)
                row["subtype_id"], row["skill_basis"] = skill_id, "model"
                applied["skill set from the model"] += 1
                applied[f"  of those, model confidence {label['confidence']}"] += 1
        result = db.normalize(root, tables)
        db.load_sqlite(result, root).close()
        trouble = db.compare_with_canonical(result, root)
        if trouble:
            raise db.ContentError("applying labels would change published set files:\n" + "\n".join(trouble[:10]))
        applied["table files changed"] = len(db.write_tables(result, root))
    report["applied"] = dict(applied)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("command", choices=["export", "merge"])
    parser.add_argument("--work", type=Path, required=True, help="folder for batches, outside the repository")
    parser.add_argument("--apply", action="store_true", help="merge: write the labels into content/db")
    parser.add_argument("--root", type=Path, default=db.ROOT, help=argparse.SUPPRESS)
    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "export":
            result = export(arguments.root.resolve(), arguments.work)
        else:
            result = merge(arguments.root.resolve(), arguments.work, arguments.apply)
            (arguments.work / "report.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            result = {key: value for key, value in result.items() if key != "skill_disagreements"} | {
                "skill_disagreements": len(result["skill_disagreements"])}
    except db.ContentError as error:
        print(str(error), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
