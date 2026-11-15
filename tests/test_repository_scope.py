from __future__ import annotations

import importlib.util
import hashlib
import json
from pathlib import Path
import sys
import shutil
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/check-repository-scope.py"
SPEC = importlib.util.spec_from_file_location("sat_repository_scope", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)
PNG_FIXTURE = b"\x89PNG\r\n\x1a\noriginal fixture chart"


def write_resources(root, images):
    manifest = root / "assets/content-resources.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps({"resources": [{
        "publicPath": image.relative_to(root).as_posix(),
        "sizeBytes": image.stat().st_size,
        "sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
    } for image in images]}))


class RepositoryScopeTests(unittest.TestCase):
    def test_authorized_structured_content_and_required_images_are_allowed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = root / "content/canonical/sets/original.json"
            data.parent.mkdir(parents=True)
            data.write_text(json.dumps({"questions": [{"question_id": "demo_scope", "passage": "Original example."}]}))
            image = root / "assets/question-media/fixture.png"
            image.parent.mkdir(parents=True)
            image.write_bytes(PNG_FIXTURE)
            write_resources(root, [image])
            self.assertTrue(MODULE.check_scope(root)["passed"])

    def test_sensitive_values_are_rejected_without_printing_the_values(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "content/canonical/sets/original.json"
            source.parent.mkdir(parents=True)
            secret = "ghp_" + "A" * 36
            contact = "private-person" + "@" + "private-domain.org"
            personal_path = "/Users/" + "private-person" + "/Documents/private"
            source.write_text(json.dumps({"secret": secret, "note": contact, "source": personal_path}))
            result = MODULE.check_scope(root)
            categories = {item["category"] for item in result["findings"]}
            self.assertTrue({"possible-secret", "contact-email", "personal-absolute-path"} <= categories)
            encoded = json.dumps(result)
            for value in (secret, contact, personal_path):
                self.assertNotIn(value, encoded)

    def test_history_documents_screenshots_and_large_files_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative in ("runs/old/report.json", "content/source.pdf", "docs/screenshot.png", "content/too-large.json"):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"{}" * 40)
            result = MODULE.check_scope(root, max_bytes=40)
            categories = {item["category"] for item in result["findings"]}
            self.assertTrue({"raw-history-or-private-tree", "excluded-file-type", "image-outside-essential-assets", "large-file"} <= categories)

    def test_synthetic_user_paths_are_only_allowed_in_tests(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative in ("tests/fixture.py", "scripts/tool.py"):
                path = root / relative
                path.parent.mkdir(parents=True)
                path.write_text("source = '/Users/reviewer/Documents/example'\n")
            result = MODULE.check_scope(root)
            paths = {item["path"] for item in result["findings"] if item["category"] == "personal-absolute-path"}
            self.assertEqual(paths, {"scripts/tool.py"})

    def test_local_build_is_excluded_and_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build = root / "build/runs/example.pdf"
            build.parent.mkdir(parents=True)
            build.write_text("ignored local output")
            link = root / "content/link.json"
            link.parent.mkdir(parents=True)
            link.symlink_to(build)
            result = MODULE.check_scope(root)
            self.assertEqual(result["checkedFiles"], 1)
            self.assertEqual(result["findings"][0]["category"], "symlink-not-allowed")

    def test_legacy_chart_directory_requires_an_exact_current_manifest_binding(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            chart = root / "categories/chart_images/demo-chart.png"
            chart.parent.mkdir(parents=True)
            chart.write_bytes(PNG_FIXTURE)
            write_resources(root, [chart])
            self.assertTrue(MODULE.check_scope(root)["passed"])
            screenshot = chart.with_name("unrelated-screenshot.png")
            screenshot.write_bytes(b"not an approved chart")
            result = MODULE.check_scope(root)
            self.assertTrue(any(item["path"].endswith("unrelated-screenshot.png")
                                and item["category"] == "image-outside-essential-assets"
                                for item in result["findings"]))
            screenshot.unlink()
            chart.write_bytes(b"modified bytes")
            result = MODULE.check_scope(root)
            self.assertTrue(any(item["category"] == "essential-image-manifest-mismatch"
                                for item in result["findings"]))

    def test_future_necessary_image_uses_current_manifest_without_changing_import_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            baseline = root / "docs/content-export.json"
            baseline.parent.mkdir(parents=True)
            baseline.write_text(json.dumps({"resources": []}))
            before = baseline.read_bytes()
            image = root / "assets/question-media/future-chart.png"
            image.parent.mkdir(parents=True)
            image.write_bytes(PNG_FIXTURE)
            self.assertFalse(MODULE.check_scope(root)["passed"])
            write_resources(root, [image])
            self.assertTrue(MODULE.check_scope(root)["passed"])
            self.assertEqual(baseline.read_bytes(), before)

    def test_current_manifest_cannot_authorize_non_png_or_private_image_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = root / "docs/audit.png"
            image.parent.mkdir(parents=True)
            image.write_bytes(PNG_FIXTURE)
            write_resources(root, [image])
            result = MODULE.check_scope(root)
            self.assertTrue(any(item["category"] == "invalid-essential-image-manifest"
                                for item in result["findings"]))

    @unittest.skipUnless(shutil.which("git"), "Git is required for index validation")
    def test_tracked_mode_does_not_scan_a_clean_worktree_over_a_different_index(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            readme = root / "README.md"
            readme.write_text("staged version")
            subprocess.run(["git", "-C", str(root), "add", "README.md"], check=True)
            self.assertTrue(MODULE.check_scope(root, tracked=True)["passed"])
            readme.write_text("different worktree version")
            result = MODULE.check_scope(root, tracked=True)
            self.assertFalse(result["passed"])
            self.assertTrue(any(item["category"] == "tracked-worktree-differs-from-index"
                                for item in result["findings"]))


if __name__ == "__main__":
    unittest.main()
