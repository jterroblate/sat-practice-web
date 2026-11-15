"""Validate a clean full-content checkout without private production evidence."""

from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SKILL = "skills/skill-sat-rw-classification"
EMAIL_LIKE = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")
SOURCE_PATH = re.compile(r"/Users/|/private/tmp/|/tmp/|[A-Za-z]:\\Users\\")


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def questions_in_html(html: str) -> list[dict]:
    marker = re.search(r"\bconst\s+QUESTIONS\s*=", html)
    if marker is None:
        raise AssertionError("Missing generated question data")
    value, _ = json.JSONDecoder().raw_decode(html[marker.end():].lstrip())
    return value


def display_resource_references(value: object):
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"chart_image_data_uri", "image_data_uri", "material_panel_crop_data_uri"} and isinstance(item, str):
                yield item
            else:
                yield from display_resource_references(item)
    elif isinstance(value, list):
        for item in value:
            yield from display_resource_references(item)


class FullContentPipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        parent = ROOT / "build/test-tmp"
        parent.mkdir(parents=True, exist_ok=True)
        cls.temporary = tempfile.TemporaryDirectory(prefix="clean-full-", dir=parent)
        cls.checkout = Path(cls.temporary.name)
        registry = load(ROOT / "sat-content-registry.json")
        report = load(ROOT / "docs/content-export.json")
        inputs = [
            "scripts/build-site.py", "scripts/build-demo.py", "scripts/build_output.py",
            "fixtures/original-demo.json", "sat-content-registry.json",
            "sat-practice-migrations.json", "docs/content-export.json",
            SKILL + "/scripts/build_learning_experience.py",
            SKILL + "/assets/templates/html_template_v1.1.html",
            SKILL + "/assets/templates/learning_index_v2.html",
            SKILL + "/assets/templates/learning_storage.js",
            *[SKILL + "/assets/zen-interface/" + name + ".svg"
              for name in ("background", "texture", "pattern")],
            *[entry["dataPath"] for entry in registry["entries"]
              if entry["kind"] in {"set", "collection"}],
            *[item["publicPath"] for item in report["resources"]],
        ]
        for relative in inputs:
            destination = cls.checkout / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes((ROOT / relative).read_bytes())
        completed = cls.run_build("build-site.py")
        if completed.returncode:
            cls.temporary.cleanup()
            raise RuntimeError(completed.stdout + completed.stderr)
        cls.output = cls.checkout / "build/site"
        cls.manifest = load(cls.output / "sat-practice-manifest.json")

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    @classmethod
    def run_build(cls, name):
        return subprocess.run([sys.executable, "scripts/" + name], cwd=cls.checkout,
                              text=True, capture_output=True)

    def test_01_full_identity_answer_and_route_contract(self):
        registry = load(self.checkout / "sat-content-registry.json")
        self.assertEqual(Counter(e["kind"] for e in registry["entries"]),
                         {"set": 34, "collection": 3, "category": 11})
        self.assertEqual(self.manifest["pageCount"], 48)
        self.assertEqual(self.manifest["uniqueQuestionCount"], 1752)
        answers = {}
        occurrences = 0
        for entry in registry["entries"]:
            self.assertFalse(entry["examEligible"])
            self.assertEqual(entry["examVariants"], [])
            self.assertTrue((self.output / entry["route"]).is_file())
            if entry["kind"] not in {"set", "collection"}:
                continue
            package = load(self.checkout / entry["dataPath"])
            occurrences += len(package["questions"])
            for question in package["questions"]:
                qid, answer = question["question_id"], question["correct_answer"]
                if qid in answers:
                    self.assertEqual(answer, answers[qid], "Shared question IDs must use the same answer")
                answers[qid] = answer
        self.assertEqual(occurrences, 1765)
        self.assertEqual(len(answers), 1752)
        category_coverage = Counter(qid for page in self.manifest["pages"]
                                    if page["kind"] == "category" for qid in page["questionIds"])
        self.assertEqual(set(category_coverage), set(answers))
        self.assertTrue(all(count == 1 for count in category_coverage.values()))
        for page in self.manifest["pages"]:
            questions = questions_in_html((self.output / page["route"]).read_text(encoding="utf-8"))
            self.assertEqual([q["question_id"] for q in questions], page["questionIds"])
            for question in questions:
                self.assertEqual(question["correct_answer"], answers[question["question_id"]])
        # This is the immutable initial export provenance. Later reviewed text
        # changes need not rewrite its original package hashes.
        report = load(self.checkout / "docs/content-export.json")
        self.assertEqual(len(report["packages"]), 37)
        for package in report["packages"]:
            self.assertEqual(package["sourceIdentityAnswerSha256"], package["publicIdentityAnswerSha256"])
        self.assertEqual(hashlib.sha256((self.checkout / "sat-practice-migrations.json").read_bytes()).hexdigest(),
                         report["migrationSha256"])

    def test_02_generated_resources_paths_and_javascript(self):
        self.assertIsNotNone(shutil.which("node"), "Node.js is required for JavaScript syntax checks")
        for path in (self.checkout / "content/canonical").rglob("*.json"):
            text = path.read_text(encoding="utf-8")
            self.assertIsNone(SOURCE_PATH.search(text))
            self.assertIsNone(EMAIL_LIKE.search(text))
        routes = ["index.html", *[page["route"] for page in self.manifest["pages"]]]
        for route in routes:
            html = (self.output / route).read_text(encoding="utf-8")
            with self.subTest(route=route):
                self.assertIsNone(SOURCE_PATH.search(html))
                self.assertIsNone(EMAIL_LIKE.search(html))
                self.assertNotIn("__SAT_STORAGE_RUNTIME__", html)
                if route != "index.html":
                    for reference in display_resource_references(questions_in_html(html)):
                        resource = (self.output / route).parent / reference
                        self.assertTrue(resource.resolve().is_relative_to(self.output.resolve()))
                        self.assertTrue(resource.is_file(), "Every visible content resource must resolve locally")
                # The page templates contain escaped closing script tags in
                # strings, so literal closing tags delimit each real script.
                scripts = re.findall(r"<script([^>]*)>([\s\S]*?)</script>", html, flags=re.IGNORECASE)
                for attrs, script in scripts:
                    if "application/json" in attrs or re.search(r"\bsrc\s*=", attrs):
                        continue
                    completed = subprocess.run(["node", "--check", "-"], input=script,
                                               text=True, capture_output=True)
                    self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_03_rebuild_removes_retired_outputs_and_preserves_other_builds(self):
        retired_route = self.output / "sets/retired-route.html"
        retired_resource = self.output / "assets/question-media/retired.png"
        retired_route.write_text("retired", encoding="utf-8")
        retired_resource.write_bytes(b"retired")
        unreferenced = self.checkout / "assets/question-media/unreferenced.png"
        unreferenced.write_bytes(b"not a registered content dependency")
        reports = self.checkout / "build/reports/keep.txt"
        reports.parent.mkdir(parents=True, exist_ok=True)
        reports.write_text("keep", encoding="utf-8")
        demo_sentinel = self.checkout / "build/demo/keep.txt"
        demo_sentinel.parent.mkdir(parents=True, exist_ok=True)
        demo_sentinel.write_text("keep", encoding="utf-8")
        completed = self.run_build("build-site.py")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertFalse(retired_route.exists())
        self.assertFalse(retired_resource.exists())
        self.assertFalse((self.output / "assets/question-media/unreferenced.png").exists())
        self.assertEqual(reports.read_text(encoding="utf-8"), "keep")
        self.assertEqual(demo_sentinel.read_text(encoding="utf-8"), "keep")

        completed = self.run_build("build-demo.py")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertFalse(demo_sentinel.exists())
        demo_retired = self.checkout / "build/demo/sets/retired-route.html"
        demo_retired.write_text("retired", encoding="utf-8")
        completed = self.run_build("build-demo.py")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertFalse(demo_retired.exists())
        self.assertTrue((self.output / "index.html").is_file())
        self.assertEqual(reports.read_text(encoding="utf-8"), "keep")

    def test_04_failed_rebuild_preserves_last_good_preview(self):
        before = (self.output / "index.html").read_bytes()
        registry_path = self.checkout / "sat-content-registry.json"
        original = registry_path.read_bytes()
        registry = json.loads(original)
        source = next(entry for entry in registry["entries"] if entry["kind"] == "set")
        source["dataPath"] = "content/canonical/sets/missing.json"
        registry_path.write_text(json.dumps(registry), encoding="utf-8")
        try:
            completed = self.run_build("build-site.py")
            self.assertNotEqual(completed.returncode, 0)
            self.assertEqual((self.output / "index.html").read_bytes(), before)
        finally:
            registry_path.write_bytes(original)


if __name__ == "__main__":
    unittest.main()
