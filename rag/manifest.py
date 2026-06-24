"""Incremental index manifest (mtime + content hash per file)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from rag.settings import MANIFEST_PATH


def _file_sig(path: Path) -> dict[str, Any]:
    st = path.stat()
    digest = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    return {"mtime": int(st.st_mtime), "size": st.st_size, "hash": digest}


def load_manifest() -> dict[str, Any]:
    if not MANIFEST_PATH.exists():
        return {"collections": {}}
    try:
        return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"collections": {}}


def save_manifest(data: dict[str, Any]) -> None:
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def collection_manifest(data: dict[str, Any], collection: str) -> dict[str, Any]:
    return data.setdefault("collections", {}).setdefault(collection, {"files": {}})


def files_needing_reindex(
    collection_key: str,
    current_files: dict[str, Path],
    *,
    force_all: bool = False,
) -> tuple[list[Path], list[str]]:
    data = load_manifest()
    coll = collection_manifest(data, collection_key)
    prev: dict[str, Any] = coll.get("files", {})

    if force_all:
        return list(current_files.values()), list(prev.keys())

    to_index: list[Path] = []
    for rel, path in current_files.items():
        try:
            sig = _file_sig(path)
        except OSError:
            continue
        if prev.get(rel) != sig:
            to_index.append(path)

    removed = [rel for rel in prev if rel not in current_files]
    return to_index, removed


def update_manifest_files(collection_key: str, indexed: dict[str, Path]) -> None:
    data = load_manifest()
    coll = collection_manifest(data, collection_key)
    files_map: dict[str, Any] = {}
    for rel, path in indexed.items():
        if path.exists():
            try:
                files_map[rel] = _file_sig(path)
            except OSError:
                continue
    coll["files"] = files_map
    save_manifest(data)


def clear_collection_manifest(collection_key: str) -> None:
    data = load_manifest()
    data.setdefault("collections", {}).pop(collection_key, None)
    save_manifest(data)
