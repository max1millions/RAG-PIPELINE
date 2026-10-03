"""Per-chat branch names, checkout, and the pre-push guard."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from codeflow.chat_branch import (
    allocate_session_branch,
    branch_name_for,
    checkout_branch,
    incident_branch,
    merge_branch,
    prepare_repo_branch,
    slugify,
)

STACK_ROOT = Path(__file__).resolve().parent.parent
PRE_PUSH = STACK_ROOT / "scripts" / "openclaw-git-guard" / "pre-push"


def _init_repo(path: Path, *, branch: str = "main") -> None:
    subprocess.run(["git", "init", "-b", branch], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True)
    (path / "hello.txt").write_text("one\n", encoding="utf-8")
    subprocess.run(["git", "add", "hello.txt"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True, capture_output=True)


class NamingTests(unittest.TestCase):
    def test_slug_and_suffix(self) -> None:
        self.assertEqual(slugify("Fix Spotify search widget!!!"), "fix-spotify-search-widget")
        name = branch_name_for("272901a3-8b20-4f59-a19a-560b06d79986", "Fix Spotify search")
        self.assertEqual(name, "cursor/fix-spotify-search-9986")

    def test_same_session_reuses_branch_new_session_does_not(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = str(Path(tmp) / "branches.json")
            env = {"ORION_CHAT_BRANCH_STORE": store}
            with patch.dict(os.environ, env, clear=False):
                first = allocate_session_branch("session-aaa-1111", "Add Spotify search")
                second = allocate_session_branch("session-aaa-1111", "A totally different follow-up")
                new_chat = allocate_session_branch("session-bbb-2222", "Add Spotify search")
        self.assertEqual(first, second)
        self.assertNotEqual(first, new_chat)
        self.assertTrue(first.startswith("cursor/"))
        self.assertTrue(new_chat.startswith("cursor/"))

    def test_incident_branch_ignores_chat(self) -> None:
        self.assertEqual(incident_branch("abcdef1234567890"), "cursor/incident-abcdef12")


class CheckoutTests(unittest.TestCase):
    def test_prepare_creates_branch_from_head_and_reuses_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            _init_repo(repo)
            store = str(Path(tmp) / "branches.json")
            env = {
                "ORION_CHAT_BRANCH_STORE": store,
                "ORION_SESSION_ID": "11111111-2222-3333-4444-55555555abcd",
            }
            with patch.dict(os.environ, env, clear=False):
                first = prepare_repo_branch(repo, request="spotify widget", repo="rightstune.com")
                (repo / "hello.txt").write_text("two\n", encoding="utf-8")
                second = prepare_repo_branch(repo, request="change the color", repo="SCHEMA")
            self.assertTrue(first["ok"], first)
            self.assertEqual(first["branch"], "cursor/spotify-widget-abcd")
            self.assertEqual(second["branch"], first["branch"])
            head = subprocess.run(
                ["git", "branch", "--show-current"],
                cwd=repo,
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertEqual(head.stdout.strip(), first["branch"])
            data = json.loads(Path(store).read_text(encoding="utf-8"))
            repos = data["sessions"]["11111111-2222-3333-4444-55555555abcd"]["repos"]
            self.assertEqual(repos["rightstune.com"], first["branch"])
            self.assertEqual(repos["SCHEMA"], first["branch"])

    def test_main_only_stays_on_main(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            _init_repo(repo)
            subprocess.run(
                ["git", "config", "hooks.allowed-push-branch", "main"],
                cwd=repo,
                check=True,
            )
            result = checkout_branch(repo, "cursor/ignored-abcd", main_only=True)
            self.assertTrue(result["ok"], result)
            self.assertEqual(result["branch"], "main")

    def test_new_session_starts_a_different_branch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            _init_repo(repo)
            store = str(Path(tmp) / "branches.json")
            with patch.dict(os.environ, {"ORION_CHAT_BRANCH_STORE": store}, clear=False):
                with patch.dict(os.environ, {"ORION_SESSION_ID": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeee1111"}):
                    first = prepare_repo_branch(repo, request="one", repo="SCHEMA")
                with patch.dict(os.environ, {"ORION_SESSION_ID": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeee2222"}):
                    second = prepare_repo_branch(repo, request="two", repo="SCHEMA")
            self.assertNotEqual(first["branch"], second["branch"])


class MergeTests(unittest.TestCase):
    def test_merge_refuses_non_cursor_branch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            _init_repo(repo)
            result = merge_branch(repo, repo="SCHEMA", branch="orion")
        self.assertFalse(result["ok"])
        self.assertIn("refusing", result["error"])

    def test_merge_uses_github_pr_merge(self) -> None:
        """Chat branches land on main via GitHub, not a local git merge."""
        from codeflow import chat_branch

        git_calls: list[list[str]] = []
        gh_calls: list[list[str]] = []

        def fake_git(repo: Path, args: list[str], timeout: int = 120):
            git_calls.append(args)
            proc = subprocess.CompletedProcess(args, 0, "", "")
            if args[:2] == ["rev-parse", "--abbrev-ref"]:
                proc.stdout = "cursor/spotify-widget-abcd\n"
            elif args[:2] == ["config", "--get"]:
                proc.stdout = "cursor\n"
            return proc

        def fake_run(cmd, **kwargs):
            gh_calls.append(list(cmd))
            proc = subprocess.CompletedProcess(cmd, 0, "", "")
            joined = " ".join(cmd)
            if cmd[:3] == ["gh", "pr", "view"] and "mergeCommit" in joined:
                proc.stdout = json.dumps(
                    {
                        "state": "MERGED",
                        "baseRefName": "main",
                        "url": "https://github.com/max1millions/SCHEMA/pull/9",
                        "mergeCommit": {"oid": "abc123def4567890"},
                    }
                )
            elif cmd[:3] == ["gh", "pr", "view"]:
                proc.stdout = json.dumps(
                    {
                        "url": "https://github.com/max1millions/SCHEMA/pull/9",
                        "state": "OPEN",
                    }
                )
            elif cmd[:3] == ["gh", "pr", "merge"]:
                proc.stdout = ""
            return proc

        with patch("codeflow.chat_branch.git_run", side_effect=fake_git):
            with patch("codeflow.chat_branch.subprocess.run", side_effect=fake_run):
                result = chat_branch.merge_branch(
                    Path("/tmp/schema"),
                    repo="SCHEMA",
                    branch="cursor/spotify-widget-abcd",
                    title="spotify",
                )
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["merged"])
        self.assertEqual(result["merge_sha"], "abc123def4567890")
        self.assertIn("on GitHub", result["summary"])
        merge_cmds = [c for c in gh_calls if c[:3] == ["gh", "pr", "merge"]]
        self.assertEqual(len(merge_cmds), 1)
        self.assertIn("--merge", merge_cmds[0])
        local_feature_merges = [
            args
            for args in git_calls
            if args and args[0] == "merge" and "cursor/spotify-widget-abcd" in args
        ]
        self.assertEqual(local_feature_merges, [])
        self.assertIn(["merge", "--ff-only", "origin/main"], git_calls)


class PrePushHookTests(unittest.TestCase):
    def _run(self, repo: Path, remote_ref: str) -> subprocess.CompletedProcess[str]:
        payload = f"refs/heads/local abc {remote_ref} def\n"
        return subprocess.run(
            ["sh", str(PRE_PUSH)],
            cwd=repo,
            input=payload,
            capture_output=True,
            text=True,
        )

    def test_chat_repo_allows_cursor_and_blocks_main_and_orion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            _init_repo(repo)
            subprocess.run(["git", "config", "hooks.allowed-push-branch", "cursor"], cwd=repo, check=True)
            ok = self._run(repo, "refs/heads/cursor/spotify-widget-9986")
            blocked_main = self._run(repo, "refs/heads/main")
            blocked_orion = self._run(repo, "refs/heads/orion")
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertNotEqual(blocked_main.returncode, 0)
        self.assertIn("BLOCKED", blocked_main.stderr)
        self.assertNotEqual(blocked_orion.returncode, 0)

    def test_main_only_allows_only_main(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            _init_repo(repo)
            subprocess.run(["git", "config", "hooks.allowed-push-branch", "main"], cwd=repo, check=True)
            ok = self._run(repo, "refs/heads/main")
            blocked = self._run(repo, "refs/heads/cursor/spotify-widget-9986")
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertNotEqual(blocked.returncode, 0)


if __name__ == "__main__":
    unittest.main()
