"""The site shows each question's origin and difficulty from content/db."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


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


DB = load("metadata_content_db", "scripts/content_db.py")
BUILDER = load("metadata_builder", "skills/skill-sat-rw-classification/scripts/build_learning_experience.py")
PIPELINE = load("metadata_pipeline_helpers", "tests/test_full_content_pipeline.py")
ORIGIN_KEYS = {"sourceKind", "sourceTier", "sourceYear", "sourceRefs", "difficultyLevel", "difficultyBasis"}
REF_KEYS = {"label", "kind", "tier", "year", "month", "region", "module", "number"}


def question(**extra):
    return {
        "question_id": "demo_origin", "section": "reading_writing", "question_number": 1,
        "passage": "An original development statement.", "prompt": "Which choice fits?",
        "choices": [{"label": label, "text": text}
                    for label, text in zip("ABCD", ("first", "second", "third", "fourth"))],
        "correct_answer": "A",
        "explanation": {"short": "The first choice fits.", "key_evidence": "Original statement.",
                        "why_correct": "The first choice matches the evidence.",
                        "detailed": "The first choice is supported by the original statement.",
                        "choice_analysis": {label: "Development explanation " + label for label in "ABCD"}},
        "classification": {"domain": "Craft and Structure", "skill": "Words in Context"},
        **extra,
    }


REF = {"label": "2031年4月机考真题第1套(亚太A卷)", "kind": "real_exam", "tier": 1, "year": 2031, "month": 4,
       "region": "apac", "module": "读写 · Module 1", "number": 7}


class MetadataFromTablesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tables, problems = DB.read_tables(ROOT)
        assert problems == []
        cls.metadata = DB.question_metadata(cls.tables, ROOT)["questions"]

    def test_covers_exactly_the_published_questions(self):
        published = {row["question_id"] for row in self.tables["question"] if row["status"] == "published"}
        self.assertEqual(set(self.metadata), published)

    def test_classification_and_explanation_level_are_stated_for_every_question(self):
        taxonomy = DB.question_metadata(self.tables, ROOT)["taxonomy"]
        tasks = {row["id"]: row for row in taxonomy["tasks"]}
        passage_types = {row["id"] for row in taxonomy["passageTypes"]}
        levels = {"full": 0, "brief": 0, "none": 0}
        for question_id, entry in self.metadata.items():
            self.assertIn(entry["task"], tasks, question_id)
            self.assertEqual((entry["skill"], entry["domain"]),
                             (tasks[entry["task"]]["skill"], tasks[entry["task"]]["domain"]), question_id)
            self.assertIn(entry["passageType"], passage_types, question_id)
            self.assertIn(entry["importance"], ("core", "important", "basic"), question_id)
            levels[entry["explanation"]] += 1
        self.assertEqual(sum(levels.values()), len(self.metadata))
        self.assertTrue(all(levels.values()), levels)
        self.assertEqual([row["id"] for row in taxonomy["importance"]], ["core", "important", "basic"])
        self.assertAlmostEqual(sum(row["examShare"] for row in taxonomy["tasks"]), 100, delta=0.5)

    def test_every_question_is_real_or_practice_with_its_best_source_first(self):
        for question_id, entry in self.metadata.items():
            self.assertIn(entry["kind"], ("real_exam", "practice"), question_id)
            self.assertTrue(entry["refs"], question_id)
            self.assertEqual(entry["refs"][0]["tier"], entry["tier"], question_id)
            self.assertEqual(entry["refs"][0]["kind"], entry["kind"], question_id)
            self.assertEqual([ref["tier"] for ref in entry["refs"]],
                             sorted(ref["tier"] for ref in entry["refs"]), question_id)
            self.assertIn(entry["difficulty"], ("easy", "medium", "hard", None), question_id)


class SourceNumberTests(unittest.TestCase):
    """A source reference gives the number the question has in that source, never a position elsewhere."""

    @classmethod
    def setUpClass(cls):
        cls.tables, problems = DB.read_tables(ROOT)
        assert problems == []

    def numbers(self, tables, question_id):
        return [(ref["label"], ref["module"], ref["number"])
                for ref in DB.question_metadata(tables, ROOT)["questions"][question_id]["refs"]]

    def test_a_renumbered_set_keeps_the_original_number(self):
        item = next(row for row in self.tables["material_item"] if row["question_id"] == "oct_us2_015")
        self.assertEqual((item["question_number"], item["source_question_number"]), (16, 17))
        self.assertEqual([number for _, _, number in self.numbers(self.tables, "oct_us2_015")], [17])

    def test_an_imported_question_is_numbered_from_its_source_only(self):
        """The reference a page shows (the first) is the imported occurrence with its own number,
        and the set file placing the question in a new material adds no second number for that source."""
        metadata = DB.question_metadata(self.tables, ROOT)["questions"]
        occurrences, labels = {}, {row["source_id"]: row["label"] for row in self.tables["source"]}
        for row in self.tables["question_source"]:
            occurrences.setdefault(row["question_id"], []).append((labels[row["source_id"]], row["number"]))
        checked = 0
        for question_id, entry in metadata.items():
            if question_id in occurrences:
                shown = entry["refs"][0]
                if shown["label"] in {label for label, _ in occurrences[question_id]}:
                    self.assertIn((shown["label"], shown["number"]), occurrences[question_id], question_id)
                for label in {label for label, _ in occurrences[question_id]}:
                    numbers = [ref["number"] for ref in entry["refs"] if ref["label"] == label]
                    self.assertEqual(sorted(numbers), sorted(number for name, number in occurrences[question_id]
                                                             if name == label), question_id)
                checked += 1
        self.assertGreater(checked, 2000)
        # A collection's own list position is no paper number: the bank numbers this one 37.
        self.assertEqual([ref["number"] for ref in metadata["wic-easy-153473"]["refs"]], [37, None])

    def test_removing_and_reordering_questions_keeps_every_source_number(self):
        before = DB.question_metadata(self.tables, ROOT)["questions"]
        tables = json.loads(json.dumps(self.tables))
        content_id = next(row["content_id"] for row in tables["material_item"]
                          if row["question_id"].startswith("bank_rw_"))
        placed = sorted((row for row in tables["material_item"] if row["content_id"] == content_id),
                        key=lambda row: row["position"])
        removed = placed[1]["question_id"]
        kept = [row for row in placed if row["question_id"] != removed][::-1]
        for position, row in enumerate(kept, 1):
            row["position"] = row["question_number"] = position
        tables["material_item"] = [row for row in tables["material_item"]
                                   if row["content_id"] != content_id] + kept
        after = DB.question_metadata(tables, ROOT)["questions"]
        for row in kept:
            self.assertEqual(after[row["question_id"]]["refs"], before[row["question_id"]]["refs"], row["question_id"])

        # A legacy set renumbered by hand keeps the number it records for the source.
        tables = json.loads(json.dumps(self.tables))
        legacy = next(row for row in tables["material_item"] if row["question_id"] == "oct_us2_015")
        legacy["question_number"] = 3
        self.assertEqual([number for _, _, number in self.numbers(tables, "oct_us2_015")], [17])

    def test_a_position_in_a_material_of_another_source_is_not_a_paper_number(self):
        tables = json.loads(json.dumps(self.tables))
        materials = {row["content_id"]: row for row in tables["material"]}
        sources = {row["source_id"] for row in tables["source"]}
        item = next(row for row in tables["material_item"]
                    if row["question_id"] == "oct_us2_015")
        other = next(source for source in sorted(sources) if source != materials[item["content_id"]]["source_id"]
                     and not any(row["source_id"] == source and row["question_id"] == "oct_us2_015"
                                 for row in tables["question_source"]))
        item["source_id"], item["source_question_number"] = other, None
        self.assertEqual([number for _, _, number in self.numbers(tables, "oct_us2_015")], [None])


class BuilderFieldTests(unittest.TestCase):
    def test_fields_are_present_and_empty_without_tables(self):
        compact = BUILDER.compact_question(question(), "sets/demo.html", ROOT)
        self.assertEqual({key: compact[key] for key in ORIGIN_KEYS},
                         {"sourceKind": "", "sourceTier": None, "sourceYear": None,
                          "sourceRefs": [], "difficultyLevel": "", "difficultyBasis": ""})
        self.assertEqual((compact["task"], compact["passageType"], compact["importance"], compact["examSittings"],
                          compact["explanationLevel"]), ("", "", "", 0, "full"))
        page = BUILDER.public_question(ROOT, "sets/demo.html", question())
        self.assertEqual((page["source_kind"], page["source_tier"], page["source_year"],
                          page["source_refs"], page["difficulty_level"]), ("", None, None, [], ""))

    def test_origin_is_carried_into_both_shapes(self):
        origin = {"kind": "real_exam", "tier": 1, "year": 2031, "difficulty": "hard", "refs": [REF]}
        compact = BUILDER.compact_question(question(_origin=origin), "sets/demo.html", ROOT)
        self.assertEqual((compact["sourceKind"], compact["sourceTier"], compact["sourceYear"],
                          compact["difficultyLevel"], compact["sourceRefs"]), ("real_exam", 1, 2031, "hard", [REF]))
        page = BUILDER.public_question(ROOT, "sets/demo.html", question(_origin=origin))
        self.assertEqual((page["source_kind"], page["source_tier"], page["source_year"],
                          page["difficulty_level"], page["source_refs"]), ("real_exam", 1, 2031, "hard", [REF]))
        self.assertNotIn("_origin", compact)
        self.assertNotIn("_origin", page)

    def test_difficulty_level_accepts_only_the_three_levels(self):
        level = lambda **extra: BUILDER.compact_question(question(**extra), "sets/demo.html", ROOT)["difficultyLevel"]
        self.assertEqual(level(difficulty_estimate="Hard"), "hard")
        self.assertEqual(level(difficulty_estimate=3), "")
        self.assertEqual(level(difficulty_estimate="unknown"), "")
        self.assertEqual(level(difficulty_estimate="easy", _origin={"difficulty": "medium"}), "medium")
        both = lambda **extra: (lambda q: (q["difficultyLevel"], q["difficultyBasis"]))(
            BUILDER.compact_question(question(**extra), "sets/demo.html", ROOT))
        self.assertEqual(both(difficulty_estimate="easy"), ("easy", "estimate"))
        self.assertEqual(both(_origin={"difficulty": "hard", "difficultyBasis": "model"}), ("hard", "model"))
        self.assertEqual(both(), ("", ""))

    def test_classification_accepts_only_well_formed_values(self):
        compact = lambda **origin: BUILDER.compact_question(question(_origin=origin), "sets/demo.html", ROOT)
        self.assertEqual([compact(task=value)["task"] for value in ("wic_blank", "Not An Id", "<b>", None, 7)],
                         ["wic_blank", "", "", "", ""])
        self.assertEqual([compact(passageType=value)["passageType"] for value in ("science", "drama", "a b", None)],
                         ["science", "drama", "", ""])
        self.assertEqual([compact(importance=value)["importance"] for value in ("core", "important", "basic", "vital", None)],
                         ["core", "important", "basic", "", ""])
        self.assertEqual([compact(sittings=value)["examSittings"] for value in (2, 0, -1, "3", None)], [2, 0, 0, 0, 0])

    def test_taxonomy_is_empty_without_tables_and_rejects_markup(self):
        self.assertEqual(BUILDER.question_taxonomy(ROOT), {"passageTypes": [], "tasks": [], "importance": []})
        with tempfile.TemporaryDirectory(dir=scratch()) as temporary:
            root = Path(temporary)
            payload = {"schemaVersion": BUILDER.QUESTION_METADATA_SCHEMA, "questions": {}, "taxonomy": {
                "passageTypes": [{"id": "science", "name": "自然科学", "family": "information"}],
                "tasks": [{"id": "wic_blank", "name": "选词填空", "english": "Word", "skill": "Words in Context",
                           "domain": "Craft and Structure", "examShare": 12.5}],
                "importance": [{"id": "core", "name": "核心"}]}}
            (root / BUILDER.QUESTION_METADATA).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            taxonomy = BUILDER.question_taxonomy(root)
            self.assertEqual(taxonomy["tasks"][0]["examShare"], 12.5)
            self.assertEqual(taxonomy["passageTypes"], payload["taxonomy"]["passageTypes"])
            payload["taxonomy"]["tasks"][0]["name"] = "<b>选词填空</b>"
            (root / BUILDER.QUESTION_METADATA).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            with self.assertRaises(ValueError):
                BUILDER.question_taxonomy(root)

    def test_explanation_level_is_read_off_the_published_question(self):
        level = lambda explanation: BUILDER.compact_question(
            question(explanation=explanation), "sets/demo.html", ROOT)["explanationLevel"]
        full = question()["explanation"]
        blank = {key: "" for key in ("short", "key_evidence", "why_correct", "detailed")}
        self.assertEqual(level(full), "full")
        self.assertEqual(level({**full, "choice_analysis": {**full["choice_analysis"], "D": " "}}), "brief")
        self.assertEqual(level({**blank, "detailed": "One sentence.", "choice_analysis": {}}), "brief")
        self.assertEqual(level({**blank, "choice_analysis": {label: "" for label in "ABCD"}}), "none")

    def test_origin_does_not_change_the_grading_revision(self):
        self.assertEqual(BUILDER.grading_revision(question()),
                         BUILDER.grading_revision(question(_origin={"kind": "practice", "refs": [REF]})))

    def test_markup_in_a_source_label_is_refused(self):
        bad = {"kind": "practice", "refs": [{**REF, "kind": "practice", "label": "<b>label</b>"}]}
        with self.assertRaisesRegex(ValueError, "not plain"):
            BUILDER.compact_question(question(_origin=bad), "sets/demo.html", ROOT)


class BuiltSiteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(dir=scratch())
        cls.checkout = Path(cls.temporary.name) / "repo"
        PIPELINE.copy_public_inputs(ROOT, cls.checkout)
        shutil.copytree(ROOT / "content/db", cls.checkout / "content/db")
        shutil.copyfile(ROOT / "scripts/content_db.py", cls.checkout / "scripts/content_db.py")
        # Every release feature on: the whole published bank with its metadata.
        result = subprocess.run([sys.executable, "scripts/build-site.py", "--preview-all"], cwd=cls.checkout,
                                capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        cls.site = cls.checkout / "build/site-preview"
        cls.manifest = json.loads((cls.site / "sat-practice-manifest.json").read_text(encoding="utf-8"))
        # What sat-release.json releases: nothing beyond the reviewed baseline.
        result = subprocess.run([sys.executable, "scripts/build-site.py"], cwd=cls.checkout,
                                capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        cls.release = cls.checkout / "build/site"
        cls.release_manifest = json.loads((cls.release / "sat-practice-manifest.json").read_text(encoding="utf-8"))

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_manifest_questions_carry_origin_and_only_published_questions(self):
        tables, _ = DB.read_tables(ROOT)
        published = {row["question_id"] for row in tables["question"] if row["status"] == "published"}
        self.assertEqual({entry["questionId"] for entry in self.manifest["questions"]}, published)
        for entry in self.manifest["questions"]:
            self.assertLessEqual(ORIGIN_KEYS, set(entry), entry["questionId"])
            self.assertIn(entry["sourceKind"], ("real_exam", "practice"), entry["questionId"])
            self.assertTrue(entry["sourceRefs"], entry["questionId"])
            self.assertTrue(all(set(ref) == REF_KEYS for ref in entry["sourceRefs"]), entry["questionId"])
            self.assertIn(entry["difficultyLevel"], ("easy", "medium", "hard"), entry["questionId"])
            self.assertIn(entry["difficultyBasis"], ("source_file", "estimate", "model"), entry["questionId"])
        kinds = {entry["sourceKind"] for entry in self.manifest["questions"]}
        self.assertEqual(kinds, {"real_exam", "practice"})

    def test_manifest_classification_and_explanation_level_agree_with_the_tables(self):
        tables, _ = DB.read_tables(ROOT)
        stated_all = DB.question_metadata(tables, ROOT)
        metadata = stated_all["questions"]
        for entry in self.manifest["questions"]:
            stated = metadata[entry["questionId"]]
            self.assertEqual((entry["task"], entry["passageType"], entry["importance"], entry["examSittings"]),
                             (stated["task"], stated["passageType"], stated["importance"], stated["sittings"]),
                             entry["questionId"])
            self.assertEqual(entry["explanationLevel"], stated["explanation"], entry["questionId"])
        self.assertEqual(self.manifest["taxonomy"], stated_all["taxonomy"])

    def test_practice_pages_carry_the_same_origin(self):
        by_id = {entry["questionId"]: entry for entry in self.manifest["questions"]}
        html = (self.site / "sets/02-gold48.html").read_text(encoding="utf-8")
        for item in PIPELINE.questions_in_html(html):
            expected = by_id[item["question_id"]]
            self.assertEqual((item["source_kind"], item["source_tier"], item["source_year"],
                              item["source_refs"], item["difficulty_level"]),
                             (expected["sourceKind"], expected["sourceTier"], expected["sourceYear"],
                              expected["sourceRefs"], expected["difficultyLevel"]))

    def test_the_release_build_carries_no_switched_off_content(self):
        manifest = self.release_manifest
        self.assertEqual((manifest["uniqueQuestionCount"], manifest["pageCount"]), (1752, 48))
        self.assertFalse(any(manifest["features"].values()))
        self.assertEqual(manifest["vocabulary"], [])
        self.assertFalse(any(page["examEligible"] or page["examVariants"] for page in manifest["pages"]))
        full = {entry["questionId"]: entry for entry in self.manifest["questions"]}
        for entry in manifest["questions"]:
            # Origin and classification are the same; only the walkthrough no longer counts as an explanation.
            self.assertEqual(entry["sourceRefs"], full[entry["questionId"]]["sourceRefs"], entry["questionId"])
            self.assertIn(entry["explanationLevel"], ("full", "brief", "none"))
        for page in manifest["pages"]:
            html = (self.release / page["route"]).read_text(encoding="utf-8")
            for item in PIPELINE.questions_in_html(html):
                self.assertNotIn("walkthrough", item, page["route"])
                self.assertNotIn("vocabulary", item, page["route"])
            meta = json.loads(re.search(r'<script type="application/json" id="satPageMeta">(.*?)</script>', html, re.S).group(1))
            self.assertEqual((meta["examEligible"], meta["examVariants"], any(meta["features"].values())), (False, [], False))
            vocabulary = re.search(r'<script type="application/json" id="satPageVocabulary">(.*?)</script>', html, re.S).group(1)
            self.assertEqual(json.loads(vocabulary), {}, page["route"])
        self.assertFalse((self.release / "sat-release.json").exists())

    def test_metadata_file_is_not_left_in_the_site_and_drafts_do_not_leak(self):
        self.assertFalse((self.site / "sat-question-metadata.json").exists())
        tables, _ = DB.read_tables(ROOT)
        drafts = {row["question_id"] for row in tables["question"] if row["status"] == "draft"}
        self.assertTrue(drafts)
        index = (self.site / "index.html").read_text(encoding="utf-8")
        shown = set(re.findall(r"bank_(?:rw|math)_\d{6}", index + json.dumps(self.manifest)))
        self.assertEqual(shown & drafts, set())


if __name__ == "__main__":
    unittest.main()
