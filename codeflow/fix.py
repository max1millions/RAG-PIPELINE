#!/usr/bin/env python3
"""Unified fix entry: RAG + DB context + LangGraph code-change workflow."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

STACK_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(STACK_ROOT))

from codeflow.db_context import gather as gather_db_context  # noqa: E402
from codeflow.graph import build_graph  # noqa: E402
from codeflow.plan_io import read_plan, resolve_plan_path  # noqa: E402
from common.config import load_config, require_feature  # noqa: E402
from incidents.fsm import load_active, resolve_by_prefix  # noqa: E402
from rag.retrieve import detect_intent, retrieve, retrieve_to_context_block  # noqa: E402


def _build_rag_context(
    request: str,
    repo: str,
    *,
    skip: bool = False,
    incident_fingerprint: str = "",
) -> str:
    if skip or not load_config().get("features", {}).get("rag"):
        return ""
    intent = "incident" if incident_fingerprint else detect_intent(request)
    if intent == "general" and any(
        w in request.lower() for w in ("config", "env", "readme", "mismatch", "inconsistent")
    ):
        intent = "discrepancy"
    all_hits = []
    for query in (request[:500], f"entry point related to: {request[:200]}"):
        try:
            all_hits.extend(
                retrieve(query, repo=repo, k=4, hybrid=False, intent=intent)
            )
        except Exception as exc:
            return f"(RAG unavailable: {exc})"
    # Distance gate, path dedupe, and early-stop happen inside context assembly.
    return retrieve_to_context_block(all_hits)


def _prompt_from_incident(record: dict[str, Any]) -> tuple[str, str, str]:
    repo = str(record.get("repos_name") or "UNKNOWN")
    rel = str(record.get("repos_rel_path") or "")
    message = str(record.get("message") or "")
    stack = str(record.get("stack_trace") or record.get("raw_stderr_tail") or "")
    fp = str(record.get("fingerprint") or "")[:8]

    parts = [
        f"Fix production failure (incident ref {fp}).",
        f"Repo: {repo}.",
    ]
    if rel:
        parts.append(f"Primary file hint: {rel}.")
    if message:
        parts.append(f"Error: {message}.")
    if stack:
        parts.append(f"Stack trace / stderr:\n{stack[:3000]}")
    return "\n".join(parts), repo, str(record.get("fingerprint") or "")


def _maybe_reindex_after_fix(repo: str, final: dict[str, Any]) -> None:
    """Best-effort incremental reindex of the touched repo after a commit.

    Never raises — a reindex failure must not affect the fix result the
    caller already has in hand.
    """
    if not final.get("commit_sha"):
        return
    cfg = load_config()
    if not cfg.get("features", {}).get("rag"):
        return
    if not (cfg.get("rag") or {}).get("index_on_fix", True):
        return
    try:
        from rag.index import index_all

        cols = tuple((cfg.get("rag") or {}).get("index_on_fix_collections") or ["repos", "docs"])
        index_all(repo_filter=repo, incremental=True, collections=cols, quiet=True)
    except Exception:
        pass


def _resolve_incident(prefix: str) -> dict[str, Any]:
    active = load_active()
    prefix_lower = prefix.lower()
    matches = [
        rec
        for fp, rec in active.get("incidents", {}).items()
        if fp.lower().startswith(prefix_lower)
    ]
    if len(matches) != 1:
        raise ValueError(f"expected 1 incident for prefix {prefix!r}, got {len(matches)}")
    return matches[0]


def invoke_fix(
    *,
    request: str,
    repo: str,
    repo_path: Path,
    push: bool = False,
    no_rag: bool = False,
    test_cmd: str | None = None,
    test_file: str | None = None,
    plan_path: str | None = None,
    db_context: str | None = None,
    rag_context: str | None = None,
    incident_fingerprint: str = "",
) -> dict[str, Any]:
    require_feature("langgraph_multiagent", "LangGraph multi-agent")

    if rag_context is None:
        rag_context = _build_rag_context(
            request,
            repo,
            skip=no_rag,
            incident_fingerprint=incident_fingerprint,
        )
    if db_context is None:
        db_context = gather_db_context(repo, request, rag_context=rag_context or "")

    cfg = load_config()
    max_iter = int(cfg.get("limits", {}).get("max_review_iterations", 3))
    initial: dict[str, Any] = {
        "request": request,
        "repo": repo,
        "repo_path": str(repo_path),
        "iteration": 0,
        "max_iterations": max_iter,
        "approved": False,
        "pushed": False,
        "rag_context": rag_context or "",
        "db_context": db_context or "",
        "incident_fingerprint": incident_fingerprint,
        "force_push": push,
    }
    if test_cmd:
        initial["test_cmd_override"] = test_cmd
    if test_file:
        initial["test_file"] = test_file
    if plan_path:
        resolved = resolve_plan_path(plan_path)
        initial["plan_path"] = str(resolved)
        initial["plan"] = read_plan(resolved)
        initial["complexity"] = "complex"

    graph = build_graph()
    final = graph.invoke(initial)
    _maybe_reindex_after_fix(repo, final)
    return final


def _result_status(final: dict[str, Any]) -> str:
    if final.get("needs_clarification") or final.get("status") == "needs_clarification":
        return "needs_clarification"
    if final.get("approved") or final.get("commit_sha"):
        return "success"
    return "failed"


def _json_safe(final: dict[str, Any]) -> dict[str, Any]:
    safe = {
        k: v
        for k, v in final.items()
        if k not in ("rag_context", "plan", "coder_output")
    }
    status = _result_status(final)
    safe["status"] = status
    safe["needs_clarification"] = status == "needs_clarification"
    questions = final.get("clarifying_questions") or []
    if not isinstance(questions, list):
        questions = [questions] if questions else []
    safe["clarifying_questions"] = questions
    return safe


def main() -> int:
    parser = argparse.ArgumentParser(description="Orion fix runner (RAG + DB + LangGraph)")
    parser.add_argument("request", nargs="?", help="Natural language fix request")
    parser.add_argument("--repo", help="REPOS subdirectory name")
    parser.add_argument("--from-incident", dest="from_incident", help="Incident fingerprint prefix")
    parser.add_argument("--push", action="store_true", help="Push to origin/orion after commit")
    parser.add_argument("--no-rag", action="store_true", help="Skip upfront RAG queries")
    parser.add_argument("--test-cmd", help="Override test command")
    parser.add_argument("--test-file", help="Run pytest/syntax on a specific file")
    parser.add_argument("--plan", help="Existing plan under plans/ — skips Opus planner")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    cfg = load_config()
    repos = Path(cfg["paths"]["repos"])

    incident_fp = ""
    if args.from_incident:
        try:
            record = _resolve_incident(args.from_incident)
        except ValueError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        request, repo, incident_fp = _prompt_from_incident(record)
    else:
        if not args.request or not args.repo:
            print("ERROR: provide REQUEST and --repo, or --from-incident", file=sys.stderr)
            return 1
        request = args.request
        repo = args.repo

    repo_path = repos / repo
    if not repo_path.is_dir():
        print(f"ERROR: repo not found: {repo_path}", file=sys.stderr)
        return 1
    if not (repo_path / ".git").exists():
        print(f"ERROR: {repo_path} is not a git repo", file=sys.stderr)
        return 1

    try:
        final = invoke_fix(
            request=request,
            repo=repo,
            repo_path=repo_path,
            push=args.push,
            no_rag=args.no_rag,
            test_cmd=args.test_cmd,
            test_file=args.test_file,
            incident_fingerprint=incident_fp,
            plan_path=args.plan,
        )
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    status = _result_status(final)
    success = status == "success"

    if args.json:
        print(json.dumps(_json_safe(final), indent=2, default=str))
    else:
        if status == "needs_clarification":
            print(final.get("summary") or "Needs clarification before implementing.")
            for q in final.get("clarifying_questions") or []:
                print(f"- {q}")
        else:
            print(final.get("summary") or final.get("error") or "Done.")
            if final.get("pr_url"):
                print(f"PR: {final['pr_url']}")

    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
