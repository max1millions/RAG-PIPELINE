"""Stable fingerprints for watchdog anomaly deduplication."""

from __future__ import annotations

import hashlib


def compute_fingerprint(check_id: str, metric_signature: str) -> str:
    payload = f"watchdog|{check_id}|{metric_signature}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def metric_signature(*, check_id: str, metric_value: float | int | str, bucket: str = "") -> str:
    if bucket:
        return bucket
    if isinstance(metric_value, float):
        return f"{metric_value:.4f}"
    return str(metric_value)
