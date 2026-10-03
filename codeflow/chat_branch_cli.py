"""CLI for per-chat branches: ensure a checkout, or merge into main."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from codeflow.chat_branch import (
    current_session_id,
    merge_branch,
    prepare_repo_branch,
    session_repo_branches,
)
from common.config import load_config


def _repo_path(name: str) -> Path:
    cfg = load_config()
    path = Path(cfg["paths"]["repos"]) / name
    if not path.is_dir() or not (path / ".git").exists():
        raise SystemExit(f"ERROR: repo not found: {path}")
    return path


def _print(result: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(result.get("summary") or result.get("branch") or result.get("error") or result)


def cmd_branch(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Check out this chat's git branch")
    parser.add_argument("action", nargs="?", default="ensure", choices=("ensure",))
    parser.add_argument("--repo", required=True)
    parser.add_argument("--request", default="change", help="Used to name the branch on first use")
    parser.add_argument("--session-id", default="")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    result = prepare_repo_branch(
        _repo_path(args.repo),
        request=args.request,
        repo=args.repo,
        session_id=args.session_id,
    )
    _print(result, args.json)
    return 0 if result.get("ok") else 1


def cmd_merge(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Merge this chat's branch into main")
    parser.add_argument("--repo", default="")
    parser.add_argument("--all", action="store_true", help="Merge every repo touched in this chat")
    parser.add_argument("--branch", default="", help="Branch to merge (default: this chat's branch)")
    parser.add_argument("--session-id", default="")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    session_id = (args.session_id or "").strip() or current_session_id()
    targets: list[tuple[str, str]] = []
    if args.all:
        if not session_id:
            print("ERROR: no OpenClaw session id", file=sys.stderr)
            return 1
        mapping = session_repo_branches(session_id)
        if args.repo:
            mapping = {args.repo: mapping.get(args.repo) or args.branch}
        if not mapping:
            print("ERROR: this chat has not pushed a branch yet", file=sys.stderr)
            return 1
        targets = [(repo, branch) for repo, branch in mapping.items() if branch]
    elif args.repo:
        branch = args.branch
        if not branch:
            if not session_id:
                print("ERROR: no OpenClaw session id and no --branch", file=sys.stderr)
                return 1
            branch = session_repo_branches(session_id).get(args.repo, "")
        if not branch:
            print(
                f"ERROR: no chat branch recorded for {args.repo}. Pass --branch.",
                file=sys.stderr,
            )
            return 1
        targets = [(args.repo, branch)]
    else:
        print("ERROR: pass --repo NAME or --all", file=sys.stderr)
        return 1

    results = []
    failed = False
    for repo, branch in targets:
        result = merge_branch(
            _repo_path(repo),
            repo=repo,
            branch=branch,
            session_id=session_id,
            title=f"{repo}: merge {branch}",
        )
        results.append(result)
        if not result.get("ok"):
            failed = True
    payload = {"ok": not failed, "results": results}
    if args.json:
        print(json.dumps(payload, indent=2, default=str))
    else:
        for result in results:
            print(result.get("summary") or result.get("error"))
    return 1 if failed else 0


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] not in ("branch", "merge"):
        print("usage: chat_branch_cli.py branch|merge ...", file=sys.stderr)
        return 2
    action = sys.argv[1]
    rest = sys.argv[2:]
    if action == "branch":
        return cmd_branch(rest)
    return cmd_merge(rest)


if __name__ == "__main__":
    raise SystemExit(main())
