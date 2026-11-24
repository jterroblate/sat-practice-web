"""Hard words are explained once, found in the question text as recorded, and opened at the right moment."""

from __future__ import annotations

import collections
import copy
import importlib.util
import json
from pathlib import Path
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


ANNOTATOR = load("annotate_vocabulary_under_test", "scripts/annotate_vocabulary.py")
BUILDER = load("vocabulary_builder", "skills/skill-sat-rw-classification/scripts/build_learning_experience.py")
DB = ANNOTATOR.db
TABLES, PROBLEMS = DB.read_tables(ROOT)


def tiny_tables(passage: str, prompt: str, choices: list[str]) -> dict:
    """One question and one explained word ("abut"), enough for the linking rules."""
    return {
        "question": [{"question_id": "q1", "passage": passage, "prompt": prompt, "status": "published"}],
        "choice": [{"question_id": "q1", "label": "ABCD"[index], "text": text} for index, text in enumerate(choices)],
        "vocabulary": [], "vocabulary_form": [{"form": "abut", "vocab_id": "abut"}, {"form": "abuts", "vocab_id": "abut"}],
        "vocabulary_example": [], "question_vocabulary": [],
    }


EXAMPLES = [{"en": "The gardens abut a park.", "zh": "花园紧挨着公园。"},
            {"en": "Two farms abut each other.", "zh": "两座农场彼此相邻。"},
            {"en": "The school abuts a busy road.", "zh": "学校紧挨着一条繁忙的马路。"}]


def example_rows(vocab_id: str, count: int = 3) -> list[dict]:
    return [{"vocab_id": vocab_id, "position": position, "example_en": example["en"], "example_zh": example["zh"]}
            for position, example in enumerate(EXAMPLES[:count], 1)]


def links(tables: dict) -> set[tuple[str, str, str]]:
    return {(row["field"], row["surface"], row["reveal"]) for row in tables["question_vocabulary"]}


