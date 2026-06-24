"""Index README and top-level repo documentation."""

from __future__ import annotations

from typing import Any

from common.tracing import traceable_rag
from rag.indexers.base import index_files, iter_repo_files
from rag.progress import Reporter


@traceable_rag(name="orion_rag_index_docs", run_type="chain")
def index_docs(
    *,
    repo_filter: str | None = None,
    reset: bool = False,
    incremental: bool = True,
    quiet: bool = False,
) -> dict[str, Any]:
    if not quiet:
        Reporter(quiet=False).line("  docs: scanning README and top-level docs...")
    file_map = iter_repo_files(repo_filter, docs_only=True)
    return index_files(
        "docs",
        file_map,
        reset=reset,
        incremental=incremental,
        quiet=quiet,
    )
