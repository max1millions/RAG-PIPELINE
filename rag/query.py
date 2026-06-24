#!/usr/bin/env python3
"""Query Chroma RAG index (CLI wrapper around retrieve)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

STACK_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(STACK_ROOT))

from common.config import load_config, require_feature  # noqa: E402
from common.tracing import init_tracing  # noqa: E402
from rag.retrieve import Hit, detect_intent, retrieve  # noqa: E402
from rag.settings import CHROMA_DIR, top_k_default  # noqa: E402


def query_rag(
    question: str,
    *,
    repo: str | None = None,
    k: int | None = None,
) -> list[dict]:
    """Backward-compatible alias: repos collection only."""
    hits = retrieve(
        question,
        repo=repo,
        k=k,
        collections=("repos",),
        hybrid=False,
        intent="general",
    )
    return [h.to_dict() for h in hits]


def main() -> int:
    parser = argparse.ArgumentParser(description="Query codebase RAG index")
    parser.add_argument("question", help="Natural language query")
    parser.add_argument("--repo", help="Filter to one REPOS subdirectory")
    parser.add_argument("--k", type=int, help="Number of chunks to return")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument(
        "--hybrid",
        action="store_true",
        help="Opt-in BM25+vector fusion (loads BM25 corpus into RAM)",
    )
    parser.add_argument(
        "--intent",
        choices=["auto", "general", "discrepancy", "incident"],
        default="auto",
        help="Retrieval intent preset",
    )
    parser.add_argument(
        "--collection",
        action="append",
        dest="collections",
        help="Collection key: repos, docs, sql, playbooks, discrepancies",
    )
    args = parser.parse_args()

    load_config()
    init_tracing()
    require_feature("rag", "RAG")

    if not CHROMA_DIR.exists():
        print(f"ERROR: Chroma store missing at {CHROMA_DIR}. Run bin/orion-rag-index first.", file=sys.stderr)
        return 1

    cols = tuple(args.collections) if args.collections else None
    intent = args.intent
    if intent == "auto":
        intent = detect_intent(args.question)

    try:
        hits = retrieve(
            args.question,
            repo=args.repo,
            k=args.k or top_k_default(),
            collections=cols,
            hybrid=args.hybrid,
            intent=intent,
        )
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps([h.to_dict() for h in hits], indent=2))
        return 0

    if not hits:
        print("(no results)")
        return 0

    for i, hit in enumerate(hits, 1):
        path = hit.path
        if hit.repo and not path.startswith(f"{hit.repo}/"):
            display_path = f"{hit.repo}/{path}"
        else:
            display_path = path
        print(
            f"--- [{i}] [{hit.collection}] {display_path} "
            f"(chunk {hit.chunk}, d={hit.distance:.4f}, kind={hit.kind}) ---"
        )
        print(hit.text[:800])
        if len(hit.text) > 800:
            print("...")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
