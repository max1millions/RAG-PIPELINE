"""Persist and compare watchdog metric baselines."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from watchdog.settings import BASELINES_PATH, ensure_data_dir


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _empty() -> dict[str, Any]:
    return {"checks": {}, "updated_at": None}


def load_baselines() -> dict[str, Any]:
    ensure_data_dir()
    if not BASELINES_PATH.exists():
        return _empty()
    try:
        data = json.loads(BASELINES_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return _empty()
    if "checks" not in data or not isinstance(data["checks"], dict):
        data["checks"] = {}
    return data


def save_baselines(data: dict[str, Any]) -> None:
    ensure_data_dir()
    data["updated_at"] = _utc_now_iso()
    BASELINES_PATH.write_text(json.dumps(data, indent=2, default=str) + "\n", encoding="utf-8")


def get_baseline(check_id: str) -> float | None:
    rec = load_baselines().get("checks", {}).get(check_id)
    if not isinstance(rec, dict):
        return None
    val = rec.get("value")
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def set_baseline(check_id: str, value: float) -> None:
    data = load_baselines()
    checks = data.setdefault("checks", {})
    checks[check_id] = {"value": value, "recorded_at": _utc_now_iso()}
    save_baselines(data)


def reset_baseline(check_id: str | None = None) -> None:
    if check_id is None:
        save_baselines(_empty())
        return
    data = load_baselines()
    checks = data.get("checks") or {}
    checks.pop(check_id, None)
    data["checks"] = checks
    save_baselines(data)
