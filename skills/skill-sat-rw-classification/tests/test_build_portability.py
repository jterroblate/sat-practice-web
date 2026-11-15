from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "skills" / "skill-sat-rw-classification" / "scripts" / "build_learning_experience.py"
SPEC = importlib.util.spec_from_file_location("sat_learning_build", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class BuildPortabilityTests(unittest.TestCase):
    def test_zen_assets_are_vendored_inside_skill(self) -> None:
        self.assertTrue(MODULE.ZEN_KIT.is_relative_to(MODULE.SKILL))
        self.assertNotIn('ZEN_KIT = Path("/Users/', SCRIPT.read_text(encoding="utf-8"))
        for name in MODULE.ZEN_ASSETS:
            self.assertTrue((MODULE.ZEN_KIT / name).is_file(), name)

    def test_sync_zen_assets_works_without_external_user_directories(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            MODULE.sync_zen_assets(root)
            for name in MODULE.ZEN_ASSETS:
                self.assertEqual(
                    (root / "assets" / "zen-interface" / name).read_bytes(),
                    (MODULE.ZEN_KIT / name).read_bytes(),
                )


if __name__ == "__main__":
    unittest.main()
