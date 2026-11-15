"""Build a clean checkout using only public code and original examples."""

from __future__ import annotations

from collections import Counter
from html.parser import HTMLParser
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SKILL = "skills/skill-sat-rw-classification"
INPUT_FILES = [
    "scripts/build-demo.py",
    "scripts/build_output.py",
    "fixtures/original-demo.json",
    SKILL + "/scripts/build_learning_experience.py",
    SKILL + "/assets/templates/html_template_v1.1.html",
    SKILL + "/assets/templates/learning_index_v2.html",
    SKILL + "/assets/templates/learning_storage.js",
    *[SKILL + "/assets/zen-interface/" + name + ".svg"
      for name in ("background", "texture", "pattern")],
]


class ScriptCollector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.scripts = []
        self.buffer = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "script" and not attrs.get("src") and attrs.get("type", "") != "application/json":
            self.buffer = []

    def handle_data(self, data):
        if self.buffer is not None:
            self.buffer.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self.buffer is not None:
            self.scripts.append("".join(self.buffer))
            self.buffer = None


class CleanDemoPipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Keep test output inside the ignored build directory as well.
        parent = ROOT / "build/test-tmp"
        parent.mkdir(parents=True, exist_ok=True)
        cls.temporary = tempfile.TemporaryDirectory(prefix="clean-demo-", dir=parent)
        cls.checkout = Path(cls.temporary.name)
        for relative in INPUT_FILES:
            target = cls.checkout / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((ROOT / relative).read_bytes())
        cls.fixture_before = (cls.checkout / "fixtures/original-demo.json").read_bytes()
        completed = subprocess.run([sys.executable, "scripts/build-demo.py"],
                                   cwd=cls.checkout, text=True, capture_output=True)
        if completed.returncode:
            cls.temporary.cleanup()
            raise RuntimeError(completed.stdout + completed.stderr)
        cls.output = cls.checkout / "build/demo"
        cls.manifest = json.loads((cls.output / "sat-practice-manifest.json").read_text(encoding="utf-8"))

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_clean_build_preserves_original_inputs_and_route_coverage(self):
        self.assertEqual((self.checkout / "fixtures/original-demo.json").read_bytes(), self.fixture_before)
        self.assertFalse((self.checkout / "sat-content-registry.json").exists())
        self.assertFalse((self.checkout / "index.html").exists())
        self.assertEqual(self.manifest["uniqueQuestionCount"], 11)
        self.assertEqual(self.manifest["pageCount"], 13)
        pages = self.manifest["pages"]
        self.assertEqual(Counter(page["kind"] for page in pages), {"set": 1, "collection": 1, "category": 11})
        self.assertEqual(sum(page["questionCount"] for page in pages if page["kind"] == "set"), 11)
        self.assertEqual(sum(page["questionCount"] for page in pages if page["kind"] == "collection"), 1)
        coverage = Counter(qid for page in pages if page["kind"] == "category" for qid in page["questionIds"])
        self.assertEqual(len(coverage), 11)
        self.assertTrue(all(count == 1 for count in coverage.values()))
        for page in pages:
            with self.subTest(route=page["route"]):
                self.assertTrue((self.output / page["route"]).is_file())
                self.assertFalse(page["examEligible"])
                self.assertEqual(page["examVariants"], [])
                self.assertTrue(all(qid.startswith("demo_original_") for qid in page["questionIds"]))

    def test_rendered_javascript_has_no_unresolved_tokens_and_compiles(self):
        self.assertIsNotNone(shutil.which("node"), "Node.js is required for the JavaScript syntax check")
        routes = ["index.html", *[page["route"] for page in self.manifest["pages"]]]
        for route in routes:
            html = (self.output / route).read_text(encoding="utf-8")
            with self.subTest(route=route):
                for token in ("__SAT_STORAGE_RUNTIME__", "__SAT_MANIFEST_JSON__", "__QUESTIONS_JSON__", "__PAGE_META_JSON__"):
                    self.assertNotIn(token, html)
                self.assertNotIn("/Users/", html)
                self.assertNotIn("tcloudbaseapp.com", html)
                self.assertNotIn("jtvocabmaster.cn", html)
                collector = ScriptCollector()
                collector.feed(html)
                self.assertTrue(collector.scripts)
                for script in collector.scripts:
                    completed = subprocess.run(["node", "--check", "-"], input=script,
                                               text=True, capture_output=True)
                    self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()
