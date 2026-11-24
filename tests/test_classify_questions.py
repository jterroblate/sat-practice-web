"""Every Reading and Writing question has a task and a passage type, and the rules that give them are stable."""

from __future__ import annotations

import copy
import importlib.util
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


def load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CLASSIFIER = load("classify_questions_under_test", "scripts/classify_questions.py")
DB = CLASSIFIER.db
CONVENTIONS = "Which choice completes the text so that it conforms to the conventions of Standard English?"


class TaskRuleTests(unittest.TestCase):
    def task(self, skill, prompt, choices=("a", "b", "c", "d"), answer="a"):
        return CLASSIFIER.task_of(skill, prompt, list(choices), answer)

    def test_stems_decide_the_reading_tasks(self):
        cases = [
            ("Words in Context", "Which choice completes the text with the most logical and precise word or phrase?", "wic_blank"),
            ("Words in Context", 'As used in the text, what does the word "plain" most nearly mean?', "wic_meaning"),
            ("Text Structure and Purpose", "Which choice best states the main purpose of the text?", "tsp_purpose"),
            ("Text Structure and Purpose", "Which choice best describes the overall structure of the text?", "tsp_structure"),
            ("Text Structure and Purpose", "Which choice best describes the function of the underlined sentence in the text as a whole?", "tsp_function"),
            ("Cross-Text Connections", "Based on the texts, how would the author of Text 2 most likely respond to the claim in Text 1?", "ctc_response"),
            ("Cross-Text Connections", "Based on the texts, both authors would most likely agree with which statement?", "ctc_compare"),
            ("Central Ideas and Details", "Which choice best states the main idea of the text?", "cid_main"),
            ("Central Ideas and Details", "According to the text, what is true about the river?", "cid_detail"),
            ("Command of Evidence: Textual", "Which finding, if true, would most directly support the researchers' claim?", "coe_support"),
            ("Command of Evidence: Textual", "Which finding, if true, would most directly weaken the hypothesis?", "coe_weaken"),
            ("Command of Evidence: Textual", "Which quotation from the poem most effectively illustrates the claim?", "coe_illustrate"),
            ("Command of Evidence: Quantitative", "Which choice most effectively uses data from the table to complete the statement?", "coq_table"),
            ("Command of Evidence: Quantitative", "Which choice most effectively uses data from the graph to complete the example?", "coq_graph"),
            ("Inferences", "Which choice most logically completes the text?", "inf_complete"),
            ("Inferences", "What does the text most strongly suggest about the coating?", "inf_suggest"),
        ]
        for skill, prompt, expected in cases:
            self.assertEqual(self.task(skill, prompt), expected, prompt)

    def test_synthesis_tasks_follow_the_stated_goal(self):
        goal = lambda text: self.task("Rhetorical Synthesis", f"The student wants to {text}. Which choice most effectively "
                                      "uses relevant information from the notes to accomplish this goal?")
        self.assertEqual(goal("emphasize a difference between the two rivers"), "rs_compare")
        self.assertEqual(goal("compare the lengths of the two rivers"), "rs_compare")
        self.assertEqual(goal("make and support a generalization about comets"), "rs_claim")
        self.assertEqual(goal("explain why nautical miles are used"), "rs_explain")
        self.assertEqual(goal("describe the approach and explain its significance"), "rs_explain")
        self.assertEqual(goal("emphasize the aim of the research study"), "rs_emphasize")
        self.assertEqual(goal("introduce the novel to a new audience"), "rs_introduce")
        self.assertEqual(goal("present the study and its findings"), "rs_introduce")

    def test_transitions_follow_the_relation_of_the_correct_answer(self):
        relation = lambda answer: self.task("Transitions", "Which choice completes the text with the most logical transition?",
                                            answer=answer)
        expected = {"However,": "tr_contrast", "by contrast,": "tr_contrast", "Therefore,": "tr_cause", "In sum,": "tr_cause",
                    "Moreover,": "tr_addition", "Similarly,": "tr_addition", "For example,": "tr_example",
                    "Specifically,": "tr_example", "In fact,": "tr_emphasis", "In other words,": "tr_emphasis",
                    "Next,": "tr_sequence", "Meanwhile,": "tr_sequence", "Fittingly,": "tr_other",
                    "Undermining this explanation,": "tr_other"}
        for answer, task in expected.items():
            self.assertEqual(relation(answer), task, answer)

    def test_conventions_tasks_are_read_off_the_choices(self):
        cases = [
            (("tombs. Built", "tombs, built", "tombs and built", "tombs built"), "tombs. Built", "bd_period"),
            (("quilts; the", "quilts, the", "quilts the", "quilts. the"), "quilts; the", "bd_semicolon"),
            (("supernova: the", "supernova, the", "supernova the", "supernova. The"), "supernova: the", "bd_colon"),
            (("vibrato—", "vibrato:", "vibrato,", "vibrato"), "vibrato—", "bd_dash"),
            (("content,", "content", "content;", "content:"), "content,", "bd_comma"),
            (("linguist Sandra Chung", "linguist, Sandra Chung", "linguist: Sandra Chung", "linguist, Sandra Chung,"),
             "linguist Sandra Chung", "bd_none"),
            (("feature", "has featured", "featuring", "features"), "feature", "fs_agreement"),
            (("vowed", "is vowing", "will vow", "vows"), "vowed", "fs_tense"),
            (("to give", "gives", "has given", "gave"), "to give", "fs_verbform"),
            (("shows", "showing", "having shown", "to show"), "shows", "fs_verbform"),
            (("it", "those", "these", "them"), "it", "fs_pronoun"),
            (("novels' protagonists", "novel's protagonists", "novels protagonists'", "novels protagonist's"),
             "novels' protagonists", "fs_possessive"),
            (("the researchers theorize that moths mistake lights for the moon",
              "moths continually try to reorient themselves, the researchers theorize",
              "the researchers' theory is that moths continually reorient",
              "mistaking lights for the moon is what the researchers theorize moths do"),
             "the researchers theorize that moths mistake lights for the moon", "fs_modifier"),
        ]
        for choices, answer, expected in cases:
            for skill in CLASSIFIER.CONVENTIONS:
                self.assertEqual(self.task(skill, CONVENTIONS, choices, answer), expected, (skill, choices))

    def test_a_stem_that_belongs_to_one_skill_wins_over_the_recorded_skill(self):
        self.assertEqual(self.task("Central Ideas and Details", "Which choice best states the main purpose of the text?"),
                         "tsp_purpose")
        self.assertEqual(self.task("Command of Evidence: Textual",
                                   "Which finding, if true, would most directly support the claim in the underlined sentence?"),
                         "coe_support")

    def test_every_task_names_a_known_skill_once(self):
        ids = [task for task, _, _, _ in CLASSIFIER.TASKS]
        self.assertEqual(len(ids), len(set(ids)))
        names = [(skill, name) for _, skill, name, _ in CLASSIFIER.TASKS]
        self.assertEqual(len(names), len(set(names)))


