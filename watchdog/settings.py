"""Load watchdog.yaml and data paths."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from common.paths import data_dir as _data_dir, load_layered_config, stack_root

STACK_ROOT = stack_root()
CONFIG_DIR = STACK_ROOT / "config"

# Data paths resolved via overlay-aware helper.
DATA_DIR = _data_dir("watchdog")
BASELINES_PATH = DATA_DIR / "baselines.json"
RUN_HISTORY_PATH = DATA_DIR / "run_history.json"

_watchdog_cache: dict[str, Any] | None = None


def load_watchdog_config(reload: bool = False) -> dict[str, Any]:
    global _watchdog_cache
    if _watchdog_cache is not None and not reload:
        return _watchdog_cache
    data = load_layered_config("watchdog")
    if not data:
        raise FileNotFoundError(
            "Missing watchdog config. Copy config/watchdog.example.yaml to your overlay "
            "(ORION_OVERLAY_ROOT/config/watchdog.yaml) and add check definitions."
        )
    _watchdog_cache = data
    return _watchdog_cache


def ensure_data_dir() -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR


def repos_root() -> Path:
    from common.config import load_config

    cfg = load_config()
    return Path(cfg["paths"]["repos"])
