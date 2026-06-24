"""Shared indexing utilities."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from rag.chunking import Chunk, chunk_file
from rag.chroma_store import chunk_id, chroma_metadata, get_collection, reset_collection
from rag.manifest import clear_collection_manifest, files_needing_reindex, update_manifest_files
from rag.progress import Reporter
from rag.redaction import redact, should_skip_file
from rag.settings import (
    BM25_CORPUS_DIR,
    INDEXABLE_EXTENSIONS,
    MAX_FILE_BYTES,
    collection_names,
    exclude_dirs,
    repos_root,
)

BATCH_SIZE = 500


def _should_skip(path: Path, excludes: set[str]) -> bool:
    return bool(set(path.parts) & excludes)


def file_to_chunks(
    path: Path,
    repos: Path,
    collection_key: str,
) -> tuple[list[str], list[str], list[dict], str]:
    if should_skip_file(path.name):
        return [], [], [], ""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return [], [], [], ""
    text = redact(text)
    rel = str(path.relative_to(repos))
    repo_name = rel.split("/")[0] if "/" in rel else "unknown"
    chunks = chunk_file(path, text, repos_root_path=repos)
    ids: list[str] = []
    documents: list[str] = []
    metadatas: list[dict] = []
    for ch in chunks:
        cid = chunk_id(collection_key, rel, ch.index)
        meta = chroma_metadata(
            {
                "repo": repo_name,
                "path": rel,
                "chunk": ch.index,
                **ch.extra,
            }
        )
        ids.append(cid)
        documents.append(ch.text)
        metadatas.append(meta)
    return ids, documents, metadatas, rel


def delete_paths_from_collection(collection, rel_paths: list[str], collection_key: str) -> None:
    if not rel_paths:
        return
    data = load_manifest_ids_for_paths(collection_key, rel_paths)
    if data:
        try:
            collection.delete(ids=data)
        except Exception:
            pass


def load_manifest_ids_for_paths(collection_key: str, rel_paths: list[str]) -> list[str]:
    """Best-effort: delete by querying existing chunks for paths (re-upsert replaces ids)."""
    from rag.manifest import load_manifest

    manifest = load_manifest()
    files = manifest.get("collections", {}).get(collection_key, {}).get("files", {})
    ids: list[str] = []
    for rel in rel_paths:
        if rel in files:
            # We don't store per-chunk ids in manifest; caller should delete by prefix via get
            pass
    return ids


def upsert_batches(
    collection,
    ids: list[str],
    documents: list[str],
    metadatas: list[dict],
    *,
    reporter: Reporter | None = None,
) -> int:
    if not documents:
        return 0
    batches = (len(documents) + BATCH_SIZE - 1) // BATCH_SIZE
    bar = reporter.bar(batches, "Embed & upsert") if reporter else None
    ctx = bar if bar else _null_context()
    with ctx:
        for i in range(0, len(documents), BATCH_SIZE):
            collection.upsert(
                ids=ids[i : i + BATCH_SIZE],
                documents=documents[i : i + BATCH_SIZE],
                metadatas=metadatas[i : i + BATCH_SIZE],
            )
            if bar:
                bar.update(1)
    return len(documents)


def _null_context():
    from contextlib import nullcontext

    return nullcontext()


def write_bm25_corpus(collection_key: str, ids: list[str], documents: list[str], metadatas: list[dict]) -> None:
    BM25_CORPUS_DIR.mkdir(parents=True, exist_ok=True)
    chroma_name = collection_names().get(collection_key, collection_key)
    path = BM25_CORPUS_DIR / f"{chroma_name}.json"
    by_id: dict[str, dict] = {}
    if path.is_file():
        try:
            for entry in json.loads(path.read_text(encoding="utf-8")):
                by_id[entry["id"]] = entry
        except (json.JSONDecodeError, OSError, KeyError):
            by_id = {}
    for i, d, m in zip(ids, documents, metadatas):
        by_id[i] = {"id": i, "text": d, "metadata": m}
    path.write_text(json.dumps(list(by_id.values()), ensure_ascii=False), encoding="utf-8")


def index_files(
    collection_key: str,
    file_map: dict[str, Path],
    *,
    reset: bool = False,
    incremental: bool = True,
    quiet: bool = False,
) -> dict[str, Any]:
    names = collection_names()
    chroma_name = names.get(collection_key, collection_key)

    if reset:
        reset_collection(chroma_name)
        clear_collection_manifest(collection_key)
        incremental = False

    to_index, removed = files_needing_reindex(
        collection_key, file_map, force_all=not incremental
    )

    rep = Reporter(quiet=quiet)
    if not quiet:
        rep.line(
            f"  {collection_key}: {len(file_map)} files in scope, "
            f"{len(to_index)} to chunk, {len(removed)} removed"
        )

    collection = get_collection(chroma_name)

    if removed and not reset:
        for rel in removed:
            try:
                existing = collection.get(where={"path": rel})
                if existing and existing.get("ids"):
                    collection.delete(ids=existing["ids"])
            except Exception:
                pass

    all_ids: list[str] = []
    all_docs: list[str] = []
    all_meta: list[dict] = []

    repos = repos_root()
    with rep.bar(len(to_index), f"Chunk {collection_key}") as bar:
        for path in to_index:
            ids, docs, meta, rel = file_to_chunks(path, repos, collection_key)
            if ids:
                all_ids.extend(ids)
                all_docs.extend(docs)
                all_meta.extend(meta)
            bar.update(1)

    if not quiet and all_docs:
        rep.line(f"  {collection_key}: {len(all_docs)} chunks ready for embedding")

    chunk_count = upsert_batches(
        collection, all_ids, all_docs, all_meta, reporter=rep
    )
    write_bm25_corpus(collection_key, all_ids, all_docs, all_meta)
    update_manifest_files(collection_key, file_map)

    if not quiet:
        print(
            f"  {collection_key}: indexed {len(to_index)} files, {chunk_count} chunks, "
            f"removed {len(removed)} paths",
            flush=True,
        )

    return {
        "collection": collection_key,
        "files_indexed": len(to_index),
        "chunks": chunk_count,
        "removed": len(removed),
    }


def iter_repo_files(
    repo_filter: str | None = None,
    *,
    skip_sql_scripts_tree: bool = False,
    docs_only: bool = False,
    sql_only: bool = False,
) -> dict[str, Path]:
    excludes = exclude_dirs()
    repos = repos_root()
    files: dict[str, Path] = {}

    if sql_only:
        sql_root = repos / "SQL-SCRIPTS"
        if sql_root.is_dir():
            for path in sql_root.rglob("*.sql"):
                if path.is_file() and path.stat().st_size <= MAX_FILE_BYTES:
                    rel = str(path.relative_to(repos))
                    files[rel] = path
        return files

    if repo_filter:
        targets = [repos / repo_filter]
    else:
        targets = sorted(p for p in repos.iterdir() if p.is_dir())

    for repo in targets:
        if not repo.exists():
            continue
        if skip_sql_scripts_tree and repo.name == "SQL-SCRIPTS":
            continue
        for path in repo.rglob("*"):
            if not path.is_file():
                continue
            if docs_only:
                from rag.chunking import is_repo_doc

                if not is_repo_doc(path, repos):
                    continue
            else:
                if path.suffix.lower() not in INDEXABLE_EXTENSIONS and path.name != ".env.example":
                    continue
            if _should_skip(path, excludes):
                continue
            if path.stat().st_size > MAX_FILE_BYTES:
                continue
            rel = str(path.relative_to(repos))
            files[rel] = path
    return files
