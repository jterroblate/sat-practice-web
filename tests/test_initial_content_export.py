"""The historical importer must never overwrite reviewed public content."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/export-current-content.py"
SPEC = importlib.util.spec_from_file_location("initial_content_export", SCRIPT)
assert SPEC and SPEC.loader
EXPORTER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(EXPORTER)


class InitialContentExportTests(unittest.TestCase):
    def test_existing_content_or_provenance_is_refused_before_reading_source(self):
        parent = ROOT / "build/test-tmp"
        parent.mkdir(parents=True, exist_ok=True)
        for marker in ("content/canonical", "docs/content-export.json",
                       "sat-content-registry.json", "sat-practice-migrations.json"):
            with self.subTest(marker=marker), tempfile.TemporaryDirectory(prefix="export-refusal-", dir=parent) as temporary:
                target = Path(temporary) / "public-copy"
                path = target / marker
                path.parent.mkdir(parents=True, exist_ok=True)
                if marker == "content/canonical":
                    path.mkdir()
                    (path / "reviewed.json").write_text("reviewed public improvement", encoding="utf-8")
                else:
                    path.write_text("frozen original bytes", encoding="utf-8")
                before = {p.relative_to(target).as_posix(): p.read_bytes()
                          for p in target.rglob("*") if p.is_file()}
                with patch.object(Path, "read_bytes", side_effect=AssertionError("Must refuse before reading source")), \
                     patch.object(Path, "write_bytes", side_effect=AssertionError("Must never overwrite a prior export")):
                    with self.assertRaisesRegex(FileExistsError, "Initial export refused"):
                        EXPORTER.export_content(Path(temporary) / "missing-maintained-source", target)
                after = {p.relative_to(target).as_posix(): p.read_bytes()
                         for p in target.rglob("*") if p.is_file()}
                self.assertEqual(after, before)

    def test_default_target_refuses_the_existing_checkout(self):
        with patch.object(Path, "write_bytes", side_effect=AssertionError("Existing checkout must remain unchanged")):
            with self.assertRaisesRegex(FileExistsError, "Initial export refused"):
                EXPORTER.export_content(ROOT.parent / "unused-maintained-source")


if __name__ == "__main__":
    unittest.main()
