"""Index incident playbooks from active.json."""

from __future__ import annotations

from typing import Any

from common.tracing import traceable_rag
from rag.chroma_store import chunk_id, chroma_metadata, get_collection
from rag.indexers.base import upsert_batches, write_bm25_corpus
from rag.settings import collection_names

try:
    from incidents.fsm import load_active
except ImportError:
    load_active = None  # type: ignore


def _record_to_doc(record: dict[str, Any]) -> str:
    parts = [
        f"Incident fingerprint: {record.get('fingerprint', '')[:16]}",
        f"Repo: {record.get('repos_name', '')}",
        f"Path: {record.get('repos_rel_path', '')}",
        f"State: {record.get('state', '')}",
        f"Message: {record.get('message', '')}",
    ]
    stack = record.get("stack_trace") or record.get("raw_stderr_tail") or ""
    if stack:
        parts.append(f"Stack:\n{stack[:2500]}")
    if record.get("fix_summary"):
        parts.append(f"Fix summary: {record.get('fix_summary')}")
    if record.get("fix_pr_url"):
        parts.append(f"PR: {record.get('fix_pr_url')}")
    return "\n".join(parts)


def playbook_records() -> list[dict[str, Any]]:
    if load_active is None:
        return []
    active = load_active()
    states = {"FIXED", "RESOLVED"}
    return [r for r in active.get("incidents", {}).values() if r.get("state") in states]


@traceable_rag(name="orion_rag_index_playbooks", run_type="chain")
def index_playbooks(*, reset: bool = False, quiet: bool = False) -> dict[str, Any]:
    from rag.chroma_store import reset_collection
    from rag.manifest import clear_collection_manifest

    collection_key = "playbooks"
    chroma_name = collection_names()[collection_key]

    if reset:
        reset_collection(chroma_name)
        clear_collection_manifest(collection_key)

    records = playbook_records()
    collection = get_collection(chroma_name)
    ids: list[str] = []
    documents: list[str] = []
    metadatas: list[dict] = []

    for i, rec in enumerate(records):
        fp = str(rec.get("fingerprint") or f"unknown_{i}")
        rel = f"playbook_{fp[:12]}.md"
        doc = _record_to_doc(rec)
        meta = chroma_metadata(
            {
                "repo": str(rec.get("repos_name") or "unknown"),
                "path": rel,
                "chunk": 0,
                "kind": "playbook",
                "fingerprint": fp[:32],
            }
        )
        ids.append(chunk_id(collection_key, rel, 0))
        documents.append(doc)
        metadatas.append(meta)

    count = upsert_batches(collection, ids, documents, metadatas)
    write_bm25_corpus(collection_key, ids, documents, metadatas)

    if not quiet:
        from rag.progress import Reporter

        Reporter(quiet=False).line(f"  playbooks: {len(records)} records, {count} chunks")

    return {"collection": collection_key, "files_indexed": len(records), "chunks": count}


def upsert_record(record: dict[str, Any]) -> None:
    """Single playbook upsert after successful fix."""
    collection_key = "playbooks"
    chroma_name = collection_names()[collection_key]
    collection = get_collection(chroma_name)
    fp = str(record.get("fingerprint") or "unknown")
    rel = f"playbook_{fp[:12]}.md"
    doc = _record_to_doc(record)
    meta = chroma_metadata(
        {
            "repo": str(record.get("repos_name") or "unknown"),
            "path": rel,
            "chunk": 0,
            "kind": "playbook",
            "fingerprint": fp[:32],
        }
    )
    cid = chunk_id(collection_key, rel, 0)
    collection.upsert(ids=[cid], documents=[doc], metadatas=[meta])
