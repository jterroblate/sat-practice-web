from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "tag_question_types.py"
SPEC = importlib.util.spec_from_file_location("sat_tag_question_types_under_test", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def question(prompt: str, passage: str = "Evidence.", choices: list[str] | None = None) -> dict:
    return {
        "question_id": "q",
        "prompt": prompt,
        "passage": passage,
        "choices": [{"label": label, "text": text} for label, text in zip("ABCD", choices or ["A", "B", "C", "D"])],
    }


class ExpandedClassificationRulesTests(unittest.TestCase):
    def skill(self, value: dict) -> str:
        result = MODULE.rule_based_classify(value)
        self.assertEqual(result["confidence"], "high")
        self.assertFalse(result["needs_review"])
        return result["skill"]

    def test_common_stem_variants(self) -> None:
        self.assertEqual(self.skill(question("As used in the text, what does the word “clear” most nearly mean?")), "Words in Context")
        self.assertEqual(self.skill(question("Which choice best describes the overall structure of the text?")), "Text Structure and Purpose")
        self.assertEqual(self.skill(question("What does the text most strongly imply about the result?")), "Inferences")
        self.assertEqual(self.skill(question("Information in the text best supports which statement?")), "Command of Evidence: Textual")
        self.assertEqual(
            self.skill(question("Which choice most effectively uses information from the given sentences to emphasize a difference?")),
            "Rhetorical Synthesis",
        )

    def test_cross_text_precedes_broad_based_on_text_rule(self) -> None:
        value = question(
            "Based on the texts, the author of Text 2 would most like to know what about Text 1?",
            "Text 1\n\nFirst view.\n\nText 2\n\nSecond view.",
        )
        self.assertEqual(self.skill(value), "Cross-Text Connections")


if __name__ == "__main__":
    unittest.main()
