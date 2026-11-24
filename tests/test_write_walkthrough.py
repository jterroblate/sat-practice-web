"""A walkthrough reads the question as it stands, covers every choice, and is shown only as an addition to the question."""

from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import re
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


def load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


WRITER = load("write_walkthrough_under_test", "scripts/write_walkthrough.py")
BUILDER = load("walkthrough_builder", "skills/skill-sat-rw-classification/scripts/build_learning_experience.py")
DB = WRITER.db
TABLES, PROBLEMS = DB.read_tables(ROOT)
CHINESE = re.compile("[一-鿿]")

PASSAGE = "The fields [u]abut[/u] a river. They flood in spring."
PROMPT = "Which choice completes the text?"


def tiny_tables() -> dict:
    return {
        "question": [{"question_id": "q1", "passage": PASSAGE, "prompt": PROMPT, "status": "published"}],
        "choice": [{"question_id": "q1", "label": label, "text": label.lower()} for label in "ABCD"],
        "walkthrough": [], "walkthrough_segment": [], "walkthrough_choice": [],
    }


def tiny_entry(**changes) -> dict:
    entry = {
        "question_id": "q1", "basis": "ai_draft",
        "approach": {"en": "Find the verb.", "zh": "找出动词。"},
        "segments": [
            {"field": "passage", "excerpt": "The fields abut a river.", "en": "The fields touch a river.", "zh": "田地紧挨着一条河。"},
            {"field": "passage", "excerpt": "They flood in spring.", "en": "Water covers them in spring.", "zh": "春天会被水淹没。"},
            {"field": "prompt", "excerpt": PROMPT, "en": "Choose the word that fits.", "zh": "选出合适的词。"}],
        "choices": {label: {"en": ("Correct. " if label == "A" else "") + "A note.", "zh": "说明。"} for label in "ABCD"},
    }
    entry.update(changes)
    return entry


class SplitPassageTests(unittest.TestCase):
    def test_sentences_and_list_items_come_out_in_reading_order(self):
        self.assertEqual(WRITER.split_passage("Notes:\n\n• One fact.\n• Another fact. It has two sentences."),
                         ["Notes:", "• One fact.", "• Another fact.", "It has two sentences."])

    def test_inline_markers_are_removed_and_tables_left_out(self):
        self.assertEqual(WRITER.split_passage("Title\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\nThe [u]text[/u] <i>begins</i> here."),
                         ["Title", "The text begins here."])

    def test_abbreviations_and_initials_do_not_end_a_sentence(self):
        text = ("Dr. Abut met J. K. Rind in the Brown v. Board case. Yakushev et al. studied metal. "
                "The sample was 84.8 dB. \"It works,\" she said. Then it ended.")
        self.assertEqual(WRITER.split_passage(text), [
            "Dr. Abut met J. K. Rind in the Brown v. Board case.", "Yakushev et al. studied metal.",
            "The sample was 84.8 dB.", "\"It works,\" she said.", "Then it ended."])

    def test_a_word_that_merely_ends_like_an_abbreviation_ends_its_sentence(self):
        self.assertEqual(WRITER.split_passage("It acts like a metal. Weeds grow across North America. Although small, they spread."),
                         ["It acts like a metal.", "Weeds grow across North America.", "Although small, they spread."])


