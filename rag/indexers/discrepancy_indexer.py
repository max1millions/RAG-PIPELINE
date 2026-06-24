"""Index discrepancy scan results."""

from __future__ import annotations

from typing import Any

from common.tracing import traceable_rag
from rag.chroma_store import get_collection, reset_collection
from rag.discrepancy import findings_to_index_docs, scan_all_repos
from rag.indexers.base import upsert_batches, write_bm25_corpus
from rag.manifest import clear_collection_manifest
from rag.settings import collection_names


@traceable_rag(name="orion_rag_index_discrepancies", run_type="chain")
def index_discrepancies(*, reset: bool = False, quiet: bool = False) -> dict[str, Any]:
    collection_key = "discrepancies"
    chroma_name = collection_names()[collection_key]

    if reset:
        reset_collection(chroma_name)
        clear_collection_manifest(collection_key)

    findings = scan_all_repos()
    ids, documents, metadatas = findings_to_index_docs(findings)
    collection = get_collection(chroma_name)
    count = upsert_batches(collection, ids, documents, metadatas)
    write_bm25_corpus(collection_key, ids, documents, metadatas)

    if not quiet:
        from rag.progress import Reporter

        Reporter(quiet=False).line(f"  discrepancies: {len(findings)} findings indexed")

    return {
        "collection": collection_key,
        "findings": len(findings),
        "chunks": count,
    }