class LinkingRuleTests(unittest.TestCase):
    def test_passage_and_prompt_words_open_at_once_and_choice_words_after_submitting(self):
        tables = tiny_tables("The fields abut a river.", "Which word means abut?", ["abuts", "joins", "ends", "runs"])
        self.assertEqual(ANNOTATOR.link(tables, [("q1", "wic_blank")]), 3)
        self.assertEqual(links(tables), {("passage", "abut", "always"), ("prompt", "abut", "always"),
                                         ("choices", "abuts", "after_submit")})

    def test_the_word_a_meaning_question_asks_about_stays_closed_until_submitting(self):
        tables = tiny_tables("Their lands [u]abut[/u] the sea.", 'As used in the text, what does the word "abut" most nearly mean?',
                             ["touch", "leave", "cross", "watch"])
        ANNOTATOR.link(tables, [("q1", "wic_meaning")])
        self.assertEqual(links(tables), {("passage", "abut", "after_submit"), ("prompt", "abut", "after_submit")})

    def test_a_capitalised_word_inside_a_sentence_is_a_name(self):
        tables = tiny_tables("The printer Clementina Abut and Dr. Abut worked here. Abut the wall stood a mill.", "Which choice fits?",
                             ["Abut", "the firm Abut", "a", "b"])
        ANNOTATOR.link(tables, [("q1", None)])
        self.assertEqual(links(tables), {("passage", "Abut", "always"), ("choices", "Abut", "after_submit")})
        tables = tiny_tables("She read ArtMed aBut notes about Abut.", "Which choice fits?", ["a", "b", "c", "d"])
        self.assertEqual(ANNOTATOR.link(tables, [("q1", None)]), 0)

    def test_markers_end_a_word_as_they_do_on_the_page(self):
        tables = tiny_tables("The walls ab[u]ut[/u] and [i]abut[/i] again.", "Which choice fits?", ["a", "b", "c", "d"])
        ANNOTATOR.link(tables, [("q1", None)])
        self.assertEqual(links(tables), {("passage", "abut", "always")})

    def test_linking_again_adds_nothing_and_keeps_a_readers_change(self):
        tables = tiny_tables("The fields abut a river.", "Which choice fits?", ["a", "b", "c", "d"])
        ANNOTATOR.link(tables, [("q1", None)])
        tables["question_vocabulary"][0]["vocab_id"] = "another-sense"
        self.assertEqual(ANNOTATOR.link(tables, [("q1", None)]), 0)
        self.assertEqual(tables["question_vocabulary"][0]["vocab_id"], "another-sense")

    def test_a_spelling_belongs_to_one_explanation(self):
        tables = tiny_tables("x", "y", ["a", "b", "c", "d"])
        entry = {"vocab_id": "adjoin", "forms": ["abut"], "basis": "ai_draft", "examples": EXAMPLES,
                 **{field: "text" for field in ANNOTATOR.ENTRY_FIELDS}}
        with self.assertRaises(DB.ContentError):
            ANNOTATOR.add_entries(tables, [entry])

    def test_entries_must_be_complete_plain_text(self):
        good = {"forms": ["Abut"], "headword": "abut", "part_of_speech": "v.", "definition_en": "to touch along one side",
                "definition_zh": "紧挨", "examples": EXAMPLES}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "entries.jsonl"
            path.write_text(json.dumps(good, ensure_ascii=False) + "\n", encoding="utf-8")
            [entry] = ANNOTATOR.read_entries(path)
            self.assertEqual((entry["vocab_id"], entry["forms"], entry["basis"]), ("abut", ["abut"], "ai_draft"))
            self.assertEqual(entry["examples"], EXAMPLES)
            for broken in ({**good, "examples": [{**EXAMPLES[0], "zh": " "}, *EXAMPLES[1:]]}, {**good, "forms": []},
                           {**good, "definition_en": "<b>to touch</b>"}, {**good, "basis": "checked"},
                           {**good, "examples": EXAMPLES[:2]}, {**good, "examples": [EXAMPLES[0]] * 3},
                           {**good, "examples": [{**EXAMPLES[0], "en": "<i>abut</i>"}, *EXAMPLES[1:]]}):
                path.write_text(json.dumps(broken, ensure_ascii=False) + "\n", encoding="utf-8")
                with self.assertRaises(DB.ContentError):
                    ANNOTATOR.read_entries(path)

    def test_ids_are_made_from_the_headword(self):
        self.assertEqual(ANNOTATOR.slug("in accordance with"), "in-accordance-with")
        self.assertEqual(ANNOTATOR.slug("ne'er"), "ne-er")

    def test_an_entry_that_comes_back_replaces_its_spellings_and_orphaned_links_go(self):
        tables = tiny_tables("The fields abut a river that abuts a road.", "Which choice fits?", ["a", "b", "c", "d"])
        ANNOTATOR.link(tables, [("q1", None)])
        self.assertEqual({row["surface"] for row in tables["question_vocabulary"]}, {"abut", "abuts"})
        tables["vocabulary_example"] = [{"vocab_id": "abut", "position": 1, "example_en": "old", "example_zh": "旧"}]
        entry = {"vocab_id": "abut", "forms": ["abut", "abutting"], "basis": "human_checked", "examples": EXAMPLES,
                 **{field: "text" for field in ANNOTATOR.ENTRY_FIELDS}}
        self.assertEqual(ANNOTATOR.add_entries(tables, [entry]), 1)
        self.assertEqual(tables["vocabulary_example"], example_rows("abut"))
        self.assertEqual({row["form"] for row in tables["vocabulary_form"]}, {"abut", "abutting"})
        self.assertEqual([row["surface"] for row in tables["question_vocabulary"]], ["abut"])
        self.assertEqual(tables["vocabulary"][0]["basis"], "human_checked")
        self.assertEqual(ANNOTATOR.link(tables, [("q1", None)]), 0)


