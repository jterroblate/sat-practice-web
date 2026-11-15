"""Plans cover fixed committed changes and cannot write into production."""
from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("workspace_plan", Path(__file__).resolve().parents[1] / "scripts/prepare-workspace-update.py")
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
PACKAGE = "content/canonical/sets/demo.json"
CORE = "skills/skill-sat-rw-classification/scripts/build_learning_experience.py"


class WorkspaceFieldTests(unittest.TestCase):
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

    def test_null_and_absent_private_preimages_are_distinct(self):
        changes = MODULE.plan_question_changes({"prompt": None}, {"prompt": "new"}, {})
        self.assertFalse(changes[0]["preimageMatches"])
        self.assertTrue(changes[0]["baselinePresent"])
        self.assertFalse(changes[0]["privatePresent"])


@unittest.skipUnless(shutil.which("git"), "Git is required for committed-snapshot tests")
class WorkspacePlanIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="sat-workspace-plan-")
        self.top = Path(self.temporary.name).resolve()
        self.root = self.top / "public"
        self.private = self.top / "private"
        self.root.mkdir()
        self.private.mkdir()
        self.git("init", "-q")
        self.write(".gitignore", "/build/\n")
        self.write("README.md", "fixture readme\n")
        for relative in MODULE.SOURCE_PATHS:
            self.write(relative, "baseline shared source\n")
        self.write("scripts/sat_runtime_metadata.py", "baseline additional source\n")
        self.write("scripts/retired_tool.py", "retired source\n")
        self.write_json("sat-content-registry.json", {"entries": []})
        self.write_json("sat-practice-migrations.json", {})
        self.write_json("assets/content-resources.json", {"resources": []})
        self.write("assets/question-media/old.png", "original fixture bytes")
        self.write_json(PACKAGE, {"title": "baseline title", "questions": [
            {"question_id": "demo_a", "prompt": "first"},
            {"question_id": "demo_b", "prompt": "second"},
        ]})
        self.base = self.commit("baseline")
        self.git("tag", "initial-import")
        for relative in self.git("ls-files", "-z").split("\0"):
            if relative:
                target = self.private / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(self.root / relative, target)
        self.private_before = self.snapshot(self.private)

    def tearDown(self):
        try:
            self.assertEqual(self.snapshot(self.private), self.private_before,
                             "A review plan must leave every production fixture byte unchanged")
        finally:
            self.temporary.cleanup()

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.root), *args], check=True,
                              capture_output=True, text=True).stdout.strip("\n")

    def commit(self, message):
        self.git("add", "-A")
        self.git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.com", "commit", "-qm", message)
        return self.git("rev-parse", "HEAD").strip()

    def write(self, relative, text):
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        return target

    def write_json(self, relative, value):
        return self.write(relative, json.dumps(value))

    @staticmethod
    def snapshot(root):
        return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in root.rglob("*") if p.is_file()}

    def run_plan(self, *extra, workspace=None):
        arguments = ["prepare-workspace-update.py", "--workspace", str(workspace or self.private), *extra]
        with patch.object(MODULE, "ROOT", self.root), patch.object(sys, "argv", arguments), contextlib.redirect_stdout(io.StringIO()) as captured:
            status = MODULE.main()
        return status, captured.getvalue()

    def report(self):
        return json.loads((self.root / "build/workspace-update.json").read_text())

    def test_committed_core_and_question_changes_bind_exact_head(self):
        self.write(CORE, "changed committed source\n")
        package = json.loads((self.root / PACKAGE).read_text())
        package["questions"][0]["prompt"] = "reviewed proposed prompt"
        self.write_json(PACKAGE, package)
        head = self.commit("reviewed change")
        status, _ = self.run_plan()
        self.assertEqual(status, 0)
        report = self.report()
        self.assertEqual(report["headCommit"], head)
        self.assertEqual(report["baseCommit"], self.base)
        self.assertEqual(report["snapshotMode"], "clean-committed-head")
        self.assertEqual(report["coverage"]["changedFiles"], 2)
        self.assertEqual(report["coverage"]["unclassifiedFiles"], 0)
        self.assertEqual(report["sourceChanges"][0]["status"], "ready-for-review")
        self.assertEqual(report["questionChanges"][0]["changes"][0]["proposedValue"], "reviewed proposed prompt")
        self.assertFalse(report["writesToProduction"])

    def test_new_deleted_renamed_and_previously_unlisted_sources_are_all_explicit(self):
        self.write("scripts/sat_runtime_metadata.py", "edited metadata source\n")
        self.write("scripts/new_helper.py", "new required dependency\n")
        (self.root / "scripts/retired_tool.py").unlink()
        old = "skills/skill-sat-rw-classification/assets/templates/html_template_v1.1.html"
        new = "skills/skill-sat-rw-classification/assets/templates/renamed.html"
        (self.root / old).rename(self.root / new)
        self.commit("source additions deletions and rename")
        self.assertEqual(self.run_plan()[0], 0)
        report = self.report()
        expected = {"scripts/sat_runtime_metadata.py", "scripts/new_helper.py", "scripts/retired_tool.py", old, new}
        self.assertEqual({item["path"] for item in report["changedFiles"]}, expected)
        self.assertEqual({item["path"] for item in report["sourceChanges"]}, expected)
        self.assertTrue(all(item["status"] == "manual-review-required" for item in report["sourceChanges"]))

    def test_protocol_manifest_asset_and_other_file_changes_are_not_omitted(self):
        for relative in MODULE.PROTOCOL_PATHS:
            self.write_json(relative, {"reviewed": True})
        (self.root / "assets/question-media/old.png").unlink()
        self.write("assets/question-media/new.png", "new necessary asset\n")
        self.write("docs/new-guide.md", "review this new document\n")
        self.commit("protocol and resource changes")
        self.assertEqual(self.run_plan()[0], 0)
        report = self.report()
        self.assertEqual({item["path"] for item in report["protocolChanges"]}, MODULE.PROTOCOL_PATHS)
        self.assertEqual({item["operation"] for item in report["assetChangesForManualReview"]}, {"A", "D"})
        self.assertEqual(report["otherFilesForManualReview"][0]["path"], "docs/new-guide.md")
        self.assertEqual(len(report["changedFiles"]), 6)

    def test_package_metadata_and_order_only_changes_are_reported(self):
        package = json.loads((self.root / PACKAGE).read_text())
        package["title"] = "reviewed package title"
        package["questions"].reverse()
        self.write_json(PACKAGE, package)
        self.commit("metadata and order")
        self.assertEqual(self.run_plan()[0], 0)
        report = self.report()
        self.assertEqual(report["packageMetadataChanges"][0]["changes"][0]["field"], "title")
        self.assertEqual(report["questionOrderChanges"][0]["proposedQuestionIds"], ["demo_b", "demo_a"])
        self.assertEqual(report["questionChanges"], [])

    def test_package_addition_deletion_and_question_membership_are_explicit(self):
        (self.root / PACKAGE).unlink()
        new = "content/canonical/sets/new.json"
        self.write_json(new, {"questions": [{"question_id": "demo_new", "prompt": "new example"}]})
        self.commit("package membership")
        self.assertEqual(self.run_plan()[0], 0)
        report = self.report()
        self.assertEqual(report["removedPackagesForManualReview"], [PACKAGE])
        statuses = {item.get("status") for item in report["questionChanges"]}
        self.assertTrue({"new-package-manual-review-required", "removed-package-manual-review-required",
                         "question-membership-manual-review-required"} <= statuses)

    def test_noneditable_question_fields_require_manual_review(self):
        package = json.loads((self.root / PACKAGE).read_text())
        package["questions"][0]["unexpected_metadata"] = "review this field"
        self.write_json(PACKAGE, package)
        self.commit("noneditable field")
        self.assertEqual(self.run_plan()[0], 0)
        change = self.report()["questionChanges"][0]["changes"][0]
        self.assertFalse(change["editableField"])
        self.assertEqual(change["status"], "manual-review-required")

    def test_dirty_tracked_or_staged_changes_are_rejected(self):
        self.write(CORE, "uncommitted tracked change\n")
        self.assertEqual(self.run_plan()[0], 2)
        self.git("add", CORE)
        self.assertEqual(self.run_plan()[0], 2)
        self.assertFalse((self.root / "build/workspace-update.json").exists())

    def test_nonignored_untracked_source_and_asset_are_rejected(self):
        self.write("scripts/untracked_helper.py", "untracked\n")
        self.write("assets/question-media/untracked.png", "untracked asset\n")
        self.assertEqual(self.run_plan()[0], 2)
        self.assertFalse((self.root / "build/workspace-update.json").exists())

    def test_ignored_build_outputs_do_not_prevent_a_zero_change_plan(self):
        self.write("build/local-preview.txt", "ignored build output\n")
        self.assertEqual(self.run_plan()[0], 0)
        self.assertEqual(self.report()["changedFiles"], [])

    def test_output_outside_real_build_is_rejected(self):
        original = (self.root / "README.md").read_bytes()
        self.assertEqual(self.run_plan("--output", str(self.root / "README.md"))[0], 2)
        self.assertEqual((self.root / "README.md").read_bytes(), original)

    def test_build_symlink_cannot_overwrite_production_canonical(self):
        (self.root / "build").symlink_to(self.private, target_is_directory=True)
        status, _ = self.run_plan("--output", str(self.root / "build" / PACKAGE))
        self.assertEqual(status, 2)

    def test_symlink_output_or_parent_cannot_overwrite_production_canonical(self):
        build = self.root / "build"
        build.mkdir()
        final = build / "review.json"
        final.symlink_to(self.private / PACKAGE)
        self.assertEqual(self.run_plan("--output", str(final))[0], 2)
        final.unlink()
        parent = build / "nested"
        parent.symlink_to(self.private, target_is_directory=True)
        self.assertEqual(self.run_plan("--output", str(parent / PACKAGE))[0], 2)

    def test_repository_and_workspace_may_not_overlap_in_either_direction(self):
        self.assertEqual(self.run_plan(workspace=self.root)[0], 2)
        self.assertEqual(self.run_plan(workspace=self.top)[0], 2)
        child = self.root / "build/private"
        child.mkdir(parents=True)
        (child / "sat-content-registry.json").write_text("{}")
        self.assertEqual(self.run_plan(workspace=child)[0], 2)

    def test_write_rechecks_a_symlink_inserted_after_planning(self):
        report = MODULE.build_plan(self.root, self.private, "initial-import")
        (self.root / "build").symlink_to(self.private, target_is_directory=True)
        with self.assertRaises(ValueError):
            MODULE.write_plan(self.root, self.private, self.root / "build" / PACKAGE, report)

    def test_write_rejects_a_head_change_after_planning(self):
        report = MODULE.build_plan(self.root, self.private, "initial-import")
        self.write("README.md", "new committed state\n")
        self.commit("later state")
        with self.assertRaises(ValueError):
            MODULE.write_plan(self.root, self.private, self.root / "build/workspace-update.json", report)
        self.assertFalse((self.root / "build/workspace-update.json").exists())

    @unittest.skipUnless(hasattr(os, "link"), "Hard links are not available")
    def test_atomic_output_replacement_preserves_a_hardlinked_private_file(self):
        build = self.root / "build"
        build.mkdir()
        output = build / "review.json"
        os.link(self.private / PACKAGE, output)
        self.assertEqual(self.run_plan("--output", str(output))[0], 0)
        self.assertEqual(json.loads(output.read_text())["schemaVersion"], "sat-workspace-review-plan-v2")


if __name__ == "__main__":
    unittest.main()
