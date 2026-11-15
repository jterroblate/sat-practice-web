import base64
import hashlib
import importlib.util
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build_learning_experience.py"
SPEC = importlib.util.spec_from_file_location("sat_build_learning_experience", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


class PublicMarkupTests(unittest.TestCase):
    def test_browser_title_uses_registry_display_name_exactly(self):
        template = (
            Path(__file__).resolve().parents[1]
            / "assets"
            / "templates"
            / "html_template_v1.1.html"
        ).read_text(encoding="utf-8")
        self.assertIn("<title>__SECTION_TITLE__</title>", template)
        self.assertNotIn("<title>__SECTION_TITLE__ Practice</title>", template)

    def test_allowed_inline_html_becomes_inert_markers(self):
        value = "A <i>title</i>, <em>term</em>, <u>line</u>, and <strong>claim</strong>."
        self.assertEqual(
            MODULE.normalize_public_inline_markup(value, "fixture"),
            "A [i]title[/i], [i]term[/i], [u]line[/u], and [b]claim[/b].",
        )

    def test_unsupported_or_attributed_html_fails_closed(self):
        for value in (
            '<i onclick="alert(1)">title</i>',
            "<table><tr><td>1</td></tr></table>",
            "<script>alert(1)</script>",
            "<!-- hidden -->",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                MODULE.normalize_public_inline_markup(value, "fixture")

    def test_unbalanced_markers_fail_closed(self):
        for value in ("[i]open", "[/u]close", "[i][u]bad[/i][/u]"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                MODULE.normalize_public_inline_markup(value, "fixture")

    def test_marker_spelling_is_canonicalized(self):
        self.assertEqual(
            MODULE.normalize_public_inline_markup("[ I ]title[/ I ]", "fixture"),
            "[i]title[/i]",
        )

    def test_plain_mathematical_comparison_is_preserved(self):
        self.assertEqual(
            MODULE.normalize_public_inline_markup("For 2 < 3, choose A.", "fixture"),
            "For 2 < 3, choose A.",
        )

    def test_student_dtos_normalize_question_and_explanation_text(self):
        question = {
            "question_id": "fixture-q1",
            "section": "reading_writing",
            "question_number": 1,
            "passage": "Read <i>this</i> ______.",
            "prompt": "What is <u>best</u>?",
            "choices": [{"label": "A", "text": "A <b>choice</b>"}],
            "correct_answer": "A",
            "answer_source": "independent_agent_consensus_verified",
            "answer_confidence": "high",
            "needs_manual_review": False,
            "explanation": {
                "key_evidence": "The <em>evidence</em> is explicit.",
                "choice_analysis": {"A": "It is <strong>precise</strong>."},
                "answer_verification_rationale": "reviewer_1, reviewer_2, reviewer_3 agree.",
            },
            "classification": {
                "skill": "Words in Context",
                "confidence": "low",
                "needs_review": True,
            },
        }
        fingerprint_before = MODULE.content_fingerprint(question)
        public = MODULE.public_question(Path("."), "sets/fixture.html", question)
        compact = MODULE.compact_question(question, "sets/fixture.html")
        self.assertEqual(MODULE.content_fingerprint(question), fingerprint_before)
        self.assertEqual(public["passage"], "Read [i]this[/i] ______.")
        self.assertEqual(public["prompt"], "What is [u]best[/u]?")
        self.assertEqual(public["choices"][0]["text"], "A [b]choice[/b]")
        self.assertEqual(public["explanation"]["key_evidence"], "The [i]evidence[/i] is explicit.")
        self.assertNotIn("answer_verification_rationale", public["explanation"])
        self.assertNotIn("answer_source", public)
        self.assertNotIn("answer_confidence", public)
        self.assertNotIn("needs_manual_review", public)
        self.assertNotIn("confidence", public["classification"])
        self.assertNotIn("needs_review", public["classification"])
        self.assertEqual(
            public["explanation"]["choice_analysis"]["A"],
            "It is [b]precise[/b].",
        )
        self.assertEqual(compact["passage"], "Read [i]this[/i] ______.")

    def test_bound_material_image_is_hash_checked_and_externalized(self):
        payload = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
        )
        digest = hashlib.sha256(payload).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "runs" / "chart.png"
            source.parent.mkdir(parents=True)
            source.write_bytes(payload)
            question = {
                "question_id": "fixture-chart",
                "section": "reading_writing",
                "question_number": 1,
                "passage": '<img class="q_mat_img_here" src="runs/chart.png">\nChart passage.',
                "prompt": "What does the graph show?",
                "choices": [{"label": "A", "text": "A"}],
                "correct_answer": "A",
                "explanation": {},
                "classification": {"skill": "Command of Evidence: Quantitative"},
                "public_visual_alt_text": "Average ratings by condition.",
                "visual_assets": [{
                    "type": "chart",
                    "path": "runs/chart.png",
                    "sha256": f"sha256:{digest}",
                    "width": 1,
                    "height": 1,
                }],
            }
            public = MODULE.public_question(root, "sets/fixture.html", question)
            self.assertEqual(public["passage"], "Chart passage.")
            self.assertEqual(public["visual_assets"], [{
                "type": "graph",
                "alt_text": "Average ratings by condition.",
                "image_data_uri": f"../assets/question-media/{digest}.png",
                "width": 1,
                "height": 1,
            }])
            self.assertEqual(
                (root / "assets" / "question-media" / f"{digest}.png").read_bytes(),
                payload,
            )
            self.assertNotIn("runs/chart.png", str(public))
            self.assertNotIn("sha256", str(public))

    def test_bound_material_image_mismatch_fails_closed(self):
        payload = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "runs" / "chart.png"
            source.parent.mkdir(parents=True)
            source.write_bytes(payload)
            question = {
                "question_id": "fixture-chart",
                "section": "reading_writing",
                "question_number": 1,
                "passage": '<img class="q_mat_img_here" src="runs/chart.png">',
                "prompt": "Prompt",
                "choices": [{"label": "A", "text": "A"}],
                "correct_answer": "A",
                "explanation": {},
                "classification": {},
                "public_visual_alt_text": "Chart.",
                "visual_assets": [{
                    "path": "runs/chart.png",
                    "sha256": "sha256:" + "0" * 64,
                    "width": 1,
                    "height": 1,
                }],
            }
            with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                MODULE.public_question(root, "sets/fixture.html", question)

    def test_unbound_source_evidence_asset_is_not_public(self):
        question = {
            "question_id": "fixture-unbound-chart",
            "section": "reading_writing",
            "question_number": 1,
            "passage": "A passage that forgot its image placeholder.",
            "prompt": "Prompt",
            "choices": [{"label": "A", "text": "A"}],
            "correct_answer": "A",
            "explanation": {},
            "classification": {},
            "visual_assets": [{
                "path": "runs/chart.png",
                "sha256": "sha256:" + "0" * 64,
                "width": 1,
                "height": 1,
            }],
        }
        public = MODULE.public_question(Path("."), "sets/fixture.html", question)
        compact = MODULE.compact_question(question, "sets/fixture.html")
        self.assertNotIn("visual_assets", public)
        self.assertNotIn("runs/chart.png", str(public))
        self.assertNotIn("runs/chart.png", str(compact))


if __name__ == "__main__":
    unittest.main()
