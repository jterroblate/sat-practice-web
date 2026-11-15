from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/sat_runtime_metadata.py"
SPEC = importlib.util.spec_from_file_location("sat_runtime_metadata", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class RuntimeMetadataTests(unittest.TestCase):
    def test_edited_answers_invalidate_old_grading_revision(self):
        question = {"question_id": "demo_revision", "passage": "Original text.", "prompt": "Choose.",
                    "choices": [{"label": "A", "text": "First"}, {"label": "B", "text": "Second"}],
                    "correct_answer": "A", "explanation": {"key_evidence": "First."}}
        before = MODULE.grading_revision(question)
        question["correct_answer"] = "B"
        self.assertNotEqual(before, MODULE.grading_revision(question))

    def test_template_cannot_downgrade_owner_scoped_runtime_to_legacy(self):
        runtime = 'function createSatLearningStore() {} "sat-owner:v1:" const learningStorage = createSatLearningStore();'
        self.assertTrue(MODULE.runtime_metadata_present({}, {}, [runtime]))
        self.assertFalse(MODULE.runtime_metadata_present({}, {}, ["plain legacy page"]))

    def test_canonical_registry_cannot_read_outside_the_candidate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry = {"entries": [{"kind": "set", "status": "published", "route": "sets/demo.html",
                                     "dataPath": "../private.json"}]}
            with self.assertRaisesRegex(ValueError, "Unsafe canonical"):
                MODULE.canonical_revision_contract(root, registry)


if __name__ == "__main__":
    unittest.main()
