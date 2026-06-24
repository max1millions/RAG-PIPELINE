"""Load fixtures/golden/manifest.yaml (overlay-aware)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from common.paths import resolve_fixture

# Resolved at import time: overlay fixture if present, else public synthetic manifest.
MANIFEST_PATH = resolve_fixture("manifest.yaml")

_cache: dict[str, Any] | None = None


def load_manifest(reload: bool = False) -> dict[str, Any]:
    global _cache
    if _cache is not None and not reload:
        return _cache
    if not MANIFEST_PATH.exists():
        raise FileNotFoundError(f"Missing golden manifest: {MANIFEST_PATH}")
    with MANIFEST_PATH.open("r", encoding="utf-8") as fh:
        _cache = yaml.safe_load(fh) or {}
    return _cache


def list_cases(*, repo: str | None = None) -> list[dict[str, Any]]:
    manifest = load_manifest()
    cases = [c for c in (manifest.get("cases") or []) if isinstance(c, dict)]
    if repo:
        cases = [c for c in cases if str(c.get("repo") or "") == repo]
    return cases
