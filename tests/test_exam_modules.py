"""Real-exam sets are offered as fixed two-module mock exams only as the tables support."""

from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


def load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


DB = load("exam_content_db", "scripts/content_db.py")
EXAMS = load("exam_modules", "scripts/exam_modules.py")


class ExamModuleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tables, problems = DB.read_tables(ROOT)
        assert problems == []
        cls.specs = EXAMS.exam_specs(cls.tables, ROOT)
        cls.registry = json.loads((ROOT / EXAMS.REGISTRY).read_text(encoding="utf-8"))
        cls.offered = [entry for entry in cls.registry["entries"] if entry["examEligible"]]

    def test_registry_is_in_step_with_the_tables(self):
        self.assertEqual(EXAMS.apply(copy.deepcopy(self.registry), self.specs), [])

    def test_some_real_exams_qualify_and_some_do_not(self):
        self.assertTrue(any(self.specs.values()))
        self.assertTrue(any(spec is None for spec in self.specs.values()))

    def test_every_offered_exam_is_two_separate_modules_of_its_own_questions(self):
        self.assertTrue(self.offered)
        for entry in self.offered:
            content_id = entry["contentId"]
            self.assertEqual((entry["kind"], entry["status"], entry["section"]),
                             ("set", "published", "reading_writing"), content_id)
            self.assertEqual(len(entry["examVariants"]), 1, content_id)
            variant = entry["examVariants"][0]
            self.assertEqual(variant["branch"], "hard", content_id)
            first, second = (entry["moduleSpec"][key] for key in variant["modules"])
            for module in (first, second):
                ids = module["questionIds"]
                self.assertEqual(len(ids), len(set(ids)), content_id)
                self.assertGreaterEqual(len(ids), EXAMS.MIN_QUESTIONS, content_id)
                self.assertLessEqual(len(ids), EXAMS.FULL_MODULE[0], content_id)
                self.assertLessEqual(set(ids), set(entry["questionIds"]), content_id)
                self.assertEqual(module["durationSeconds"] % 60, 0, content_id)
                self.assertLessEqual(module["durationSeconds"], EXAMS.FULL_MODULE[1], content_id)
            self.assertFalse(set(first["questionIds"]) & set(second["questionIds"]), content_id)
            self.assertNotIn("Easier", second["label"], content_id)

    def test_a_shorter_module_gets_proportionally_less_time(self):
        self.assertEqual(EXAMS.duration(27), 32 * 60)
        self.assertEqual(EXAMS.duration(24), 29 * 60)
        self.assertEqual(EXAMS.duration(22), 27 * 60)

    def test_hand_made_modules_are_switched_on_but_never_rewritten(self):
        entry = {"contentId": "set-hand-made", "kind": "set", "status": "published", "examEligible": False,
                 "examVariants": [], "questionIds": ["a", "b"],
                 "moduleSpec": {"m1": {"questionIds": ["a"]}, "m2_hard": {"questionIds": ["b"]}}}
        broken = {**copy.deepcopy(entry), "contentId": "set-hand-made-broken", "questionIds": ["a"]}
        other = {"contentId": "set-plain", "kind": "set", "status": "published", "examEligible": False,
                 "examVariants": [], "moduleSpec": None}
        registry = {"entries": [copy.deepcopy(entry), copy.deepcopy(broken), copy.deepcopy(other)]}
        self.assertEqual(EXAMS.apply(registry, {}), ["set-hand-made"])
        changed, still_closed, untouched = registry["entries"]
        self.assertEqual(changed["moduleSpec"], entry["moduleSpec"])
        self.assertEqual((changed["examEligible"], changed["examVariants"][0]["modules"]), (True, ["m1", "m2_hard"]))
        self.assertEqual(still_closed, broken)
        self.assertEqual(untouched, other)

    def test_a_real_exam_set_that_stops_qualifying_is_switched_off(self):
        entry = {"contentId": "set-exam-x", "kind": "set", "status": "published", "examEligible": True,
                 "examVariants": [{"id": "fixed"}], "moduleSpec": {"m1": {}, "m2": {}}}
        registry = {"entries": [entry]}
        self.assertEqual(EXAMS.apply(registry, {"set-exam-x": None}), ["set-exam-x"])
        self.assertEqual((entry["examEligible"], entry["moduleSpec"], entry["examVariants"]), (False, None, []))


if __name__ == "__main__":
    unittest.main()
