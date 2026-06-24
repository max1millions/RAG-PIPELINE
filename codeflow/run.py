#!/usr/bin/env python3
"""CLI entrypoint for LangGraph code-change workflow."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

STACK_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(STACK_ROOT))

from codeflow.fix import invoke_fix  # noqa: E402
from common.config import load_config, require_feature  # noqa: E402


def main() -> int:
    require_feature("langgraph_multiagent", "LangGraph multi-agent")

    parser = argparse.ArgumentParser(description="Orion LangGraph code change runner")
    parser.add_argument("request", help="Natural language code change request")
    parser.add_argument("--repo", required=True, help="REPOS subdirectory name, e.g. CWR-INTERFACE")
    parser.add_argument("--push", action="store_true", help="Push to origin/orion after commit")
    parser.add_argument("--test-cmd", help="Override test command")
    parser.add_argument("--test-file", help="Run tests on a specific file")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    cfg = load_config()
    repos = Path(cfg["paths"]["repos"])
    repo_path = repos / args.repo
    if not repo_path.is_dir():
        print(f"ERROR: repo not found: {repo_path}", file=sys.stderr)
        return 1

    if not (repo_path / ".git").exists():
        print(f"ERROR: {repo_path} is not a git repo", file=sys.stderr)
        return 1

    try:
        final = invoke_fix(
            request=args.request,
            repo=args.repo,
            repo_path=repo_path,
            push=args.push,
            no_rag=False,
            test_cmd=args.test_cmd,
            test_file=args.test_file,
        )
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if args.json:
        safe = {k: v for k, v in final.items() if k not in ("rag_context", "plan", "coder_output")}
        print(json.dumps(safe, indent=2, default=str))
    else:
        print(final.get("summary") or final.get("error") or "Done.")
        if final.get("plan") and not final.get("approved"):
            print("\nPlan excerpt:")
            print(str(final["plan"])[:500])
        if final.get("pr_url"):
            print(f"PR: {final['pr_url']}")

    return 0 if final.get("approved") or final.get("commit_sha") else 1


if __name__ == "__main__":
    raise SystemExit(main())
