#!/usr/bin/env python3
"""Integration tests for plans/ pipeline (plan_io, routing, --plan E2E with mocked LLM)."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

STACK_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(STACK_ROOT))

from codeflow.fix import invoke_fix  # noqa: E402
from codeflow.graph import _route_after_triage  # noqa: E402
from codeflow.nodes import planner_node  # noqa: E402
from codeflow.plan_io import read_plan, resolve_plan_path, write_plan  # noqa: E402


class PlanIoTests(unittest.TestCase):
    def test_write_read_resolve(self) -> None:
        path = write_plan("TEST-REPO", "add logging", "# Goal\n\nAdd a log line.")
        self.assertTrue(path.exists())
        self.assertIn("Add a log line", read_plan(path))
        self.assertEqual(resolve_plan_path(f"plans/{path.name}"), path.resolve())
        self.assertEqual(resolve_plan_path(path.name), path.resolve())
        path.unlink()

    def test_resolve_missing_raises(self) -> None:
        with self.assertRaises(FileNotFoundError):
            resolve_plan_path("plans/does-not-exist__x__20990101-000000.md")


class RoutingTests(unittest.TestCase):
    def test_preloaded_plan_skips_planner(self) -> None:
        route = _route_after_triage(
            {"plan_path": "/tmp/plans/foo.md", "plan": "# Plan", "complexity": "complex"}
        )
        self.assertEqual(route, "coder")

    def test_complex_without_plan_goes_to_planner(self) -> None:
        route = _route_after_triage({"complexity": "complex"})
        self.assertEqual(route, "planner")

    def test_simple_goes_to_coder(self) -> None:
        route = _route_after_triage({"complexity": "simple"})
        self.assertEqual(route, "coder")


class PlannerNodeTests(unittest.TestCase):
    def test_planner_writes_plan_file(self) -> None:
        fake_plan = "# Goal\n\nTest planner persistence.\n"
        state = {
            "repo": "E2E-TEST",
            "request": "persist plan file",
            "rag_context": "",
            "db_context": "",
            "review_feedback": "",
            "test_feedback": "",
        }
        with patch("codeflow.nodes._llm", return_value=fake_plan):
            out = planner_node(state)
        self.assertIn("Test planner persistence", out["plan"])
        plan_path = Path(out["plan_path"])
        try:
            self.assertTrue(plan_path.is_file())
            self.assertIn("Test planner persistence", read_plan(plan_path))
            self.assertIn("E2E-TEST", plan_path.name)
        finally:
            if plan_path.is_file():
                plan_path.unlink()


class PlanFlagE2ETests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo_path = Path(self.tmp.name) / "fixture-repo"
        self.repo_path.mkdir()
        self._init_git_repo(self.repo_path)
        target = self.repo_path / "hello.py"
        target.write_text('def greet():\n    return "hello"\n', encoding="utf-8")
        subprocess.run(["git", "add", "hello.py"], cwd=self.repo_path, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=self.repo_path, check=True)

        self.plan_path = write_plan(
            "FIXTURE-REPO",
            "add docstring",
            (
                "# Goal\n\nAdd a module docstring to hello.py.\n\n"
                "## Files\n\n- hello.py\n"
            ),
        )

    def tearDown(self) -> None:
        if self.plan_path.is_file():
            self.plan_path.unlink()
        self.tmp.cleanup()

    @staticmethod
    def _init_git_repo(path: Path) -> None:
        subprocess.run(["git", "init", "-b", "main"], cwd=path, check=True, capture_output=True)
        subprocess.run(["git", "checkout", "-b", "orion"], cwd=path, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "orion@test.local"],
            cwd=path,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Orion Test"],
            cwd=path,
            check=True,
        )

    def test_invoke_fix_with_plan_skips_planner_and_applies_edits(self) -> None:
        coder_payload = {
            "edits": [
                {
                    "path": "hello.py",
                    "type": "replace",
                    "old_str": 'def greet():\n    return "hello"\n',
                    "new_str": '"""Greeting helper."""\n\ndef greet():\n    return "hello"\n',
                }
            ],
            "commit_message": "test: add hello docstring",
        }
        llm_calls: list[str] = []

        def fake_llm(model: str, system: str, user: str) -> str:
            llm_calls.append(system[:40])
            if "classify" in system.lower() or "complexity" in system.lower():
                return json.dumps({"complexity": "complex", "reason": "test"})
            if "Coder agent" in system:
                self.assertIn(str(self.plan_path), user)
                self.assertIn("Add a module docstring", user)
                return json.dumps(coder_payload)
            if "Review" in system or "approved" in system.lower():
                return json.dumps({"approved": True, "feedback": "looks good"})
            if "Planner agent" in system:
                self.fail("Planner should be skipped when --plan is provided")
            return json.dumps({"approved": True, "feedback": "ok"})

        with patch("codeflow.nodes._llm", side_effect=fake_llm):
            with patch("codeflow.nodes.feature_enabled") as mock_feat:
                mock_feat.side_effect = lambda name: name not in (
                    "auto_push_orion",
                    "auto_pr",
                    "rag",
                )
                final = invoke_fix(
                    request="add docstring per plan",
                    repo="FIXTURE-REPO",
                    repo_path=self.repo_path,
                    plan_path=str(self.plan_path),
                    rag_context="",
                    db_context="",
                )

        hello = (self.repo_path / "hello.py").read_text(encoding="utf-8")
        self.assertIn('"""Greeting helper."""', hello)
        self.assertTrue(final.get("approved") or final.get("commit_sha"))
        self.assertEqual(final.get("plan_path"), str(self.plan_path.resolve()))
        self.assertNotIn("Planner agent", " ".join(llm_calls))


class CliPlanFlagTests(unittest.TestCase):
    def test_fix_cli_rejects_missing_plan(self) -> None:
        proc = subprocess.run(
            [
                sys.executable,
                str(STACK_ROOT / "codeflow" / "fix.py"),
                "noop",
                "--repo",
                "DOES-NOT-EXIST",
                "--plan",
                "plans/missing-plan.md",
            ],
            cwd=STACK_ROOT,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(proc.returncode, 0)
        combined = (proc.stderr + proc.stdout).lower()
        self.assertTrue("plan not found" in combined or "repo not found" in combined)


if __name__ == "__main__":
    unittest.main()
