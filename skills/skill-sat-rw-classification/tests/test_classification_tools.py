from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest


SKILL = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, SKILL / "scripts" / (name + ".py"))
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


VALIDATOR = load("validate_classification")
REPORTER = load("generate_classification_report")
MERGER = load("merge_classification_results")
TAXONOMY = VALIDATOR.load_taxonomy(SKILL / "config/sat_rw_taxonomy.json")


class ClassificationToolTests(unittest.TestCase):
    def test_possessive_options_reject_a_mistaken_transition_tag(self):
        question = {
            "question_id": "demo_possessive",
            "choices": [{"label": label, "text": text} for label, text in zip("ABCD", [
                "artist's painted blue labels", "artists' painted blue labels",
                "artists painted blue label's", "artists painted blue labels",
            ])],
        }
        self.assertIsNotNone(VALIDATOR.option_shape_mismatch(question, "Transitions"))
        self.assertIsNone(VALIDATOR.option_shape_mismatch(question, "Form, Structure, and Sense"))

    def test_conflicting_review_is_not_silently_adopted(self):
        question = {"question_id": "demo_conflict", "classification": {
            "domain": "Information and Ideas", "skill": "Inferences", "confidence": "high", "evidence": {},
        }}
        result = MERGER.merge([question], [{"task_id": "demo_conflict", "skill": "Words in Context", "confidence": "high"}])
        classification = result[0]["classification"]
        self.assertEqual(classification["skill"], "Inferences")
        self.assertEqual(classification["model_agreement"], "conflict")
        self.assertTrue(classification["needs_review"])

    def test_imported_markup_remains_text_in_the_classification_report(self):
        raw = '<script>alert("fixture")</script>'
        question = {"question_id": "demo_markup", "prompt": raw, "classification": {
            "domain": "Information and Ideas", "skill": "Inferences", "confidence": "high",
            "evidence": {"stem_signal": raw},
        }}
        html = REPORTER.build_html([question], {"qa_passed": True})
        self.assertNotIn(raw, html)
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("&quot;fixture&quot;", html)


if __name__ == "__main__":
    unittest.main()
