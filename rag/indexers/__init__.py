"""Collection-specific indexers."""

from rag.indexers.repos import index_repos
from rag.indexers.docs import index_docs
from rag.indexers.sql_scripts import index_sql
from rag.indexers.playbooks import index_playbooks
from rag.indexers.discrepancy_indexer import index_discrepancies

__all__ = [
    "index_repos",
    "index_docs",
    "index_sql",
    "index_playbooks",
    "index_discrepancies",
]