class IntegrityCheckTests(unittest.TestCase):
    def tables(self):
        tables = tiny_tables("The fields abut a river.", "Which choice fits?", ["a", "b", "c", "d"])
        tables["vocabulary"] = [{"vocab_id": "abut", "headword": "abut"}]
        tables["vocabulary_example"] = example_rows("abut")
        ANNOTATOR.link(tables, [("q1", None)])
        return tables

    def test_a_consistent_list_has_no_problems(self):
        self.assertEqual(DB.vocabulary_problems(self.tables()), [])
        second = self.tables()
        second["vocabulary"].append({"vocab_id": "abut-2", "headword": "abut"})
        second["vocabulary_form"].append({"form": "abutting", "vocab_id": "abut-2"})
        second["vocabulary_example"] += example_rows("abut-2")
        self.assertEqual(DB.vocabulary_problems(second), [])

    def test_an_explanation_needs_three_examples_numbered_from_one(self):
        for rows in (example_rows("abut", 2), [], [{**row, "position": row["position"] + 1} for row in example_rows("abut")]):
            tables = self.tables()
            tables["vocabulary_example"] = rows
            with self.subTest(rows=len(rows)):
                [problem] = DB.vocabulary_problems(tables)
                self.assertIn("needs at least 3 examples", problem)

    def test_a_link_must_use_a_known_spelling(self):
        tables = self.tables()
        tables["vocabulary_form"] = [{"form": "abuts", "vocab_id": "abut"}]
        [problem] = DB.vocabulary_problems(tables)
        self.assertIn("not a spelling", problem)

    def test_an_explanation_needs_a_spelling(self):
        tables = self.tables()
        tables["vocabulary"].append({"vocab_id": "arid", "headword": "arid"})
        tables["vocabulary_example"] += example_rows("arid")
        [problem] = DB.vocabulary_problems(tables)
        self.assertIn("'arid' has no spelling", problem)

    def test_an_id_must_come_from_its_headword(self):
        for vocab_id in ("adjoin", "abut-10", "abutx"):
            tables = self.tables()
            tables["vocabulary"] = [{"vocab_id": vocab_id, "headword": "abut"}]
            tables["vocabulary_example"] = example_rows(vocab_id)
            tables["vocabulary_form"] = [{"form": "abut", "vocab_id": vocab_id}, {"form": "abuts", "vocab_id": vocab_id}]
            with self.subTest(vocab_id=vocab_id):
                self.assertTrue(any("not made from the headword" in problem for problem in DB.vocabulary_problems(tables)))


class StoredVocabularyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.connection = DB.load_sqlite(TABLES, ROOT)

    @classmethod
    def tearDownClass(cls):
        cls.connection.close()

    def test_the_tables_are_readable(self):
        self.assertEqual(PROBLEMS, [])
        self.assertEqual(DB.vocabulary_problems(TABLES), [])

    def test_a_missing_word_is_reported(self):
        tables = copy.copy(TABLES)
        row = dict(TABLES["question_vocabulary"][0], surface="zzzabsent")
        tables["question_vocabulary"] = [row]
        [problem] = DB.vocabulary_problems(tables)
        self.assertIn("zzzabsent", problem)

    def test_every_explanation_is_used_and_every_spelling_explained(self):
        explained = {row["vocab_id"] for row in TABLES["vocabulary"]}
        self.assertEqual({row["vocab_id"] for row in TABLES["question_vocabulary"]}, explained)
        self.assertEqual({row["vocab_id"] for row in TABLES["vocabulary_form"]}, explained)

    def test_nearly_every_core_question_has_explained_words(self):
        rows = {importance: (questions, annotated) for importance, _, questions, annotated in
                self.connection.execute("SELECT * FROM vocabulary_coverage")}
        questions, annotated = rows["core"]
        self.assertGreaterEqual(annotated / questions, 0.95, f"{annotated} of {questions} core questions")

    def test_only_published_reading_and_writing_questions_are_linked(self):
        stray = self.connection.execute(
            "SELECT count(*) FROM question_vocabulary v JOIN practice_pool p USING (question_id)"
            " WHERE p.status <> 'published' OR p.section <> 'reading_writing'").fetchone()[0]
        self.assertEqual(stray, 0)

    def test_choice_words_never_open_before_submitting(self):
        early = [row for row in TABLES["question_vocabulary"] if row["field"] == "choices" and row["reveal"] != "after_submit"]
        self.assertEqual(early, [])
        with self.assertRaises(Exception):
            self.connection.execute(
                "INSERT INTO question_vocabulary VALUES (?, 'choices', 'zzz', ?, 'always')",
                (TABLES["question_vocabulary"][0]["question_id"], TABLES["vocabulary"][0]["vocab_id"]))

    def test_exporting_and_importing_unchanged_changes_nothing(self):
        names = ("question", "choice", "vocabulary", "vocabulary_example", "vocabulary_form", "question_vocabulary")
        tables = copy.deepcopy({name: TABLES[name] for name in names})
        exported = ANNOTATOR.export_entries(tables)
        self.assertEqual(len(exported), len(TABLES["vocabulary"]))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "export.jsonl"
            path.write_text("".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in exported), encoding="utf-8")
            self.assertEqual(ANNOTATOR.add_entries(tables, ANNOTATOR.read_entries(path)), 0)
        for name in ("vocabulary", "vocabulary_example", "vocabulary_form", "question_vocabulary"):
            self.assertEqual(sorted(map(json.dumps, tables[name])), sorted(map(json.dumps, TABLES[name])), name)

    def test_the_management_view_lists_every_explanation(self):
        rows = self.connection.execute(
            "SELECT count(*), min(spellings), min(examples), min(questions) FROM vocabulary_use").fetchone()
        self.assertEqual(rows, (len(TABLES["vocabulary"]), 1, DB.VOCABULARY_EXAMPLES, 1))
        with self.assertRaises(Exception):
            first = TABLES["vocabulary"][0]
            self.connection.execute("INSERT INTO vocabulary VALUES ('zzz', ?, 'n.', ?, 'x', 'ai_draft')",
                                    (first["headword"], first["definition_en"]))

    def test_an_example_is_not_repeated_for_its_word(self):
        with self.assertRaises(Exception):
            first = TABLES["vocabulary_example"][0]
            self.connection.execute("INSERT INTO vocabulary_example VALUES (?, 99, ?, 'x')",
                                    (first["vocab_id"], first["example_en"]))

    def test_linking_the_recorded_scope_again_changes_nothing(self):
        tables = copy.deepcopy({name: TABLES[name] for name in
                                ("question", "choice", "vocabulary", "vocabulary_example", "vocabulary_form",
                                 "question_vocabulary")})
        linked = sorted({row["question_id"] for row in TABLES["question_vocabulary"]})
        tasks = {row["question_id"]: row["task_id"] for row in TABLES["question"]}
        self.assertEqual(ANNOTATOR.link(tables, [(question_id, tasks[question_id]) for question_id in linked]), 0)

    def test_every_example_uses_its_word_and_comes_from_no_question(self):
        forms = collections.defaultdict(set)
        for row in TABLES["vocabulary_form"]:
            forms[row["vocab_id"]].add(row["form"])
        texts = DB.gloss_texts(TABLES)
        corpus = "\n".join(text["passage"] + "\n" + text["choices"] for text in texts.values())
        words = {row["vocab_id"]: row for row in TABLES["vocabulary"]}
        self.assertGreaterEqual(len(TABLES["vocabulary_example"]), DB.VOCABULARY_EXAMPLES * len(words))
        for row in TABLES["vocabulary_example"]:
            word = words[row["vocab_id"]]
            with self.subTest(word=row["vocab_id"], position=row["position"]):
                example = row["example_en"].lower().replace("’", "'")
                stems = {form[:max(4, len(form) - 3)] for form in forms[row["vocab_id"]] | {word["headword"].lower()}}
                self.assertTrue(any(stem in example for stem in stems), row["example_en"])
                self.assertNotIn(row["example_en"], corpus)
                self.assertRegex(row["example_zh"], "[一-鿿]")
                self.assertRegex(word["definition_zh"], "[一-鿿]")
                self.assertNotRegex(word["definition_en"] + row["example_en"], "[一-鿿]")

    def test_the_site_metadata_carries_the_explanations_the_questions_use(self):
        metadata = DB.question_metadata(TABLES, ROOT)
        used = {item["id"] for entry in metadata["questions"].values() for item in entry.get("vocabulary", [])}
        self.assertEqual(set(metadata["vocabulary"]), used)
        row = TABLES["question_vocabulary"][0]
        self.assertIn({"field": row["field"], "surface": row["surface"], "id": row["vocab_id"], "reveal": row["reveal"]},
                      metadata["questions"][row["question_id"]]["vocabulary"])
        entry = metadata["vocabulary"][row["vocab_id"]]
        self.assertEqual(set(entry), {"word", "partOfSpeech", "english", "chinese", "examples", "basis",
                                      "questions", "realExamQuestions", "occurrences"})
        for vocab_id, entry in metadata["vocabulary"].items():
            self.assertGreaterEqual(len(entry["examples"]), DB.VOCABULARY_EXAMPLES, vocab_id)
            self.assertTrue(all(set(example) == {"english", "chinese"} for example in entry["examples"]), vocab_id)
            # Every explained word is linked in a published question, so it occurs at least once.
            self.assertGreaterEqual(entry["questions"], 1, vocab_id)
            self.assertLessEqual(entry["realExamQuestions"], entry["questions"], vocab_id)
            self.assertLessEqual(entry["questions"], entry["occurrences"], vocab_id)

    def test_usage_counts_cover_every_question_the_word_is_written_in(self):
        published = {row["question_id"] for row in TABLES["question"] if row["status"] == "published"}
        usage = DB.vocabulary_usage(TABLES, published)
        linked = collections.defaultdict(set)
        for row in TABLES["question_vocabulary"]:
            linked[row["vocab_id"]].add(row["question_id"])
        for vocab_id, questions in linked.items():
            self.assertLessEqual(questions, usage[vocab_id]["questions"], vocab_id)


