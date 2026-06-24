"""Index REPOS code (excluding SQL-SCRIPTS tree)."""

from __future__ import annotations

from typing import Any

from common.tracing import traceable_rag
from rag.indexers.base import index_files, iter_repo_files
from rag.progress import Reporter


@traceable_rag(name="orion_rag_index_repos", run_type="chain")
def index_repos(
    *,
    repo_filter: str | None = None,
    reset: bool = False,
    incremental: bool = True,
    quiet: bool = False,
) -> dict[str, Any]:
    if not quiet:
        Reporter(quiet=False).line("  repos: scanning REPOS tree (may take a minute)...")
    file_map = iter_repo_files(repo_filter, skip_sql_scripts_tree=True)
    return index_files(
        "repos",
        file_map,
        reset=reset,
        incremental=incremental,
        quiet=quiet,
    )
