"""Chroma client and collection helpers."""

from __future__ import annotations

import hashlib
from typing import Any

import chromadb

from rag.settings import CHROMA_DIR, collection_names


def get_client() -> chromadb.PersistentClient:
    CHROMA_DIR.mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(path=str(CHROMA_DIR))


def get_collection(chroma_name: str):
    client = get_client()
    return client.get_or_create_collection(
        name=chroma_name,
        metadata={"hnsw:space": "cosine"},
    )


def reset_collection(chroma_name: str) -> None:
    client = get_client()
    try:
        client.delete_collection(chroma_name)
    except Exception:
        pass


def chunk_id(collection_key: str, rel_path: str, chunk_index: int) -> str:
    raw = f"{collection_key}:{rel_path}:{chunk_index}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def chroma_metadata(meta: dict[str, Any]) -> dict[str, Any]:
    """Chroma accepts str/int/float/bool only."""
    out: dict[str, Any] = {}
    for key, val in meta.items():
        if val is None:
            continue
        if isinstance(val, (str, int, float, bool)):
            out[key] = val
        elif isinstance(val, list):
            out[key] = ",".join(str(v) for v in val[:20])
        else:
            out[key] = str(val)[:500]
    return out
