"""Load incident supervisor config (path_map + incidents.yaml)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from common.paths import data_dir as _data_dir, load_layered_config, stack_root

# Keep CONFIG_DIR / STACK_ROOT for any legacy import references.
STACK_ROOT = stack_root()
CONFIG_DIR = STACK_ROOT / "config"

# Data paths resolved via overlay-aware helper (evaluated at import time;
# ORION_OVERLAY_ROOT must be set in the environment before import).
DATA_DIR = _data_dir("incidents")
ACTIVE_PATH = DATA_DIR / "active.json"
STATE_PATH = DATA_DIR / "state.json"

_path_map_cache: dict[str, Any] | None = None
_incidents_cache: dict[str, Any] | None = None


def load_path_map(reload: bool = False) -> dict[str, Any]:
    global _path_map_cache
    if _path_map_cache is not None and not reload:
        return _path_map_cache
    data = load_layered_config("path_map")
    if not data:
        raise FileNotFoundError(
            "Missing path_map config. Copy config/path_map.example.yaml to your overlay "
            "(ORION_OVERLAY_ROOT/config/path_map.yaml) and fill in production_root."
        )
    _path_map_cache = data
    return _path_map_cache


def load_incidents_config(reload: bool = False) -> dict[str, Any]:
    global _incidents_cache
    if _incidents_cache is not None and not reload:
        return _incidents_cache
    data = load_layered_config("incidents")
    if not data:
        raise FileNotFoundError(
            "Missing incidents config. Copy config/incidents.example.yaml to your overlay "
            "(ORION_OVERLAY_ROOT/config/incidents.yaml) and configure notify_targets."
        )
    _incidents_cache = data
    return _incidents_cache


def ensure_data_dir() -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR


def openclaw_token() -> str | None:
    token = os.environ.get("OPENCLAW_TOKEN")
    if token:
        return token
    state_dir = Path(os.environ.get("OPENCLAW_STATE_DIR", Path.home() / ".openclaw"))
    cfg_path = state_dir / "openclaw.json"
    if not cfg_path.is_file():
        return None
    import json

    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        return (cfg.get("gateway") or {}).get("auth", {}).get("token")
    except (json.JSONDecodeError, OSError):
        return None