class PassageTypeRuleTests(unittest.TestCase):
    def test_a_passage_that_names_its_kind_is_literature(self):
        kind = CLASSIFIER.literary_type
        self.assertEqual(kind("The following text is adapted from an 1899 novel. A traveller arrives."), "fiction")
        self.assertEqual(kind('The following text is from a 1912 poem "Summer." It is summer.'), "poetry")
        self.assertEqual(kind("Text 1 is a 1912 poem. Text 2 is from a 1913 poem."), "poetry")
        self.assertEqual(kind('"Morning" is a 1904 play by an Irish writer. In the play, ______'), "drama")
        self.assertEqual(kind("The following text is from Yung Wing's 1909 memoir My Life in China and America."), "nonfiction")
        self.assertEqual(kind("The following text is from a 2010 book about the novel as a form."), "nonfiction")
        self.assertEqual(kind("A critic's 1957 novel is regularly described as autobiography."), None)
        self.assertEqual(kind("Researchers studied how poems are memorised."), None)

    def test_word_lists_decide_only_with_a_clear_margin(self):
        science = ("Biologist Ana Ruiz studied how bacteria in the soil help plants take up nitrogen. "
                   "Her team sequenced the genomes of forty species.")
        self.assertEqual(CLASSIFIER.passage_type_of(science), ("science", "word_list"))
        social = "Economists surveyed voters about tax policy before the election and compared wages across cities."
        self.assertEqual(CLASSIFIER.passage_type_of(social), ("social", "word_list"))
        arts = "The painter's murals, shown at the museum with her sculptures and photographs, drew critics' praise."
        self.assertEqual(CLASSIFIER.passage_type_of(arts), ("humanities", "word_list"))
        self.assertEqual(CLASSIFIER.passage_type_of("A report that the limit could not be passed was later disproved."),
                         (None, None))
        mixed = "The historian wrote about a painter."
        self.assertEqual(CLASSIFIER.passage_type_of(mixed), (None, None))

    def test_the_same_text_has_one_key_however_it_was_typeset(self):
        key = CLASSIFIER.passage_key
        self.assertEqual(key("The <i>harbor</i> “tide” — rises."), key('the [i]harbor[/i] "tide" - rises'))
        self.assertNotEqual(key("The harbor tide rises."), key("The harbor tide falls."))


class CommittedClassificationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tables, problems = DB.read_tables(ROOT)
        assert problems == [], problems[:3]
        cls.connection = DB.load_sqlite(cls.tables, ROOT)

    @classmethod
    def tearDownClass(cls):
        cls.connection.close()

    def count(self, sql):
        return self.connection.execute(sql).fetchone()[0]

    def test_no_reading_and_writing_question_is_left_out(self):
        self.assertEqual(self.count(
            "SELECT count(*) FROM practice_pool WHERE section = 'reading_writing'"
            "   AND (task_id IS NULL OR passage_type IS NULL)"), 0)
        self.assertEqual(self.count(
            "SELECT count(*) FROM practice_pool WHERE section = 'math'"
            "   AND (task_id IS NOT NULL OR passage_type IS NOT NULL)"), 0)
        self.assertEqual(self.count("SELECT count(*) FROM practice_pool WHERE importance IS NULL"), 0)
        self.assertEqual(self.count("SELECT count(*) FROM practice_pool"), len(self.tables["question"]))

    def test_the_classifier_has_nothing_left_to_change(self):
        again = copy.deepcopy(self.tables)
        outcome = CLASSIFIER.classify(again)
        self.assertEqual(outcome["changed"], 0)
        self.assertEqual(dict(outcome["pending"]), {})
        self.assertEqual(sorted(again["task"], key=lambda row: row["task_id"]), self.tables["task"])
        self.assertEqual(sorted(again["passage_type"], key=lambda row: row["passage_type"]), self.tables["passage_type"])

    def test_reviewed_rows_are_kept_as_they_are(self):
        again = copy.deepcopy(self.tables)
        row = next(item for item in again["question"] if item["passage_type_basis"] == "reviewed")
        before = (row["passage_type"], row["passage_type_basis"])
        CLASSIFIER.classify(again)
        self.assertEqual((row["passage_type"], row["passage_type_basis"]), before)

    def test_the_reference_tables_are_the_ones_the_classifier_defines(self):
        passage_types, tasks = CLASSIFIER.reference_rows(self.tables)
        self.assertEqual(self.tables["passage_type"], sorted(passage_types, key=lambda row: row["passage_type"]))
        self.assertEqual(self.tables["task"], sorted(tasks, key=lambda row: row["task_id"]))
        used = {row["task_id"] for row in self.tables["question"] if row["task_id"]}
        self.assertEqual(used, {row["task_id"] for row in tasks}, "every task is used and every used task is defined")

    def test_skill_conflicts_are_few_and_the_pool_follows_the_task(self):
        conflicts = self.count("SELECT count(*) FROM question_skill_conflict")
        self.assertLess(conflicts, len(self.tables["question"]) * 0.01)
        self.assertEqual(self.count(
            "SELECT count(*) FROM practice_pool p JOIN task k USING (task_id) WHERE k.skill_id <> p.skill_id"), 0)

    def test_importance_follows_where_a_question_has_appeared(self):
        rows = self.connection.execute(
            "SELECT i.importance, i.sittings, i.latest_year, o.tier FROM question_importance i"
            "  LEFT JOIN question_origin o USING (question_id)").fetchall()
        latest = self.count("SELECT max(s.year) FROM source s JOIN source_family f USING (family_id) WHERE f.tier = 1")
        seen = set()
        for importance, sittings, year, tier in rows:
            seen.add(importance)
            if importance == "basic":
                self.assertNotEqual(tier, 1)
            else:
                self.assertEqual(tier, 1)
                self.assertEqual(importance == "core", sittings >= 2 or year == latest)
        self.assertEqual(seen, {"core", "important", "basic"})

    def test_task_frequency_adds_up_over_real_exam_questions(self):
        total = self.count("SELECT sum(questions) FROM task_frequency")
        self.assertEqual(total, self.count(
            "SELECT count(*) FROM question q JOIN question_origin o USING (question_id)"
            " WHERE o.tier = 1 AND q.task_id IS NOT NULL"))
        self.assertAlmostEqual(self.count("SELECT sum(percent) FROM task_frequency"), 100, delta=0.5)


if __name__ == "__main__":
    unittest.main()
