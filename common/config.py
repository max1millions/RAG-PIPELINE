"""Load features.yaml + .env and enforce feature-flag gating."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

from common.paths import _deep_merge, overlay_root, stack_root

STACK_ROOT = stack_root()
CONFIG_DIR = STACK_ROOT / "config"
FEATURES_PATH = CONFIG_DIR / "features.yaml"
ENV_PATH = CONFIG_DIR / ".env"

_config_cache: dict[str, Any] | None = None


def _expand(path: str) -> Path:
    return Path(os.path.expanduser(path)).resolve()


def load_config(reload: bool = False) -> dict[str, Any]:
    global _config_cache
    if _config_cache is not None and not reload:
        return _config_cache

    # Load .env: overlay wins (loaded first; load_dotenv override=False means
    # first-writer wins, so overlay values cannot be overwritten by repo .env).
    ov = overlay_root()
    if ov:
        overlay_env = ov / "config" / ".env"
        if overlay_env.exists():
            load_dotenv(overlay_env, override=False)
    if ENV_PATH.exists():
        load_dotenv(ENV_PATH, override=False)

    if not FEATURES_PATH.exists():
        raise FileNotFoundError(f"Missing features config: {FEATURES_PATH}")

    with FEATURES_PATH.open("r", encoding="utf-8") as fh:
        cfg: dict[str, Any] = yaml.safe_load(fh) or {}

    # Merge overlay features if present (overlay wins on scalar conflicts).
    if ov:
        overlay_features = ov / "config" / "features.yaml"
        if overlay_features.exists():
            try:
                with overlay_features.open("r", encoding="utf-8") as fh2:
                    ovdata = yaml.safe_load(fh2) or {}
                cfg = _deep_merge(cfg, ovdata)
            except Exception:
                pass

    paths = cfg.setdefault("paths", {})
    paths["stack_root"] = str(STACK_ROOT)
    paths["workspace"] = str(_expand(paths.get("workspace", "~/.openclaw/workspace")))
    paths["repos"] = str(_expand(paths.get("repos", "~/.openclaw/workspace/REPOS")))
    if ov:
        paths["overlay_root"] = str(ov)

    _config_cache = cfg
    return cfg


def feature_enabled(name: str) -> bool:
    cfg = load_config()
    return bool(cfg.get("features", {}).get(name, False))


def require_feature(name: str, human_label: str | None = None) -> None:
    if not feature_enabled(name):
        label = human_label or name
        print(
            f"ERROR: Feature '{label}' is disabled in {FEATURES_PATH}. "
            f"Set features.{name}: true to enable.",
            file=sys.stderr,
        )
        sys.exit(2)


def get_env(key: str, default: str | None = None) -> str | None:
    load_config()
    return os.environ.get(key, default)


def mysql_config() -> dict[str, Any]:
    load_config()
    return {
        "host": get_env("MYSQL_HOST", "127.0.0.1"),
        "port": int(get_env("MYSQL_PORT", "3306") or "3306"),
        "user": get_env("MYSQL_USER", "orion"),
        "password": get_env("MYSQL_PASSWORD", ""),
        "database": get_env("MYSQL_DATABASE", "orion_app"),
    }


def anthropic_api_key() -> str:
    load_config()
    key = get_env("ANTHROPIC_API_KEY")
    if not key or key.startswith("sk-ant-api03-REPLACE"):
        raise RuntimeError(
            f"ANTHROPIC_API_KEY is missing or placeholder. Set it in {ENV_PATH}"
        )
    return key