class EntryTests(unittest.TestCase):
    def read(self, entry: dict) -> list[dict]:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "entries.jsonl"
            path.write_text(json.dumps(entry, ensure_ascii=False) + "\n", encoding="utf-8")
            return WRITER.read_entries(path)

    def test_a_complete_entry_is_read_with_its_excerpts_as_plain_text(self):
        entry = tiny_entry()
        entry["segments"][0]["excerpt"] = "The fields [u]abut[/u]   a river."
        [read] = self.read(entry)
        self.assertEqual(read["segments"][0]["excerpt"], "The fields abut a river.")
        self.assertEqual((read["basis"], list(read["choices"])), ("ai_draft", list("ABCD")))

    def test_incomplete_or_marked_up_entries_are_refused(self):
        good = tiny_entry()
        broken = [
            {**good, "approach": {"en": "Only English.", "zh": " "}},
            {**good, "segments": [{**good["segments"][0], "field": "choices"}]},
            {**good, "segments": [{**good["segments"][0], "excerpt": " "}]},
            {**good, "segments": [{**good["segments"][0], "zh": "<b>粗体</b>"}]},
            {**good, "choices": {**good["choices"], "B": {"en": "No Chinese.", "zh": ""}}},
            {**good, "basis": "checked"},
            {key: value for key, value in good.items() if key != "segments"},
        ]
        for entry in broken:
            with self.subTest(entry=str(entry)[:60]), self.assertRaises(DB.ContentError):
                self.read(entry)

    def test_an_entry_replaces_the_walkthrough_its_question_had(self):
        tables = tiny_tables()
        WRITER.add_entries(tables, self.read(tiny_entry()), {"q1"})
        self.assertEqual(len(tables["walkthrough_segment"]), 3)
        WRITER.add_entries(tables, self.read(tiny_entry(segments=tiny_entry()["segments"][:1], basis="human_checked")), {"q1"})
        self.assertEqual([row["position"] for row in tables["walkthrough_segment"]], [1])
        self.assertEqual([row["basis"] for row in tables["walkthrough"]], ["human_checked"])
        self.assertEqual(sorted(row["label"] for row in tables["walkthrough_choice"]), list("ABCD"))

    def test_only_questions_that_take_a_walkthrough_are_written(self):
        entries = self.read(tiny_entry())
        with self.assertRaises(DB.ContentError):
            WRITER.add_entries(tiny_tables(), entries, set())
        with self.assertRaises(DB.ContentError):
            WRITER.add_entries(tiny_tables(), entries + entries, {"q1"})

    def test_a_template_holds_the_text_and_leaves_the_notes_empty(self):
        [template] = WRITER.templates(tiny_tables(), ["q1"])
        self.assertEqual([segment["excerpt"] for segment in template["segments"]],
                         ["The fields abut a river.", "They flood in spring.", PROMPT])
        self.assertEqual(template["segments"][-1]["field"], "prompt")
        self.assertEqual(template["choices"], {label: {"en": "", "zh": ""} for label in "ABCD"})
        with self.assertRaises(DB.ContentError):
            WRITER.templates(tiny_tables(), ["missing"])


class IntegrityCheckTests(unittest.TestCase):
    def tables(self, **changes) -> dict:
        tables = tiny_tables()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "entries.jsonl"
            path.write_text(json.dumps(tiny_entry(**changes), ensure_ascii=False) + "\n", encoding="utf-8")
            WRITER.add_entries(tables, WRITER.read_entries(path), {"q1"})
        return tables

    def test_a_consistent_walkthrough_has_no_problems(self):
        self.assertEqual(DB.walkthrough_problems(self.tables()), [])
        self.assertEqual(DB.walkthrough_problems(tiny_tables()), [])

    def test_an_excerpt_must_stand_in_its_field_in_reading_order(self):
        segments = tiny_entry()["segments"]
        for broken in ([{**segments[0], "excerpt": "The fields border a river."}, *segments[1:]],
                       [segments[1], segments[0], segments[2]],
                       [segments[0], segments[0], segments[2]],
                       [{**segments[0], "field": "prompt"}, *segments[1:]]):
            with self.subTest(first=broken[0]["excerpt"]):
                problems = DB.walkthrough_problems(self.tables(segments=broken))
                self.assertTrue(problems)

    def test_the_passage_is_read_before_the_prompt(self):
        segments = tiny_entry()["segments"]
        problems = DB.walkthrough_problems(self.tables(segments=[segments[2], segments[0]]))
        self.assertTrue(any("before the prompt" in problem for problem in problems))

    def test_segments_are_numbered_from_one_and_there_is_at_least_one(self):
        tables = self.tables()
        tables["walkthrough_segment"] = [row for row in tables["walkthrough_segment"] if row["position"] != 2]
        self.assertTrue(any("numbered from 1" in problem for problem in DB.walkthrough_problems(tables)))
        tables["walkthrough_segment"] = []
        self.assertTrue(any("numbered from 1" in problem for problem in DB.walkthrough_problems(tables)))

    def test_every_choice_of_the_question_has_a_note(self):
        tables = self.tables()
        tables["walkthrough_choice"] = [row for row in tables["walkthrough_choice"] if row["label"] != "C"]
        [problem] = DB.walkthrough_problems(tables)
        self.assertIn("one note for each of its choices", problem)

    def test_a_stale_excerpt_is_reported_when_the_passage_changes(self):
        tables = self.tables()
        tables["question"][0]["passage"] = "The fields border a river. They flood in spring."
        self.assertTrue(DB.walkthrough_problems(tables))


class StoredWalkthroughTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.connection = DB.load_sqlite(TABLES, ROOT)
        cls.questions = {row["question_id"]: row for row in TABLES["question"]}

    @classmethod
    def tearDownClass(cls):
        cls.connection.close()

    def test_the_tables_are_readable_and_consistent(self):
        self.assertEqual(PROBLEMS, [])
        self.assertEqual(DB.walkthrough_problems(TABLES), [])

    def test_every_core_question_has_a_walkthrough(self):
        rows = {importance: (questions, walked) for importance, _, questions, walked in
                self.connection.execute("SELECT * FROM walkthrough_coverage")}
        questions, walked = rows["core"]
        self.assertEqual(walked, questions)

    def test_only_published_reading_and_writing_questions_have_one(self):
        takes = WRITER.reading_questions(TABLES, ROOT)
        self.assertLessEqual({row["question_id"] for row in TABLES["walkthrough"]}, takes)

    def test_a_question_with_a_walkthrough_counts_as_fully_explained(self):
        levels = {level for (level,) in self.connection.execute(
            "SELECT x.level FROM question_explanation x JOIN walkthrough w USING (question_id)")}
        self.assertEqual(levels, {"full"})

    def test_the_note_on_the_keyed_choice_and_no_other_says_it_is_correct(self):
        # The page marks the choice from the answer key; a note that disagrees with the key would mislead.
        for row in TABLES["walkthrough_choice"]:
            keyed = self.questions[row["question_id"]]["correct_answer"] == row["label"]
            with self.subTest(question=row["question_id"], label=row["label"]):
                self.assertEqual(row["note_en"].startswith("Correct."), keyed)
                self.assertEqual(row["note_zh"].startswith("正确。"), keyed)
                self.assertEqual(row["note_zh"].startswith("错误。"), not keyed)

    def test_every_text_is_written_in_its_own_language(self):
        texts = [(row["approach_en"], row["approach_zh"], row["question_id"]) for row in TABLES["walkthrough"]]
        texts += [(row["note_en"], row["note_zh"], row["question_id"]) for row in TABLES["walkthrough_segment"]]
        texts += [(row["note_en"], row["note_zh"], row["question_id"]) for row in TABLES["walkthrough_choice"]]
        for english, chinese, question_id in texts:
            with self.subTest(question=question_id):
                self.assertNotRegex(english, CHINESE)
                self.assertRegex(chinese, CHINESE)
                self.assertNotIn("|", english + chinese)

    def test_each_walkthrough_reads_the_passage_and_ends_with_the_prompt(self):
        fields: dict[str, list[str]] = {}
        for row in sorted(TABLES["walkthrough_segment"], key=lambda row: (row["question_id"], row["position"])):
            fields.setdefault(row["question_id"], []).append(row["field"])
        for question_id, order in fields.items():
            with self.subTest(question=question_id):
                self.assertEqual(order[-1], "prompt")
                self.assertEqual(order.count("prompt"), 1)
                self.assertIn("passage", order)

    def test_exporting_and_importing_unchanged_changes_nothing(self):
        names = ("question", "choice", *DB.WALKTHROUGH_TABLES)
        tables = copy.deepcopy({name: TABLES[name] for name in names})
        exported = WRITER.export_entries(tables)
        self.assertEqual(len(exported), len(TABLES["walkthrough"]))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "export.jsonl"
            WRITER.write_lines(path, exported)
            WRITER.add_entries(tables, WRITER.read_entries(path), {entry["question_id"] for entry in exported})
        for name in DB.WALKTHROUGH_TABLES:
            self.assertEqual(sorted(map(json.dumps, tables[name])), sorted(map(json.dumps, TABLES[name])), name)

    def test_the_site_metadata_carries_each_walkthrough_whole(self):
        metadata = DB.question_metadata(TABLES, ROOT)
        self.assertEqual(metadata["schemaVersion"], BUILDER.QUESTION_METADATA_SCHEMA)
        walked = {question_id for question_id, entry in metadata["questions"].items() if "walkthrough" in entry}
        self.assertEqual(walked, {row["question_id"] for row in TABLES["walkthrough"]})
        question_id = TABLES["walkthrough"][0]["question_id"]
        stated = metadata["questions"][question_id]["walkthrough"]
        self.assertEqual(set(stated), {"approach", "basis", "segments", "choices"})
        self.assertEqual(len(stated["segments"]),
                         sum(row["question_id"] == question_id for row in TABLES["walkthrough_segment"]))
        self.assertEqual(metadata["questions"][question_id]["explanation"], "full")


