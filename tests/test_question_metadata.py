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

    def test_genre_and_explanation_level_are_stated_for_every_question(self):
        levels = {"full": 0, "brief": 0, "none": 0}
        for question_id, entry in self.metadata.items():
            self.assertIn(entry["genre"], ("poetry", "fiction", "drama", None), question_id)
            levels[entry["explanation"]] += 1
        self.assertEqual(sum(levels.values()), len(self.metadata))
        self.assertTrue(all(levels.values()), levels)

    def test_the_tagger_has_nothing_left_to_change(self):
        tagger = load("metadata_tag_genre", "scripts/tag_genre.py")
        section = {row["domain_id"]: row["section"] for row in self.tables["domain"]}
        skill = {row["skill_id"]: section[row["domain_id"]] for row in self.tables["skill"]}
        subtype = {row["subtype_id"]: skill[row["skill_id"]] for row in self.tables["subtype"]}
        for row in self.tables["question"]:
            expected = tagger.genre_of(row["passage"]) if subtype[row["subtype_id"]] == "reading_writing" else None
            self.assertEqual(row["genre"], expected, row["question_id"])

    def test_genre_rule_reads_only_the_passage_introduction(self):
        genre = load("metadata_tag_genre_rule", "scripts/tag_genre.py").genre_of
        self.assertEqual(genre("The following text is adapted from an 1899 novel. A traveller arrives."), "fiction")
        self.assertEqual(genre("The following text is from a 1912 poem \"Summer.\" It is summer."), "poetry")
        self.assertEqual(genre("Text 1 is a 1912 poem. Text 2 is from a 1913 poem."), "poetry")
        self.assertEqual(genre("\"Morning\" is a 1904 play by an Irish writer. In the play, ______"), "drama")
        self.assertEqual(genre("A critic's 1957 novel is regularly described as autobiography."), None)
        self.assertEqual(genre("The following text is from a 2010 book about the novel as a form."), None)
        self.assertEqual(genre("Researchers studied how poems are memorised."), None)

    def test_every_question_is_real_or_practice_with_its_best_source_first(self):
        for question_id, entry in self.metadata.items():
            self.assertIn(entry["kind"], ("real_exam", "practice"), question_id)
            self.assertTrue(entry["refs"], question_id)
            self.assertEqual(entry["refs"][0]["tier"], entry["tier"], question_id)
            self.assertEqual(entry["refs"][0]["kind"], entry["kind"], question_id)
            self.assertEqual([ref["tier"] for ref in entry["refs"]],
                             sorted(ref["tier"] for ref in entry["refs"]), question_id)
            self.assertIn(entry["difficulty"], ("easy", "medium", "hard", None), question_id)


class BuilderFieldTests(unittest.TestCase):
    def test_fields_are_present_and_empty_without_tables(self):
        compact = BUILDER.compact_question(question(), "sets/demo.html", ROOT)
        self.assertEqual({key: compact[key] for key in ORIGIN_KEYS},
                         {"sourceKind": "", "sourceTier": None, "sourceYear": None,
                          "sourceRefs": [], "difficultyLevel": "", "difficultyBasis": ""})
        self.assertEqual((compact["genre"], compact["explanationLevel"]), ("", "full"))
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

    def test_genre_accepts_only_the_known_kinds(self):
        genre = lambda value: BUILDER.compact_question(
            question(_origin={"genre": value}), "sets/demo.html", ROOT)["genre"]
        self.assertEqual([genre(value) for value in ("poetry", "fiction", "drama", "essay", None)],
                         ["poetry", "fiction", "drama", "", ""])

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
        result = subprocess.run([sys.executable, "scripts/build-site.py"], cwd=cls.checkout,
                                capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        cls.site = cls.checkout / "build/site"
        cls.manifest = json.loads((cls.site / "sat-practice-manifest.json").read_text(encoding="utf-8"))

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

    def test_manifest_genre_and_explanation_level_agree_with_the_tables(self):
        tables, _ = DB.read_tables(ROOT)
        metadata = DB.question_metadata(tables, ROOT)["questions"]
        for entry in self.manifest["questions"]:
            stated = metadata[entry["questionId"]]
            self.assertEqual(entry["genre"], stated["genre"] or "", entry["questionId"])
            self.assertEqual(entry["explanationLevel"], stated["explanation"], entry["questionId"])

    def test_practice_pages_carry_the_same_origin(self):
        by_id = {entry["questionId"]: entry for entry in self.manifest["questions"]}
        html = (self.site / "sets/02-gold48.html").read_text(encoding="utf-8")
        for item in PIPELINE.questions_in_html(html):
            expected = by_id[item["question_id"]]
            self.assertEqual((item["source_kind"], item["source_tier"], item["source_year"],
                              item["source_refs"], item["difficulty_level"]),
                             (expected["sourceKind"], expected["sourceTier"], expected["sourceYear"],
                              expected["sourceRefs"], expected["difficultyLevel"]))

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
