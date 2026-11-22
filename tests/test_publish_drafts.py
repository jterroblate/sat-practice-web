"""Publishing drafts adds new materials and leaves existing ones alone."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


def scratch() -> Path:
    """Temporary copies go under the ignored build tree, like the other pipeline tests."""
    parent = ROOT / "build/test-tmp"
    parent.mkdir(parents=True, exist_ok=True)
    return parent


def load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


IMPORTER = load("publish_test_importer", "scripts/import_question_bank.py")
PUBLISHER = load("publish_test_publisher", "scripts/publish_drafts.py")
DB = PUBLISHER.db
STEM = "Which choice completes the text with the most logical and precise word or phrase?"
EXAM = "2032年7月机考真题第9套(亚太Z卷)"


def page(title: str, items: list[dict]) -> str:
    data = {"title": title, "modules": [{"name": "读写 · Module 1", "sec": "读写", "time": 32, "questions": items}]}
    return "<html><body><script>\nconst DATA = " + json.dumps(data, ensure_ascii=False) + ";\n</script></body></html>"


def item(item_id: int, material: str, options: list[str] | None = None, answer: int = 0, parse: str = "") -> dict:
    options = options or [f"alpha{item_id}", f"bravo{item_id}", f"charlie{item_id}", f"delta{item_id}"]
    return {"id": item_id, "stem": STEM, "guide": "", "material": material, "materialTrs": "",
            "options": [{"text": text, "pic": None} for text in options], "isSpr": False, "answer": answer,
            "parse": parse, "displaytitle": "", "ztsort": 1}


def passage(number: int, extra: str = "") -> str:
    topics = ["harbor pilots", "desert lichens", "glass harmonicas", "tidal mills", "paper kites", "salt roads",
              "copper bells", "river ferries", "wind organs", "clay ovens", "stone bridges", "night markets",
              "reed boats", "ice cellars"]
    return (f"Invented study {number} follows {topics[number % len(topics)]} across {number + 3} seasons. "
            f"Observers in workshop {number * 7} ______ the records each week.{extra}")


class PublishTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(dir=scratch())
        cls.root = Path(cls.temporary.name) / "repo"
        shutil.copytree(ROOT / "content", cls.root / "content")
        shutil.copyfile(ROOT / "sat-content-registry.json", cls.root / "sat-content-registry.json")
        raw = Path(cls.temporary.name) / "pages"
        raw.mkdir()
        items = [item(910000 + n, passage(n), parse="Because the first choice fits.<br>It matches." if n == 1 else "")
                 for n in range(1, 12)]
        items += [
            item(910020, passage(20, " The <u>second</u> report was <em>longer</em>.")),
            item(910021, "<table><caption>Invented Counts</caption><tr><th>Site</th><th>Count</th></tr>"
                         "<tr><td>North</td><td>4</td></tr><tr><td>South</td><td>9</td></tr></table>" + passage(21)),
            item(910022, passage(22) + '<img src="https://example.com/figure.png">'),
            item(910023, passage(23) + " （编者注：此处原文缺失）"),
            # near copy of item 1: one word changed, same choices, same answer
            item(910024, passage(1).replace("each week", "every week"),
                 options=["alpha910001", "bravo910001", "charlie910001", "delta910001"]),
            # near copy of item 2 that gives another answer
            item(910025, passage(2).replace("each week", "every week"),
                 options=["alpha910002", "bravo910002", "charlie910002", "delta910002"], answer=1),
        ]
        (raw / "exam.html").write_text(page(EXAM, items), encoding="utf-8")
        (raw / "book.html").write_text(page("BARRON'S Digital SAT/PRACTICE TESTS 9", [
            item(920000 + n, passage(40 + n)) for n in range(1, 12)]), encoding="utf-8")
        IMPORTER.run(cls.root, raw, set(IMPORTER.FAMILY_ORDER))

        cls.registry_before = json.loads((cls.root / "sat-content-registry.json").read_text(encoding="utf-8"))
        cls.result = PUBLISHER.plan(cls.root, {1, 2})
        cls.ours = [material for material in cls.result["materials"]
                    if any(member["placement"]["raw_item_id"] >= 910000 for member in material["members"])]
        PUBLISHER.write(cls.root, cls.result)
        cls.tables, problems = DB.read_tables(cls.root)
        assert problems == []

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def question_for(self, raw_item_id: int) -> dict:
        question_id = next(row["question_id"] for row in self.tables["question_source"]
                           if row["raw_item_id"] == raw_item_id)
        return next(row for row in self.tables["question"] if row["question_id"] == question_id)

    def published_record(self, raw_item_id: int) -> dict:
        question_id = self.question_for(raw_item_id)["question_id"]
        document = json.loads((self.root / self.ours[0]["data_path"]).read_text(encoding="utf-8"))
        return next(record for record in document["questions"] if record["question_id"] == question_id)

    def test_tables_and_set_files_agree_and_existing_files_are_untouched(self):
        self.assertEqual(DB.check(self.root), [])
        for path in DB.canonical_files(ROOT):
            relative = path.relative_to(ROOT)
            self.assertEqual((self.root / relative).read_bytes(), path.read_bytes(), str(relative))
        before = self.registry_before["entries"]
        after = json.loads((self.root / "sat-content-registry.json").read_text(encoding="utf-8"))["entries"]
        self.assertEqual(after[:len(before)], before)

    def test_one_new_set_for_the_exam_page_with_a_registry_entry(self):
        self.assertEqual(len(self.ours), 1)
        material = self.ours[0]
        self.assertEqual((material["kind"], material["route"], material["title"]),
                         ("set", "sets/exam-2032-07-s9-apac-z.html", EXAM))
        registry = json.loads((self.root / "sat-content-registry.json").read_text(encoding="utf-8"))
        entry = next(entry for entry in registry["entries"] if entry["contentId"] == material["content_id"])
        document = json.loads((self.root / material["data_path"]).read_text(encoding="utf-8"))
        ids = [record["question_id"] for record in document["questions"]]
        self.assertEqual((entry["questionIds"], entry["baselineQuestionIds"], entry["examEligible"], entry["status"]),
                         (ids, ids, False, "published"))
        self.assertEqual([record["question_number"] for record in document["questions"]], list(range(1, len(ids) + 1)))
        row = next(row for row in self.tables["material"] if row["content_id"] == material["content_id"])
        source = next(row for row in self.tables["source"] if row["source_id"] == row["source_id"] and row["label"] == EXAM)
        self.assertEqual(row["source_id"], source["source_id"])

    def test_published_drafts_keep_their_ids_classification_basis_and_origin(self):
        row = self.question_for(910003)
        self.assertTrue(row["question_id"].startswith("bank_rw_"))
        self.assertEqual((row["status"], row["markup"], row["skill_basis"]), ("published", "canonical", "rule"))
        connection = DB.load_sqlite(self.tables, self.root)
        self.addCleanup(connection.close)
        origin = connection.execute("SELECT kind, latest_year FROM question_origin WHERE question_id = ?",
                                    (row["question_id"],)).fetchone()
        self.assertEqual(origin, ("real_exam", 2032))

    def test_markup_tables_and_explanations_are_converted(self):
        self.assertIn("The [u]second[/u] report was [i]longer[/i].", self.published_record(910020)["passage"])
        table = self.published_record(910021)
        self.assertEqual(table["table_data"], {"title": "Invented Counts", "columns": ["Site", "Count"],
                                               "rows": [["North", "4"], ["South", "9"]]})
        self.assertNotIn("<", table["passage"])
        self.assertEqual(self.published_record(910001)["explanation"]["detailed"],
                         "Because the first choice fits.\nIt matches.")
        self.assertEqual(self.published_record(910003)["explanation"]["detailed"], "")

    def test_images_foreign_notes_copies_and_disputes_stay_drafts(self):
        for raw_item_id in (910022, 910023, 910024, 910025):
            self.assertEqual(self.question_for(raw_item_id)["status"], "draft", raw_item_id)
        self.assertEqual(self.question_for(910002)["status"], "draft", "the question its near copy disputes")
        self.assertEqual(self.question_for(910001)["status"], "published", "the original of an agreeing near copy")

    def test_third_party_sources_wait_for_an_explicit_choice(self):
        self.assertEqual(self.question_for(920001)["status"], "draft")
        self.assertGreaterEqual(self.result["kept_out"]["source tier 3 is not selected"], 11)
        later = PUBLISHER.plan(self.root, {1, 2, 3})
        self.assertTrue(any(member["placement"]["raw_item_id"] == 920001
                            for material in later["materials"] for member in material["members"]))

    def test_running_again_publishes_nothing_more_from_these_pages(self):
        again = PUBLISHER.plan(self.root, {1, 2})
        self.assertFalse(any(member["placement"]["raw_item_id"] >= 910000
                             for material in again["materials"] for member in material["members"]))


if __name__ == "__main__":
    unittest.main()