class UsageCountTests(unittest.TestCase):
    def test_a_word_is_counted_once_per_question_and_names_are_not_counted(self):
        tables = tiny_tables("The fields abut a river and abut a road. Mr. Abut left.", "Which choice fits?", ["Abuts", "a", "b", "c"])
        tables["question"].append({"question_id": "q2", "passage": "Nothing here.", "prompt": "Which?", "status": "published"})
        tables["vocabulary"] = [{"vocab_id": "abut"}, {"vocab_id": "arid"}]
        usage = DB.vocabulary_usage(tables, {"q1", "q2"})
        self.assertEqual(usage["abut"], {"questions": {"q1"}, "occurrences": 3})
        self.assertEqual(usage["arid"], {"questions": set(), "occurrences": 0})
        self.assertEqual(DB.vocabulary_usage(tables, {"q2"})["abut"]["occurrences"], 0)


class BuilderVocabularyTests(unittest.TestCase):
    ENTRY = {"word": "abut", "partOfSpeech": "v.", "english": "to touch along one side", "chinese": "紧挨",
             "examples": [{"english": example["en"], "chinese": example["zh"]} for example in EXAMPLES],
             "basis": "ai_draft"}

    def question(self, *items):
        return {"question_id": "q1", "_origin": {"vocabulary": list(items)}}

    def test_links_are_copied_as_stated(self):
        item = {"field": "passage", "surface": "abut", "id": "abut", "reveal": "always"}
        self.assertEqual(BUILDER.question_vocabulary(self.question(item)), [item])
        self.assertEqual(BUILDER.question_vocabulary({"question_id": "q1"}), [])

    def test_malformed_links_stop_the_build(self):
        good = {"field": "passage", "surface": "abut", "id": "abut", "reveal": "always"}
        for broken in ({**good, "field": "explanation"}, {**good, "reveal": "never"}, {**good, "id": "Abut!"},
                       {**good, "surface": "abut <b>"}, {**good, "field": "choices"}):
            with self.subTest(broken=broken), self.assertRaises(ValueError):
                BUILDER.question_vocabulary(self.question(broken))

    def test_a_page_gets_only_its_own_explanations_without_the_basis(self):
        stated = {"abut": self.ENTRY, "arid": {**self.ENTRY, "word": "arid"}}
        questions = [{"vocabulary": [{"field": "passage", "surface": "abut", "id": "abut", "reveal": "always"}]}, {}]
        self.assertEqual(BUILDER.page_vocabulary(stated, questions),
                         {"abut": {key: value for key, value in self.ENTRY.items() if key != "basis"}})
        self.assertEqual(BUILDER.page_vocabulary(None, [{}]), {})

    def test_explanations_must_be_present_and_plain(self):
        questions = [{"vocabulary": [{"field": "passage", "surface": "abut", "id": "abut", "reveal": "always"}]}]
        examples = self.ENTRY["examples"]
        for stated in ({}, {"abut": {**self.ENTRY, "examples": examples[:2]}},
                       {"abut": {**self.ENTRY, "examples": [{**examples[0], "chinese": ""}, *examples[1:]]}},
                       {"abut": {**self.ENTRY, "examples": [{**examples[0], "english": "<i>abut</i>"}, *examples[1:]]}},
                       {"abut": {**self.ENTRY, "english": "<i>to touch</i>"}}):
            with self.subTest(stated=stated), self.assertRaises(ValueError):
                BUILDER.page_vocabulary(stated, questions)

    def test_the_word_list_is_sorted_and_carries_the_counts(self):
        counts = {"questions": 3, "realExamQuestions": 2, "occurrences": 5}
        stated = {"zeal": {**self.ENTRY, "word": "zeal", **counts}, "abut": {**self.ENTRY, **counts},
                  "by-virtue-of": {**self.ENTRY, "word": "by virtue of", **counts}}
        rows = BUILDER.site_vocabulary(stated)
        self.assertEqual([row["word"] for row in rows], ["abut", "by virtue of", "zeal"])
        self.assertEqual(rows[0], {"id": "abut", **{key: value for key, value in self.ENTRY.items() if key != "basis"}, **counts})
        self.assertEqual(BUILDER.site_vocabulary(None), [])

    def test_word_list_counts_must_be_whole_and_consistent(self):
        for counts in ({"questions": 3, "realExamQuestions": 2}, {"questions": 3, "realExamQuestions": 4, "occurrences": 5},
                       {"questions": 6, "realExamQuestions": 2, "occurrences": 5}, {"questions": "3", "realExamQuestions": 2, "occurrences": 5},
                       {"questions": -1, "realExamQuestions": -1, "occurrences": 5}):
            with self.subTest(counts=counts), self.assertRaises(ValueError):
                BUILDER.site_vocabulary({"abut": {**self.ENTRY, **counts}})
        with self.assertRaises(ValueError):
            BUILDER.site_vocabulary({"Abut!": {**self.ENTRY, "questions": 1, "realExamQuestions": 1, "occurrences": 1}})

    def test_the_practice_page_embeds_the_explanations(self):
        template = "<script id=\"satPageVocabulary\">__PAGE_VOCABULARY_JSON__</script>"
        page = BUILDER.render_practice_page(template, [], "Title", {}, [], {}, {"abut": {"word": "abut"}})
        self.assertIn('{"abut": {"word": "abut"}}', page.replace('":{', '": {').replace('":"', '": "'))
        self.assertIn("{}", BUILDER.render_practice_page(template, [], "Title", {}, [], {}))


if __name__ == "__main__":
    unittest.main()
