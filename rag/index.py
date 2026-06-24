#!/usr/bin/env python3
"""Index REPOS into multi-collection Chroma store."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

STACK_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(STACK_ROOT))

from common.config import load_config, require_feature  # noqa: E402
from common.tracing import init_tracing  # noqa: E402
from rag.indexers import (  # noqa: E402
    index_discrepancies,
    index_docs,
    index_playbooks,
    index_repos,
    index_sql,
)
from rag.progress import Reporter  # noqa: E402
from rag.settings import CHROMA_DIR, incremental_enabled, rag_config, repos_root  # noqa: E402

COLLECTION_HANDLERS = {
    "repos": lambda **kw: index_repos(**kw),
    "docs": lambda **kw: index_docs(**kw),
    "sql": lambda **kw: index_sql(**kw),
    "playbooks": lambda **kw: index_playbooks(**kw),
    "discrepancies": lambda **kw: index_discrepancies(**kw),
}

DEFAULT_ORDER = ("repos", "docs", "sql", "playbooks", "discrepancies")


def index_all(
    *,
    repo_filter: str | None = None,
    reset: bool = False,
    incremental: bool | None = None,
    collections: tuple[str, ...] | None = None,
    quiet: bool = False,
) -> int:
    require_feature("rag", "RAG")
    init_tracing()
    inc = incremental_enabled() if incremental is None else incremental
    cols = collections or DEFAULT_ORDER
    cfg = rag_config()

    rep = Reporter(quiet=quiet)
    if not quiet:
        rep.line("")
        rep.line("Orion RAG multi-collection index")
        rep.line("─" * 52)
        rep.line(f"  REPOS:  {repos_root()}")
        rep.line(f"  Store:  {CHROMA_DIR}")
        rep.line(f"  Collections: {', '.join(cols)}")
        rep.line(f"  Incremental: {inc}")
        rep.line("")

    t0 = time.monotonic()
    results: list[dict] = []
    total_cols = len(cols)

    for phase, key in enumerate(cols, start=1):
        if key not in COLLECTION_HANDLERS:
            rep.line(f"Unknown collection: {key}")
            return 1
        if not quiet:
            rep.phase(phase, total_cols, f"Index {key}")
        handler = COLLECTION_HANDLERS[key]
        kwargs: dict = {"reset": reset, "quiet": quiet}
        if key in ("repos", "docs"):
            kwargs["repo_filter"] = repo_filter
            kwargs["incremental"] = inc
        elif key == "sql":
            kwargs["incremental"] = inc
        elif key == "discrepancies":
            if not cfg.get("scan_discrepancies_on_index", True) and not reset:
                continue
        results.append(handler(**kwargs))

    elapsed = time.monotonic() - t0
    if not quiet:
        rep.line("")
        rep.line("─" * 52)
        rep.line(f"Index complete in {elapsed:.1f}s")
        for r in results:
            rep.line(f"  {r.get('collection')}: {r}")
        rep.line("─" * 52)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Index REPOS into Chroma (multi-collection)")
    parser.add_argument("--repo", help="Limit repos/docs indexing to one REPOS subdirectory")
    parser.add_argument("--reset", action="store_true", help="Drop and rebuild selected collection(s)")
    parser.add_argument(
        "--collection",
        action="append",
        dest="collections",
        choices=list(COLLECTION_HANDLERS.keys()),
        help="Index only this collection (repeatable)",
    )
    parser.add_argument(
        "--no-incremental",
        action="store_true",
        help="Re-index all files in collection(s)",
    )
    parser.add_argument("--quiet", action="store_true", help="Minimal output")
    args = parser.parse_args()
    load_config()
    cols = tuple(args.collections) if args.collections else None
    return index_all(
        repo_filter=args.repo,
        reset=args.reset,
        incremental=not args.no_incremental,
        collections=cols,
        quiet=args.quiet,
    )


if __name__ == "__main__":
    raise SystemExit(main())
