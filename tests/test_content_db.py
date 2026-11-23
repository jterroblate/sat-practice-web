"""content/db/ and content/canonical/ must hold the same questions, and stay that way."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


def scratch() -> Path:
    """Temporary copies go under the ignored build tree, like the other pipeline tests."""
    parent = ROOT / "build/test-tmp"
    parent.mkdir(parents=True, exist_ok=True)
    return parent


def load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


DB = load("content_db_under_test", "scripts/content_db.py")
BUILDER = load("content_db_builder", "skills/skill-sat-rw-classification/scripts/build_learning_experience.py")
SHARED_ID = "jun2025_apac_a_01"  # appears in two sets


def run(root: Path, command: str) -> tuple[int, str]:
    output = io.StringIO()
    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
        code = DB.main([command, "--root", str(root)])
    return code, output.getvalue()


def rewrite(path: Path, change) -> None:
    """Apply `change` to each row of a table file and write it back in the fixed form."""
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    rows = [row for row in (change(row) for row in rows) if row is not None]
    path.write_text("".join(DB.dump_row(row) + "\n" for row in rows), encoding="utf-8")


class CommittedContentTests(unittest.TestCase):
    def test_tables_and_set_files_agree_in_both_directions(self):
        self.assertEqual(DB.check(ROOT), [])

    def test_no_third_party_prep_book_material_is_committed(self):
        """The repository is public: sources of a tier 3 family stay out of the tables."""
        tables, _ = DB.read_tables(ROOT)
        third_party = {row["family_id"] for row in tables["source_family"] if row["tier"] == 3}
        self.assertEqual([row["label"] for row in tables["source"] if row["family_id"] in third_party], [])
        self.assertEqual({row["source_id"] for row in tables["question_source"]}
                         - {row["source_id"] for row in tables["source"]}, set())

    def test_export_reproduces_every_set_file_byte_for_byte(self):
        tables, problems = DB.read_tables(ROOT)
        self.assertEqual(problems, [])
        exported = DB.export(tables)
        on_disk = {path.relative_to(ROOT).as_posix(): path.read_bytes() for path in DB.canonical_files(ROOT)}
        self.assertEqual(sorted(exported), sorted(on_disk))
        for name, payload in on_disk.items():
            self.assertEqual(exported[name], payload, name)

    def test_grading_revisions_are_unchanged(self):
        tables, _ = DB.read_tables(ROOT)
        exported = DB.export(tables)
        compared = 0
        for path in DB.canonical_files(ROOT):
            before = json.loads(path.read_bytes())["questions"]
            after = json.loads(exported[path.relative_to(ROOT).as_posix()])["questions"]
            self.assertEqual(len(before), len(after))
            for old, new in zip(before, after):
                self.assertEqual(BUILDER.grading_revision(old), BUILDER.grading_revision(new), old["question_id"])
                compared += 1
        self.assertEqual(compared, len(tables["material_item"]))

    def test_each_published_question_is_stored_once_with_its_choices_and_answer(self):
        tables, _ = DB.read_tables(ROOT)
        connection = DB.load_sqlite(tables, ROOT)
        self.addCleanup(connection.close)
        ids_in_sets = {question["question_id"] for path in DB.canonical_files(ROOT)
                       for question in json.loads(path.read_bytes())["questions"]}
        stored = {row[0] for row in connection.execute("SELECT question_id FROM question WHERE status = 'published'")}
        self.assertEqual(stored, ids_in_sets)
        incomplete = connection.execute(
            "SELECT question_id FROM question q WHERE status = 'published' AND ("
            " (SELECT count(*) FROM choice c WHERE c.question_id = q.question_id) != 4"
            " OR NOT EXISTS (SELECT 1 FROM explanation e WHERE e.question_id = q.question_id))").fetchall()
        self.assertEqual(incomplete, [])
        reused = connection.execute(
            "SELECT count(*) FROM (SELECT question_id FROM material_item GROUP BY 1 HAVING count(*) > 1)").fetchone()[0]
        self.assertGreater(reused, 0, "the baseline has questions shared between sets")

    def test_drafts_are_complete_and_stay_out_of_the_sets(self):
        tables, _ = DB.read_tables(ROOT)
        connection = DB.load_sqlite(tables, ROOT)
        self.addCleanup(connection.close)
        placed = connection.execute(
            "SELECT count(*) FROM material_item i JOIN question q USING (question_id)"
            " WHERE q.status = 'draft'").fetchone()[0]
        self.assertEqual(placed, 0)
        unanswerable = connection.execute(
            "SELECT question_id FROM question q WHERE status = 'draft' AND ("
            " (format = 'choice' AND (SELECT count(*) FROM choice c WHERE c.question_id = q.question_id) < 2)"
            " OR (format = 'numeric' AND NOT EXISTS"
            "     (SELECT 1 FROM numeric_answer a WHERE a.question_id = q.question_id)))").fetchall()
        self.assertEqual(unanswerable, [])

    def test_every_question_is_a_real_exam_or_a_practice_question(self):
        tables, _ = DB.read_tables(ROOT)
        connection = DB.load_sqlite(tables, ROOT)
        self.addCleanup(connection.close)
        unsorted = connection.execute(
            "SELECT count(*) FROM question q LEFT JOIN question_origin o USING (question_id)"
            " WHERE o.kind IS NULL OR o.kind NOT IN ('real_exam', 'practice')").fetchone()[0]
        self.assertEqual(unsorted, 0)
        twice = connection.execute(
            "SELECT count(*) FROM (SELECT question_id FROM question_origin GROUP BY 1 HAVING count(*) > 1)").fetchone()[0]
        self.assertEqual(twice, 0)

    def test_reading_and_writing_practice_is_chosen_from_four_categories(self):
        tables, _ = DB.read_tables(ROOT)
        connection = DB.load_sqlite(tables, ROOT)
        self.addCleanup(connection.close)
        categories = dict(connection.execute(
            "SELECT domain_zh, count(*) FROM practice_pool"
            " WHERE status = 'published' AND section = 'reading_writing' GROUP BY 1"))
        self.assertEqual(set(categories), {"信息与观点", "技巧与构造", "观点表达", "标准英语用法"})
        self.assertEqual(sum(categories.values()), sum(row["status"] == "published" for row in tables["question"]))
        unsorted = connection.execute(
            "SELECT count(*) FROM practice_pool WHERE status = 'published'"
            " AND (kind IS NULL OR domain_zh IS NULL)").fetchone()[0]
        self.assertEqual(unsorted, 0)


class EditingTests(unittest.TestCase):
    """Work on a private copy of the content so the repository is never touched."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=scratch())
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        shutil.copytree(ROOT / "content", self.root / "content")
        self.db = self.root / "content/db"

    def canonical(self, name: str) -> dict:
        return json.loads((self.root / "content/canonical/sets" / name).read_text(encoding="utf-8"))

    def test_copy_starts_clean(self):
        self.assertEqual(run(self.root, "check")[0], 0)

    def test_editing_one_table_row_updates_every_set_that_uses_the_question(self):
        def edit(row):
            if row["question_id"] == SHARED_ID:
                row["prompt"] += " (revised)"
            return row
        rewrite(self.db / "question.jsonl", edit)
        code, message = run(self.root, "check")
        self.assertEqual(code, 1)
        self.assertIn("python3 scripts/content_db.py export", message)

        code, changed = run(self.root, "export")
        self.assertEqual(code, 0)
        self.assertEqual(sorted(changed.split()), [
            "content/canonical/sets/06-2025-06-apac-a-partial.json",
            "content/canonical/sets/08-2025-06-apac-a-full.json"])
        for name in ("06-2025-06-apac-a-partial.json", "08-2025-06-apac-a-full.json"):
            prompts = [q["prompt"] for q in self.canonical(name)["questions"] if q["question_id"] == SHARED_ID]
            self.assertEqual(len(prompts), 1)
            self.assertTrue(prompts[0].endswith(" (revised)"))
        self.assertEqual(run(self.root, "check")[0], 0)

    def test_editing_a_set_file_is_carried_into_the_tables_without_renumbering(self):
        path = self.root / "content/canonical/sets/02-gold48.json"
        document = json.loads(path.read_text(encoding="utf-8"))
        document["questions"][0]["explanation"]["short"] += " Revised."
        document["questions"][1]["source_set"] = "A newly named source"
        path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self.assertEqual(run(self.root, "check")[0], 1)

        sources_before = (self.db / "source.jsonl").read_text(encoding="utf-8")
        layouts_before = (self.db / "legacy_layout.jsonl").read_text(encoding="utf-8")
        code, changed = run(self.root, "normalize")
        self.assertEqual(code, 0)
        self.assertEqual(sorted(changed.split()), [
            "content/db/explanation.jsonl", "content/db/material_item.jsonl", "content/db/source.jsonl"])
        sources_after = (self.db / "source.jsonl").read_text(encoding="utf-8")
        self.assertTrue(sources_after.startswith(sources_before), "existing source ids must not move")
        self.assertIn("A newly named source", sources_after[len(sources_before):])
        self.assertEqual((self.db / "legacy_layout.jsonl").read_text(encoding="utf-8"), layouts_before)
        self.assertEqual(run(self.root, "check")[0], 0)

    def test_one_sided_edit_of_a_shared_question_is_refused(self):
        path = self.root / "content/canonical/sets/06-2025-06-apac-a-partial.json"
        document = json.loads(path.read_text(encoding="utf-8"))
        target = next(q for q in document["questions"] if q["question_id"] == SHARED_ID)
        target["passage"] += " Extra sentence."
        path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        code, message = run(self.root, "normalize")
        self.assertEqual(code, 1)
        self.assertIn(SHARED_ID, message)
        self.assertIn("two different values for passage", message)

    def test_answer_must_be_one_of_the_choices(self):
        def edit(row):
            if row["question_id"] == SHARED_ID:
                row["correct_answer"] = "E"
            return row
        rewrite(self.db / "question.jsonl", edit)
        code, message = run(self.root, "check")
        self.assertEqual(code, 1)
        self.assertIn(f"question [{SHARED_ID}]: question_id, correct_answer has no matching row in choice", message)

    def test_question_cannot_point_at_a_missing_subtype(self):
        used = json.loads((self.db / "question.jsonl").read_text(encoding="utf-8").splitlines()[0])["subtype_id"]
        rewrite(self.db / "subtype.jsonl", lambda row: None if row["subtype_id"] == used else row)
        code, message = run(self.root, "check")
        self.assertEqual(code, 1)
        self.assertIn("subtype_id has no matching row in subtype", message)

    def test_unused_rows_and_untidy_files_are_reported(self):
        with (self.db / "source.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(DB.dump_row({"source_id": "src-999", "label": "Nobody uses this"}) + "\n")
        code, message = run(self.root, "check")
        self.assertEqual(code, 1)
        self.assertIn("content/db/source.jsonl", message)

        shutil.copyfile(ROOT / "content/db/source.jsonl", self.db / "source.jsonl")
        lines = (self.db / "skill.jsonl").read_text(encoding="utf-8").splitlines()
        (self.db / "skill.jsonl").write_text("\n".join(reversed(lines)) + "\n", encoding="utf-8")
        code, message = run(self.root, "check")
        self.assertEqual(code, 1)
        self.assertIn("rows must be sorted by skill_id", message)

    # ---- export writes only set and collection files, and all or nothing

    GOLD = "content/canonical/sets/02-gold48.json"

    def snapshot(self) -> dict[str, bytes]:
        return {path.relative_to(self.root).as_posix(): path.read_bytes()
                for path in sorted((self.root / "content").rglob("*")) if path.is_file() and not path.is_symlink()}

    def revise_gold(self) -> str:
        """Edit a question only 02-gold48 holds, so a correct export would rewrite that file."""
        gold = self.canonical("02-gold48.json")["questions"][0]["question_id"]
        def edit(row):
            if row["question_id"] == gold:
                row["prompt"] += " (revised)"
            return row
        rewrite(self.db / "question.jsonl", edit)
        return gold

    def outside(self, name: str = "outside-export.json") -> Path:
        """A sentinel file outside the copied checkout."""
        directory = Path(tempfile.mkdtemp(dir=scratch()))
        self.addCleanup(shutil.rmtree, directory, True)
        sentinel = directory / name
        sentinel.write_text("sentinel\n", encoding="utf-8")
        return sentinel

    def test_export_refuses_a_target_outside_the_set_folders_and_writes_nothing(self):
        sentinel = self.outside()
        self.revise_gold()
        targets = ["../outside-export.json", str(sentinel), "content/canonical/sets/../../../outside-export.json",
                   "content/canonical/other/x.json", "content/canonical/sets/x.txt", "content/canonical/sets/Upper.json",
                   "content/db/question.jsonl", "content/canonical/sets/./02-gold48.json"]
        for target in targets:
            with self.subTest(target=target):
                def point(row, target=target):
                    if row["data_path"] == self.GOLD:
                        row["data_path"] = target
                    return row
                shutil.copyfile(ROOT / "content/db/material.jsonl", self.db / "material.jsonl")
                rewrite(self.db / "material.jsonl", point)
                before = self.snapshot()
                code, message = run(self.root, "export")
                self.assertEqual(code, 1, message)
                self.assertIn("data_path", message)
                self.assertEqual(self.snapshot(), before)
                self.assertEqual(sentinel.read_text(encoding="utf-8"), "sentinel\n")
                self.assertFalse((self.root.parent / "outside-export.json").exists())
                self.assertEqual(run(self.root, "check")[0], 1)

    def test_export_refuses_a_symbolic_link_at_the_file_or_a_folder_above_it(self):
        sentinel = self.outside()
        self.revise_gold()
        gold = self.root / self.GOLD
        sentinel.write_bytes(gold.read_bytes())
        gold.unlink()
        gold.symlink_to(sentinel)
        before = sentinel.read_bytes()
        code, message = run(self.root, "export")
        self.assertEqual(code, 1, message)
        self.assertIn("is a symbolic link", message)
        self.assertEqual(sentinel.read_bytes(), before)

        # A linked folder: the real set files move outside, the checkout keeps only a link.
        gold.unlink()
        gold.write_bytes(before)
        elsewhere = sentinel.parent / "sets"
        shutil.move(str(self.root / "content/canonical/sets"), elsewhere)
        (self.root / "content/canonical/sets").symlink_to(elsewhere, target_is_directory=True)
        moved = {path.name: path.read_bytes() for path in elsewhere.iterdir()}
        code, message = run(self.root, "export")
        self.assertEqual(code, 1, message)
        self.assertIn("content/canonical/sets is a symbolic link", message)
        self.assertEqual({path.name: path.read_bytes() for path in elsewhere.iterdir()}, moved)

    def test_a_failed_write_leaves_every_file_as_it_was(self):
        self.revise_gold()
        def edit(row):
            if row["question_id"] == SHARED_ID:
                row["prompt"] += " (revised)"
            return row
        rewrite(self.db / "question.jsonl", edit)  # three set files to rewrite now
        before = self.snapshot()
        real_replace, calls = DB.os.replace, []
        def failing(source, target):
            calls.append(target)
            if len(calls) == 2:
                raise OSError("disk full")
            real_replace(source, target)
        with mock.patch.object(DB.os, "replace", failing):
            code, message = run(self.root, "export")
        self.assertEqual(code, 1)
        self.assertIn("nothing was changed", message)
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(list(self.root.rglob(".*.tmp")), [])
        code, changed = run(self.root, "export")
        self.assertEqual((code, len(changed.split())), (0, 3))

    def test_a_published_question_withdrawn_to_draft_while_still_placed_is_refused(self):
        gold = self.canonical("02-gold48.json")["questions"][0]["question_id"]
        def withdraw(row):
            if row["question_id"] == gold:
                row["status"] = "draft"
            return row
        rewrite(self.db / "question.jsonl", withdraw)
        before = self.snapshot()
        code, message = run(self.root, "check")
        self.assertEqual(code, 1)
        self.assertIn(f"{gold} is draft, not published; withdraw it from the set first", message)
        code, message = run(self.root, "export")
        self.assertEqual(code, 1)
        self.assertIn("withdraw it from the set first", message)
        self.assertEqual(self.snapshot(), before)
        with self.assertRaises(DB.ContentError):
            DB.export(DB.read_tables(self.root)[0])

    def test_check_writes_nothing(self):
        self.revise_gold()
        before = self.snapshot()
        self.assertEqual(run(self.root, "check")[0], 1)
        self.assertEqual(self.snapshot(), before)

    def test_unknown_field_in_a_set_file_needs_a_column_first(self):
        path = self.root / "content/canonical/sets/02-gold48.json"
        document = json.loads(path.read_text(encoding="utf-8"))
        document["questions"][0]["teacher_note"] = "not modelled"
        path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        code, message = run(self.root, "normalize")
        self.assertEqual(code, 1)
        self.assertIn("teacher_note", message)


if __name__ == "__main__":
    unittest.main()
