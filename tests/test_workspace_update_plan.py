"""Public edits may not silently replace richer private content."""
import importlib.util
from pathlib import Path
import unittest

SPEC = importlib.util.spec_from_file_location("workspace_plan", Path(__file__).resolve().parents[1] / "scripts/prepare-workspace-update.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class WorkspacePlanTests(unittest.TestCase):
    def test_private_preimage_conflict_requires_manual_review(self):
        changes = MODULE.plan_question_changes({"prompt": "baseline"}, {"prompt": "proposed"}, {"prompt": "new private correction"})
        self.assertEqual(changes[0]["status"], "manual-review-required")
        self.assertFalse(changes[0]["preimageMatches"])

    def test_only_changed_fields_are_proposed(self):
        changes = MODULE.plan_question_changes({"prompt": "old"}, {"prompt": "new"}, {"prompt": "old", "source_file": "private evidence"})
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["status"], "ready-for-review")
        self.assertEqual(changes[0]["proposedValue"], "new")
        self.assertNotIn("source_file", str(changes))

    def test_identity_change_requires_manual_review(self):
        changes = MODULE.plan_question_changes({"question_id": "stable"}, {"question_id": "new"}, {"question_id": "stable"})
        self.assertEqual(changes[0]["status"], "manual-review-required")


if __name__ == "__main__":
    unittest.main()
