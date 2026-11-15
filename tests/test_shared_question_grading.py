"""A shared public question ID must have one scoring revision everywhere."""

from __future__ import annotations

import copy
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills/skill-sat-rw-classification/scripts/build_learning_experience.py"
SPEC = importlib.util.spec_from_file_location("shared_grading_builder", SCRIPT)
assert SPEC and SPEC.loader
BUILDER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILDER)


def question():
    return {
        "question_id": "demo_shared_revision", "section": "reading_writing",
        "passage": "An original development statement.", "prompt": "Which choice fits?",
        "choices": [{"label": label, "text": text}
                    for label, text in zip("ABCD", ("first", "second", "third", "fourth"))],
        "correct_answer": "A",
        "explanation": {"short": "The first choice fits.", "key_evidence": "Original statement.",
                        "why_correct": "The first choice matches the evidence.",
                        "detailed": "The first choice is supported by the original statement.",
                        "choice_analysis": {label: "Development explanation " + label for label in "ABCD"}},
    }


class SharedQuestionGradingTests(unittest.TestCase):
    def load(self, first, second):
        entries = [{"kind": "set", "status": "published", "route": route}
                   for route in ("sets/demo-first.html", "sets/demo-second.html")]
        questions = {entries[0]["route"]: first, entries[1]["route"]: second}
        with patch.object(BUILDER, "load_question_package",
                          side_effect=lambda root, entry: [copy.deepcopy(questions[entry["route"]])]):
            return BUILDER.load_published_content(ROOT, {"entries": entries})

    def test_one_sided_explanation_edit_names_id_and_both_routes(self):
        first, second = question(), question()
        second["explanation"]["detailed"] += " Additional reviewed detail."
        with self.assertRaises(ValueError) as caught:
            self.load(first, second)
        message = str(caught.exception)
        for value in (first["question_id"], "sets/demo-first.html", "sets/demo-second.html"):
            self.assertIn(value, message)

    def test_synchronized_explanation_edits_are_accepted(self):
        first = question()
        first["explanation"]["detailed"] += " Additional reviewed detail."
        second = copy.deepcopy(first)
        first["question_number"], second["question_number"] = 1, 8
        _, pages, canonical = self.load(first, second)
        revisions = {BUILDER.grading_revision(questions[0]) for questions in pages.values()}
        self.assertEqual(len(revisions), 1)
        self.assertIn(first["question_id"], canonical)

    def test_one_sided_answer_change_is_rejected(self):
        first, second = question(), question()
        second["correct_answer"] = "B"
        with self.assertRaisesRegex(ValueError, "Shared question grading fields differ"):
            self.load(first, second)


if __name__ == "__main__":
    unittest.main()
