"""RAG paths, excludes, and Chroma collection settings."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from common.config import load_config
from common.paths import rag_artifacts_dir as _rag_artifacts_dir, stack_root

STACK_ROOT = stack_root()

# Artifact directories — resolved via overlay if ORION_OVERLAY_ROOT is set.
_RAG_DIR = _rag_artifacts_dir()
CHROMA_DIR = _RAG_DIR / "chroma"
MANIFEST_PATH = _RAG_DIR / "index_manifest.json"
BM25_CORPUS_DIR = _RAG_DIR / "bm25_corpus"

# Legacy default collection name
COLLECTION_NAME = "orion_repos"

INDEXABLE_EXTENSIONS = {
    ".py",
    ".sh",
    ".sql",
    ".md",
    ".json",
    ".yaml",
    ".yml",
    ".txt",
    ".php",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".html",
    ".css",
    ".env.example",
}

DOC_FILENAMES = frozenset({"README.md", "CONTRIBUTING.md", "AGENTS.md", "TOOLS.md"})
SQL_REPO_NAME = "SQL-SCRIPTS"
MAX_DOC_DEPTH = 2
MAX_FILE_BYTES = 512_000


def rag_config() -> dict[str, Any]:
    return load_config().get("rag", {}) or {}


def collection_names() -> dict[str, str]:
    cfg = rag_config()
    defaults = {
        "repos": "orion_repos",
        "docs": "orion_docs",
        "sql": "orion_sql",
        "playbooks": "orion_playbooks",
        "discrepancies": "orion_discrepancies",
    }
    custom = cfg.get("collections") or {}
    return {k: str(custom.get(k, v)) for k, v in defaults.items()}


def repos_root() -> Path:
    cfg = load_config()
    return Path(cfg["paths"]["repos"])


def exclude_dirs() -> set[str]:
    return set(rag_config().get("exclude", []))


def chunk_settings() -> tuple[int, int]:
    cfg = load_config()
    limits = cfg.get("limits", {})
    return int(limits.get("rag_chunk_size", 1200)), int(limits.get("rag_chunk_overlap", 200))


def top_k_default() -> int:
    cfg = load_config()
    return int(cfg.get("limits", {}).get("rag_top_k", 8))


def bm25_top_k_default() -> int:
    cfg = load_config()
    return int(cfg.get("limits", {}).get("rag_bm25_top_k", 24))


def hybrid_enabled_by_config() -> bool:
    return bool(rag_config().get("hybrid", False))


def incremental_enabled() -> bool:
    return bool(rag_config().get("incremental", True))
