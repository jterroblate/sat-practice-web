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

        # The broken one was already closed. Opened by hand, it is closed again.
        still_closed.update({"examEligible": True, "examVariants": [dict(EXAMS.CURATED_VARIANT)]})
        self.assertEqual(EXAMS.apply(registry, {}), ["set-hand-made-broken"])
        self.assertEqual(still_closed, broken)

    def test_an_open_hand_made_exam_closes_when_a_member_goes_and_reopens_when_it_is_back(self):
        """Open first, then incomplete, then recomputed: the curated set 34 of the registry itself."""
        registry = copy.deepcopy(self.registry)
        entry = next(item for item in registry["entries"] if item["contentId"] == "set-2026-06-apac-b-fixed-hard")
        self.assertTrue(entry["examEligible"])
        members = EXAMS.published_members(self.tables)
        spec = copy.deepcopy(entry["moduleSpec"])
        self.assertEqual(EXAMS.apply(registry, self.specs, members), [])

        dropped = entry["moduleSpec"]["m1"]["questionIds"][3]
        incomplete = {**members, entry["contentId"]: members[entry["contentId"]] - {dropped}}
        self.assertEqual(EXAMS.apply(registry, self.specs, incomplete), [entry["contentId"]])
        self.assertEqual((entry["examEligible"], entry["examVariants"]), (False, []))
        self.assertEqual(entry["moduleSpec"], spec, "the hand-made modules are kept for repair")
        self.assertNotIn(entry["contentId"], [item["contentId"] for item in registry["entries"] if item["examEligible"]])

        self.assertEqual(EXAMS.apply(registry, self.specs, members), [entry["contentId"]])
        self.assertTrue(entry["examEligible"])
        self.assertEqual(entry["examVariants"], [EXAMS.CURATED_VARIANT])

    def test_a_hand_made_exam_whose_question_is_withdrawn_to_a_draft_is_closed(self):
        tables = copy.deepcopy(self.tables)
        registry = copy.deepcopy(self.registry)
        entry = next(item for item in registry["entries"] if item["contentId"] == "set-2026-06-apac-b-fixed-hard")
        withdrawn = entry["moduleSpec"]["m2_hard"]["questionIds"][0]
        for row in tables["question"]:
            if row["question_id"] == withdrawn:
                row["status"] = "draft"
        self.assertEqual(EXAMS.apply(registry, self.specs, EXAMS.published_members(tables)), [entry["contentId"]])
        self.assertFalse(entry["examEligible"])

    def test_an_open_derived_exam_closes_when_its_source_or_module_is_gone(self):
        registry = copy.deepcopy(self.registry)
        derived = [item for item in registry["entries"]
                   if item["examEligible"] and item["contentId"] in self.specs][:2]
        self.assertEqual(len(derived), 2)
        gone_source, too_short = derived
        specs = {key: value for key, value in self.specs.items() if key != gone_source["contentId"]}
        specs[too_short["contentId"]] = None
        changed = EXAMS.apply(registry, specs, EXAMS.published_members(self.tables))
        self.assertEqual(sorted(changed), sorted([gone_source["contentId"], too_short["contentId"]]))
        self.assertEqual((gone_source["examEligible"], gone_source["examVariants"]), (False, []))
        self.assertEqual((too_short["examEligible"], too_short["moduleSpec"], too_short["examVariants"]),
                         (False, None, []))

    def test_dropping_module_questions_in_the_tables_closes_the_derived_exam(self):
        """Recompute from the tables after a module falls under MIN_QUESTIONS."""
        content_id = next(key for key, spec in self.specs.items() if spec)
        first = self.specs[content_id]["moduleSpec"]["m1"]["questionIds"]
        dropped = set(first[: len(first) - EXAMS.MIN_QUESTIONS + 1])
        tables = copy.deepcopy(self.tables)
        for row in tables["question"]:
            if row["question_id"] in dropped:
                row["status"] = "draft"
        tables["material_item"] = [row for row in tables["material_item"]
                                   if not (row["content_id"] == content_id and row["question_id"] in dropped)]
        specs = EXAMS.exam_specs(tables, ROOT)
        self.assertIsNone(specs[content_id])
        registry = copy.deepcopy(self.registry)
        self.assertIn(content_id, EXAMS.apply(registry, specs, EXAMS.published_members(tables)))
        entry = next(item for item in registry["entries"] if item["contentId"] == content_id)
        self.assertFalse(entry["examEligible"])

    def test_a_real_exam_set_that_stops_qualifying_is_switched_off(self):
        entry = {"contentId": "set-exam-x", "kind": "set", "status": "published", "examEligible": True,
                 "examVariants": [{"id": "fixed"}], "moduleSpec": {"m1": {}, "m2": {}}}
        registry = {"entries": [entry]}
        self.assertEqual(EXAMS.apply(registry, {"set-exam-x": None}), ["set-exam-x"])
        self.assertEqual((entry["examEligible"], entry["moduleSpec"], entry["examVariants"]), (False, None, []))


if __name__ == "__main__":
    unittest.main()