class BuilderWalkthroughTests(unittest.TestCase):
    STATED = {
        "approach": {"english": "Find the verb.", "chinese": "找出动词。"}, "basis": "ai_draft",
        "segments": [{"field": "passage", "excerpt": "The fields abut a river.", "english": "They touch.", "chinese": "紧挨着。"},
                     {"field": "prompt", "excerpt": PROMPT, "english": "Choose.", "chinese": "选择。"}],
        "choices": {label: {"english": "A note.", "chinese": "说明。"} for label in "ABCD"},
    }

    def question(self, stated=None) -> dict:
        question = {"question_id": "q1", "passage": PASSAGE, "prompt": PROMPT, "correct_answer": "A",
                    "choices": [{"label": label, "text": label.lower()} for label in "ABCD"], "explanation": {}}
        if stated is not None:
            question["_origin"] = {"walkthrough": stated}
        return question

    def test_a_walkthrough_is_copied_without_its_basis(self):
        self.assertEqual(BUILDER.question_walkthrough(self.question(self.STATED)),
                         {key: value for key, value in self.STATED.items() if key != "basis"})
        self.assertIsNone(BUILDER.question_walkthrough(self.question()))

    def test_a_malformed_walkthrough_stops_the_build(self):
        good = self.STATED
        for broken in ({**good, "segments": []},
                       {**good, "segments": [{**good["segments"][0], "field": "choices"}]},
                       {**good, "segments": [{**good["segments"][0], "english": "<i>They touch.</i>"}]},
                       {**good, "segments": [{**good["segments"][0], "excerpt": ""}]},
                       {**good, "approach": {"english": "Only English."}},
                       {**good, "choices": {label: good["choices"][label] for label in "ABC"}},
                       {**good, "choices": {**good["choices"], "E": good["choices"]["A"]}},
                       "text"):
            with self.subTest(broken=str(broken)[:60]), self.assertRaises(ValueError):
                BUILDER.question_walkthrough(self.question(broken))

    def test_a_walkthrough_makes_the_explanation_full(self):
        self.assertEqual(BUILDER.explanation_level(self.question()), "none")
        self.assertEqual(BUILDER.explanation_level(self.question(self.STATED)), "full")

    def test_a_walkthrough_leaves_the_grading_revision_alone(self):
        self.assertEqual(BUILDER.grading_revision(self.question()), BUILDER.grading_revision(self.question(self.STATED)))


if __name__ == "__main__":
    unittest.main()
