"""Unified retrieval API with opt-in hybrid search and intent presets."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any

from common.config import load_config, require_feature
from common.tracing import traceable_rag
from rag.chroma_store import get_collection
from rag.settings import (
    BM25_CORPUS_DIR,
    bm25_top_k_default,
    collection_names,
    hybrid_enabled_by_config,
    rag_config,
    top_k_default,
)

DISCREPANCY_KEYWORDS = re.compile(
    r"\b(discrepanc|inconsisten|mismatch|out of sync|contradict|doesn'?t match|drift)\b",
    re.I,
)
DB_KEYWORDS = re.compile(
    r"\b(sql|schema|mysql|table|database|column|migration|ddl)\b",
    re.I,
)


@dataclass
class Hit:
    repo: str | None
    path: str
    chunk: int
    distance: float
    text: str
    collection: str = "repos"
    kind: str = ""
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "repo": self.repo,
            "path": self.path,
            "chunk": self.chunk,
            "distance": self.distance,
            "text": self.text,
            "collection": self.collection,
            "kind": self.kind,
            **self.extra,
        }


def _use_hybrid(explicit: bool | None) -> bool:
    if explicit is not None:
        return explicit
    if os.environ.get("ORION_RAG_HYBRID", "").lower() in ("1", "true", "yes"):
        return True
    return hybrid_enabled_by_config()


def detect_intent(question: str, explicit: str | None = None) -> str:
    if explicit and explicit != "auto":
        return explicit
    if DISCREPANCY_KEYWORDS.search(question):
        return "discrepancy"
    return "general"


def collections_for_intent(intent: str, question: str) -> tuple[str, ...]:
    if intent == "discrepancy":
        return ("discrepancies", "docs", "repos")
    if intent == "incident":
        return ("playbooks", "docs", "repos")
    cols: list[str] = ["repos", "docs"]
    if DB_KEYWORDS.search(question):
        cols.append("sql")
    return tuple(cols)


def _boost_factor(kind: str) -> float:
    cfg = rag_config()
    if kind == "readme" or kind == "doc":
        return float(cfg.get("readme_boost", 1.35))
    if kind == "discrepancy":
        return float(cfg.get("discrepancy_boost", 1.25))
    return 1.0


def _vector_hits(
    question: str,
    collection_key: str,
    *,
    repo: str | None,
    k: int,
) -> list[Hit]:
    names = collection_names()
    chroma_name = names.get(collection_key, collection_key)
    try:
        collection = get_collection(chroma_name)
    except Exception:
        return []

    where = None
    if repo and collection_key in ("repos", "docs"):
        where = {"repo": repo}
    elif collection_key == "sql":
        where = {"repo": "SQL-SCRIPTS"}

    try:
        result = collection.query(
            query_texts=[question],
            n_results=k,
            where=where,
        )
    except Exception:
        result = collection.query(query_texts=[question], n_results=k)

    hits: list[Hit] = []
    docs = result.get("documents", [[]])[0]
    metas = result.get("metadatas", [[]])[0]
    dists = result.get("distances", [[]])[0]
    for doc, meta, dist in zip(docs, metas, dists):
        if not doc:
            continue
        kind = str((meta or {}).get("kind", ""))
        score = float(dist) if dist is not None else 1.0
        score /= _boost_factor(kind)
        hits.append(
            Hit(
                repo=(meta or {}).get("repo"),
                path=str((meta or {}).get("path", "")),
                chunk=int((meta or {}).get("chunk", 0)),
                distance=score,
                text=doc,
                collection=collection_key,
                kind=kind,
            )
        )
    return hits


def _bm25_hits(question: str, collection_key: str, *, k: int) -> list[Hit]:
    names = collection_names()
    chroma_name = names.get(collection_key, collection_key)
    path = BM25_CORPUS_DIR / f"{chroma_name}.json"
    if not path.is_file():
        return []
    try:
        from rank_bm25 import BM25Okapi
    except ImportError:
        return []

    entries = json.loads(path.read_text(encoding="utf-8"))
    if not entries:
        return []

    corpus = [e["text"] for e in entries]
    tokenized = [doc.lower().split() for doc in corpus]
    bm25 = BM25Okapi(tokenized)
    scores = bm25.get_scores(question.lower().split())
    ranked = sorted(enumerate(scores), key=lambda x: -x[1])[:k]

    hits: list[Hit] = []
    for idx, score in ranked:
        if score <= 0:
            continue
        entry = entries[idx]
        meta = entry.get("metadata") or {}
        kind = str(meta.get("kind", ""))
        hits.append(
            Hit(
                repo=meta.get("repo"),
                path=str(meta.get("path", "")),
                chunk=int(meta.get("chunk", 0)),
                distance=1.0 / (1.0 + float(score)),
                text=entry["text"],
                collection=collection_key,
                kind=kind,
            )
        )
    return hits


def _rrf_merge(vector: list[Hit], bm25: list[Hit], k: int = 60) -> list[Hit]:
    scores: dict[tuple[str, str, int], float] = {}
    items: dict[tuple[str, str, int], Hit] = {}

    for rank, hit in enumerate(vector):
        key = (hit.collection, hit.path, hit.chunk)
        scores[key] = scores.get(key, 0) + 1.0 / (k + rank + 1)
        items[key] = hit

    for rank, hit in enumerate(bm25):
        key = (hit.collection, hit.path, hit.chunk)
        scores[key] = scores.get(key, 0) + 1.0 / (k + rank + 1)
        if key not in items:
            items[key] = hit

    merged = sorted(scores.items(), key=lambda x: -x[1])
    out: list[Hit] = []
    for key, _score in merged:
        hit = items[key]
        out.append(hit)
    return out


@traceable_rag(name="orion_rag_retrieve", run_type="retriever")
def retrieve(
    question: str,
    *,
    repo: str | None = None,
    k: int | None = None,
    collections: tuple[str, ...] | None = None,
    hybrid: bool | None = None,
    intent: str = "auto",
) -> list[Hit]:
    require_feature("rag", "RAG")
    k = k or top_k_default()
    intent_resolved = detect_intent(question, intent)
    cols = collections or collections_for_intent(intent_resolved, question)

    per_col = max(2, k // max(len(cols), 1) + 1)
    all_vector: list[Hit] = []
    all_bm25: list[Hit] = []

    use_hybrid = _use_hybrid(hybrid)

    for col in cols:
        all_vector.extend(_vector_hits(question, col, repo=repo, k=per_col))
        if use_hybrid:
            all_bm25.extend(_bm25_hits(question, col, k=bm25_top_k_default()))

    if use_hybrid and all_bm25:
        merged = _rrf_merge(all_vector, all_bm25)
    else:
        merged = sorted(all_vector, key=lambda h: h.distance)

    # When repo set, ensure top docs hits included
    if repo and "docs" in cols:
        doc_hits = _vector_hits(question, "docs", repo=repo, k=2)
        seen = {(h.collection, h.path, h.chunk) for h in merged}
        for h in doc_hits:
            key = (h.collection, h.path, h.chunk)
            if key not in seen:
                merged.insert(0, h)
                seen.add(key)

    return merged[:k]


def retrieve_to_context_block(hits: list[Hit], *, max_chars: int = 1200) -> str:
    blocks: list[str] = []
    for h in hits:
        path = h.path
        if h.repo and not path.startswith(f"{h.repo}/"):
            path = f"{h.repo}/{path}"
        block = f"### [{h.collection}] {path} (chunk {h.chunk})\n{h.text[:max_chars]}"
        if block not in blocks:
            blocks.append(block)
    return "\n\n".join(blocks)
