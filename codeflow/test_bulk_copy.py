#!/usr/bin/env python3
"""Unit tests for bulk_copy (no LLM)."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

STACK_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(STACK_ROOT))

from codeflow import bulk_copy  # noqa: E402


class BulkCopyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "rightstune.com"
        self.repo.mkdir()
        (self.repo / "feedback.html").write_text(
            '<a href="mailto:max@rightstune.com">max@rightstune.com</a>\n',
            encoding="utf-8",
        )
        (self.repo / "contact.html").write_text(
            '<a href="mailto:max@rightstune.com">max@rightstune.com</a> or on Instagram @Rightstune\n',
            encoding="utf-8",
        )
        (self.repo / "skip.env").write_text("max@rightstune.com\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_append_skips_already_updated(self) -> None:
        with mock.patch.object(bulk_copy, "REPOS", self.repo.parent):
            rc = bulk_copy.main(
                [
                    "--repo",
                    "rightstune.com",
                    "--mode",
                    "append",
                    "--needle",
                    "max@rightstune.com",
                    "--append",
                    " or on Instagram @Rightstune",
                    "--skip-if-contains",
                    "Instagram",
                    "--apply",
                    "--json",
                ]
            )
        self.assertEqual(rc, 0)
        feedback = (self.repo / "feedback.html").read_text(encoding="utf-8")
        contact = (self.repo / "contact.html").read_text(encoding="utf-8")
        self.assertIn("or on Instagram @Rightstune", feedback)
        self.assertEqual(contact.count("or on Instagram"), 1)
        self.assertFalse((self.repo / "skip.env").read_text(encoding="utf-8").endswith("Instagram\n"))


if __name__ == "__main__":
    unittest.main()
