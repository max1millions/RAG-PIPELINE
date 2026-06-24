"""Stable incident fingerprints for deduplication."""

from __future__ import annotations

import hashlib
from typing import Any


def compute_fingerprint(row: dict[str, Any]) -> str:
    kind = str(row.get("kind") or "")
    tool = str(row.get("tool") or "")
    returncode = str(row.get("returncode") or "")
    stderr = str(row.get("stderr") or "")[:500]
    payload = f"{kind}|{tool}|{returncode}|{stderr}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
