"""Unit tests for Cursor delegation wiring (no live Cursor API)."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from codeflow.cursor_backend import build_brief
from codeflow.graph import build_graph
from codeflow.mac_bridge import resolve_target


class BriefTests(unittest.TestCase):
    def test_brief_includes_constraints(self) -> None:
        brief = build_brief(request="fix x", repo="SCHEMA", repo_path="/tmp/r")
        self.assertIn("do NOT commit", brief)
        self.assertIn(".env", brief)


class ResolveTargetTests(unittest.TestCase):
    def test_explicit_mac(self) -> None:
        self.assertEqual(resolve_target(explicit="mac", request="x", repo="SCHEMA"), "mac")

    def test_other_scripts_auto(self) -> None:
        self.assertEqual(
            resolve_target(explicit="auto", request="fix OTHER/scripts cron", repo="OTHER"),
            "mac",
        )


class CursorGraphTests(unittest.TestCase):
    def test_cursor_node_path_commits(self) -> None:
        td = Path(tempfile.mkdtemp())
        subprocess.run(["git", "init"], cwd=td, capture_output=True, check=True)
        (td / "hello.py").write_text("print(1)\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=td, capture_output=True, check=True)
        subprocess.run(
            ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "init"],
            cwd=td,
            capture_output=True,
            check=True,
        )

        def fake_local(*, brief, cwd, model=None, timeout_s=None, agent_bin=None):
            Path(cwd, "hello.py").write_text("print(2)\n", encoding="utf-8")
            return {
                "ok": True,
                "error": "",
                "changed_files": ["hello.py"],
                "summary": "ok",
                "commit_message": "test",
            }

        with patch("codeflow.nodes.run_cursor_agent_local", side_effect=fake_local):
            final = build_graph().invoke(
                {
                    "request": "bump",
                    "repo": "SMOKE",
                    "repo_path": str(td),
                    "iteration": 0,
                    "max_iterations": 2,
                    "approved": False,
                    "pushed": False,
                    "rag_context": "",
                    "db_context": "",
                    "code_backend": "cursor",
                    "fix_target": "node",
                    "force_push": False,
                }
            )
        self.assertTrue(final.get("approved"))
        self.assertTrue(final.get("commit_sha"))


if __name__ == "__main__":
    unittest.main()
