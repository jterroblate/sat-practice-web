"""Importing practice pages adds drafts and sources without touching published content."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


def scratch() -> Path:
    """Temporary copies go under the ignored build tree, like the other pipeline tests."""
    parent = ROOT / "build/test-tmp"
    parent.mkdir(parents=True, exist_ok=True)
    return parent
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location("import_bank_under_test", ROOT / "scripts/import_question_bank.py")
assert SPEC and SPEC.loader
IMPORTER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(IMPORTER)
DB = IMPORTER.db
PUBLISHED_ID = "wic-easy-153437"


def page(title: str, modules: list[dict]) -> str:
    data = json.dumps({"title": title, "modules": modules}, ensure_ascii=False)
    return f"<html><body><script>\nconst DATA = {data};\n</script></body></html>"


def choice_item(item_id: int, material: str, stem: str, options: list[str], answer: int, parse: str = "") -> dict:
    return {"id": item_id, "stem": stem, "guide": "", "material": material, "materialTrs": "",
            "options": [{"text": text, "pic": None} for text in options], "isSpr": False,
            "answer": answer, "parse": parse, "displaytitle": "", "ztsort": 1}


def numeric_item(item_id: int, stem: str, answer: str) -> dict:
    return {"id": item_id, "stem": stem, "guide": "", "material": "", "materialTrs": "", "options": [],
            "isSpr": True, "answer": answer, "parse": "", "displaytitle": "", "ztsort": 1}


class ImportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(dir=scratch())
        cls.root = Path(cls.temporary.name) / "repo"
        shutil.copytree(ROOT / "content", cls.root / "content")
        cls.raw = Path(cls.temporary.name) / "pages"
        cls.raw.mkdir()

        tables, _ = DB.read_tables(cls.root)
        cls.before = tables
        published = next(row for row in tables["question"] if row["question_id"] == PUBLISHED_ID)
        options = sorted((row for row in tables["choice"] if row["question_id"] == PUBLISHED_ID),
                         key=lambda row: row["label"])
        texts = [row["text"] for row in options]
        right = "ABCD".index(published["correct_answer"])
        passage = published["passage"].replace("[vocab]", "").replace("[/vocab]", "")
        tide = ["rises twice", "never moves", "freezes", "turns red"]

        (cls.raw / "exam.html").write_text(page("2031年4月机考真题第1套(亚太A卷)", [
            {"name": "读写 · Module 1", "sec": "读写", "time": 32, "questions": [
                choice_item(900001, passage, published["prompt"], texts, right),
                choice_item(900002, "The harbor “tide” ______ each day.", "Which choice fits?", tide, 0),
                choice_item(900003, passage, published["prompt"], texts, (right + 1) % 4),
            ]},
            {"name": "数学 · Module 1", "sec": "数学", "time": 35, "questions": [
                numeric_item(900004, "If 2x + 6 = 20, what is x?", "7 或 7.0"),
                choice_item(900005, "", "What is x - 2 when x is 5?", ["1", "2", "3", "4"], 2),
                choice_item(900006, "", "What is x + 2 when x is 5?", ["1", "2", "3", "7"], 3),
            ]},
        ]), encoding="utf-8")
        (cls.raw / "skill.html").write_text(page("Craft and Structure/Words in Context/Hard", [
            {"name": "读写 · Module 1", "sec": "读写", "time": 20, "questions": [
                choice_item(900007, 'The harbor "tide" ______ each day.', "Which choice fits?", tide, 0,
                            parse="The first choice matches the daily pattern."),
            ]},
        ]), encoding="utf-8")
        (cls.raw / "essay.html").write_text(page("SAT Essay Practice 99", [
            {"name": "写作 · Essay", "sec": "写作", "time": 50, "questions": [numeric_item(900008, "Write.", "")]},
        ]), encoding="utf-8")

        cls.result = IMPORTER.run(cls.root, cls.raw, set(IMPORTER.FAMILY_ORDER))
        cls.after, problems = DB.read_tables(cls.root)
        assert problems == []
        cls.connection = DB.load_sqlite(cls.after, cls.root)

    @classmethod
    def tearDownClass(cls):
        cls.connection.close()
        cls.temporary.cleanup()

    def placed(self, raw_item_id: int) -> str:
        return self.connection.execute(
            "SELECT question_id FROM question_source WHERE raw_item_id = ?", (raw_item_id,)).fetchone()[0]

    def question(self, question_id: str) -> dict:
        return next(row for row in self.after["question"] if row["question_id"] == question_id)

    def test_tables_stay_consistent_and_set_files_untouched(self):
        self.assertEqual(DB.check(self.root), [])
        for path in DB.canonical_files(ROOT):
            relative = path.relative_to(ROOT)
            self.assertEqual((self.root / relative).read_bytes(), path.read_bytes(), str(relative))

    def test_published_question_is_recognised_not_copied(self):
        self.assertEqual(self.placed(900001), PUBLISHED_ID)
        before = next(row for row in self.before["question"] if row["question_id"] == PUBLISHED_ID)
        self.assertEqual(self.question(PUBLISHED_ID), before)

    def test_same_text_with_another_answer_becomes_a_flagged_draft(self):
        disputed = self.question(self.placed(900003))
        self.assertEqual(disputed["status"], "draft")
        self.assertTrue(disputed["needs_manual_review"])
        self.assertIn(PUBLISHED_ID, [qid for entry in self.result["conflicts"] for qid in entry["published"]])

    def test_repeats_merge_across_pages_and_take_labels_from_the_skill_file(self):
        question_id = self.placed(900002)
        self.assertEqual(self.placed(900007), question_id, "quote style alone must not split a question")
        row = self.question(question_id)
        self.assertEqual((row["status"], row["difficulty"], row["skill_basis"]), ("draft", "hard", "source_file"))
        self.assertEqual(row["subtype_id"], "words_in_context")
        explained = [entry["detailed"] for entry in self.after["explanation"] if entry["question_id"] == question_id]
        self.assertEqual(explained, ["The first choice matches the daily pattern."])
        origin = self.connection.execute(
            "SELECT kind, latest_year FROM question_origin WHERE question_id = ?", (question_id,)).fetchone()
        self.assertEqual(origin, ("real_exam", 2031))

    def test_math_questions_keep_operators_apart_and_numeric_answers_split(self):
        self.assertNotEqual(self.placed(900005), self.placed(900006))
        numeric = self.question(self.placed(900004))
        self.assertEqual((numeric["format"], numeric["correct_answer"]), ("numeric", None))
        self.assertTrue(numeric["question_id"].startswith("bank_math_"))
        values = [row["value"] for row in self.after["numeric_answer"] if row["question_id"] == numeric["question_id"]]
        self.assertEqual(values, ["7", "7.0"])

    def test_source_records_family_date_region_and_essays_are_left_out(self):
        source = next(row for row in self.after["source"] if row["label"].startswith("2031年4月"))
        self.assertEqual((source["family_id"], source["year"], source["month"], source["region"]),
                         ("exam", 2031, 4, "apac"))
        self.assertIn("SAT Essay Practice 99", self.result["skipped_pages"])
        self.assertIsNone(self.connection.execute(
            "SELECT 1 FROM question_source WHERE raw_item_id = 900008").fetchone())

    def test_third_party_prep_books_are_read_only_when_named(self):
        self.assertEqual(set(IMPORTER.DEFAULT_FAMILIES),
                         {row["family_id"] for row in IMPORTER.FAMILIES if row["tier"] < 3})
        raw = Path(self.temporary.name) / "book-pages"
        raw.mkdir()
        title = "BARRON'S Digital SAT/PRACTICE TESTS 9"
        (raw / "book.html").write_text(page(title, [
            {"name": "读写 · Module 1", "sec": "读写", "time": 32, "questions": [
                choice_item(900020, "An invented sentence about ______ kites.", "Which choice fits?",
                            ["red", "blue", "green", "grey"], 1)]}]), encoding="utf-8")
        pages, skipped = IMPORTER.read_pages(raw, set(IMPORTER.DEFAULT_FAMILIES))
        self.assertEqual((pages, skipped), ([], [title]))
        pages, skipped = IMPORTER.read_pages(raw, set(IMPORTER.FAMILY_ORDER))
        self.assertEqual(([entry["title"] for entry in pages], skipped), ([title], []))

    def test_second_run_changes_nothing_and_keeps_earlier_drafts(self):
        drafts = sum(row["status"] == "draft" for row in self.after["question"])
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = IMPORTER.main(["--raw", str(self.raw), "--root", str(self.root)])
        self.assertEqual(code, 0)
        self.assertIn("Changed: nothing", output.getvalue())
        again, _ = DB.read_tables(self.root)
        self.assertEqual(sum(row["status"] == "draft" for row in again["question"]), drafts)
        self.assertEqual(drafts, sum(row["status"] == "draft" for row in self.before["question"]) + 5)


if __name__ == "__main__":
    unittest.main()
