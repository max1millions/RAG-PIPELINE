"""Stable incident fingerprints for deduplication."""

from __future__ import annotations

import hashlib
import re
from typing import Any

# HubSpot iframe URLs and similar tokens change on every retry. Hashing the
# raw stderr made each retry a new iMessage.
_URL_RE = re.compile(r"https?://\S+")
_UUID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
_QUERY_TOKEN_RE = re.compile(r"\b(utk|uuid|token|sid)=[A-Za-z0-9_-]+", re.IGNORECASE)
_LONG_HEX_RE = re.compile(r"\b[0-9a-fA-F]{16,}\b")


def normalize_stderr(stderr: str) -> str:
    """Strip volatile URLs and tokens so retries of one failure share a fingerprint."""
    text = stderr or ""
    text = _URL_RE.sub("<url>", text)
    text = _UUID_RE.sub("<uuid>", text)
    text = _QUERY_TOKEN_RE.sub(r"\1=<token>", text)
    text = _LONG_HEX_RE.sub("<hex>", text)
    return text


def failure_signature(stderr: str) -> str:
    """One stable line for the failure, without counts, quotes, or URLs.

    Playwright call logs repeat ``waiting for`` with a different count every
    run. The fingerprint uses the fatal line only, so those retries match.
    """
    text = normalize_stderr(stderr)
    chosen = ""
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("INFO:"):
            continue
        if (
            "Fatal portal error" in stripped
            or "ModuleNotFoundError" in stripped
            or stripped.startswith("Traceback")
            or re.search(r"\b(?:Error|Exception)\b", stripped)
        ):
            chosen = stripped
            break
    if not chosen:
        chosen = text
    chosen = re.sub(r"\d+", "#", chosen)
    chosen = chosen.replace('"', "").replace("'", "")
    chosen = re.sub(r"\s+", " ", chosen).strip()
    return chosen[:180]


def compute_fingerprint(row: dict[str, Any]) -> str:
    kind = str(row.get("kind") or "")
    tool = str(row.get("tool") or "")
    returncode = str(row.get("returncode") or "")
    stderr = failure_signature(str(row.get("stderr") or ""))
    payload = f"{kind}|{tool}|{returncode}|{stderr}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
