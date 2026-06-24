"""Index SQL-SCRIPTS catalog."""

from __future__ import annotations

from typing import Any

from common.tracing import traceable_rag
from rag.indexers.base import index_files, iter_repo_files


@traceable_rag(name="orion_rag_index_sql", run_type="chain")
def index_sql(
    *,
    reset: bool = False,
    incremental: bool = True,
    quiet: bool = False,
) -> dict[str, Any]:
    file_map = iter_repo_files(sql_only=True)
    return index_files(
        "sql",
        file_map,
        reset=reset,
        incremental=incremental,
        quiet=quiet,
    )
