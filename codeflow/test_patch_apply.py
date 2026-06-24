#!/usr/bin/env python3
"""Unit tests for patch_apply and file_context (no LLM)."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

STACK_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(STACK_ROOT))

from codeflow.file_context import discover_paths, gather_file_context  # noqa: E402
from codeflow.patch_apply import apply_edits, validate_repo_path  # noqa: E402


class PatchApplyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_single_replace(self) -> None:
        target = self.repo / "foo.py"
        target.write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
        result = apply_edits(
            [{"path": "foo.py", "type": "replace", "old_str": "beta", "new_str": "BETA"}],
            self.repo,
        )
        self.assertTrue(result.ok)
        self.assertEqual(target.read_text(encoding="utf-8"), "alpha\nBETA\ngamma\n")

    def test_ambiguous_replace(self) -> None:
        target = self.repo / "dup.py"
        target.write_text("x\nx\n", encoding="utf-8")
        result = apply_edits(
            [{"path": "dup.py", "type": "replace", "old_str": "x", "new_str": "y"}],
            self.repo,
        )
        self.assertFalse(result.ok)
        self.assertEqual(target.read_text(encoding="utf-8"), "x\nx\n")

    def test_missing_old_str(self) -> None:
        target = self.repo / "miss.py"
        target.write_text("hello\n", encoding="utf-8")
        result = apply_edits(
            [{"path": "miss.py", "type": "replace", "old_str": "nope", "new_str": "y"}],
            self.repo,
        )
        self.assertFalse(result.ok)

    def test_create(self) -> None:
        result = apply_edits(
            [{"path": "new.py", "type": "create", "content": "print('hi')\n"}],
            self.repo,
        )
        self.assertTrue(result.ok)
        self.assertEqual((self.repo / "new.py").read_text(encoding="utf-8"), "print('hi')\n")

    def test_delete(self) -> None:
        target = self.repo / "gone.py"
        target.write_text("bye\n", encoding="utf-8")
        result = apply_edits([{"path": "gone.py", "type": "delete"}], self.repo)
        self.assertTrue(result.ok)
        self.assertFalse(target.exists())

    def test_path_traversal(self) -> None:
        with self.assertRaises(ValueError):
            validate_repo_path("../etc/passwd", self.repo)
        result = apply_edits(
            [{"path": "../escape.py", "type": "create", "content": "x"}],
            self.repo,
        )
        self.assertFalse(result.ok)

    def test_multiple_ordered_replaces(self) -> None:
        target = self.repo / "multi.py"
        target.write_text("a\nb\nc\nd\n", encoding="utf-8")
        result = apply_edits(
            [
                {"path": "multi.py", "type": "replace", "old_str": "b", "new_str": "B"},
                {"path": "multi.py", "type": "replace", "old_str": "d", "new_str": "D"},
            ],
            self.repo,
        )
        self.assertTrue(result.ok)
        self.assertEqual(target.read_text(encoding="utf-8"), "a\nB\nc\nD\n")


class FileContextTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        (self.repo / "phase3.py").write_text("line1\nline2\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_discover_paths_from_request(self) -> None:
        paths = discover_paths(
            request="Fix SyntaxError in phase3.py line 2",
            plan="",
            rag_context="",
        )
        self.assertIn("phase3.py", paths)

    def test_gather_includes_line_numbers(self) -> None:
        ctx = gather_file_context(
            repo_path=self.repo,
            request="phase3.py",
            plan="",
            rag_context="",
            max_file_chars=8000,
            max_total_chars=40000,
        )
        self.assertIn("phase3.py", ctx)
        self.assertIn("0001|", ctx)


if __name__ == "__main__":
    unittest.main()
