"""Central path resolution for the public/overlay two-layer config model.

Overlay discovery precedence:
  1. ORION_OVERLAY_ROOT env var (explicit override)
  2. paths.overlay_root in config/features.yaml
  3. Convention: ~/.openclaw/local/rag-pipeline/ (if dir exists)
  4. None → pure-public mode: in-repo paths + *.example.yaml configs

Usage:
    from common.paths import overlay_root, data_dir, rag_artifacts_dir, load_layered_config
"""
from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

import yaml


# ---------------------------------------------------------------------------
# Root resolution
# ---------------------------------------------------------------------------


def stack_root() -> Path:
    """Absolute path of the RAG-PIPELINE repository root."""
    return Path(__file__).resolve().parent.parent


def _raw_features() -> dict[str, Any]:
    """Read features.yaml directly (no caching, no circular import via config.py)."""
    p = stack_root() / "config" / "features.yaml"
    if not p.exists():
        return {}
    try:
        return yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def overlay_root() -> Path | None:
    """Return the resolved overlay root directory, or None in pure-public mode."""
    v = os.environ.get("ORION_OVERLAY_ROOT")
    if v:
        return Path(v).expanduser().resolve()
    cfg_val = _raw_features().get("paths", {}).get("overlay_root")
    if cfg_val:
        return Path(str(cfg_val)).expanduser().resolve()
    conv = Path("~/.openclaw/local/rag-pipeline").expanduser()
    if conv.is_dir():
        return conv.resolve()
    return None


# ---------------------------------------------------------------------------
# Data / artifact directories
# ---------------------------------------------------------------------------


def data_dir(sub: str) -> Path:
    """Return overlay/data/<sub> when overlay is configured, else STACK_ROOT/data/<sub>."""
    root = overlay_root() or stack_root()
    return root / "data" / sub


def rag_artifacts_dir() -> Path:
    """Return the rag artifacts root (chroma, bm25_corpus, index_manifest.json)."""
    return (overlay_root() or stack_root()) / "rag"


def repos_root() -> Path:
    """Return the REPOS workspace root (from features.yaml or default)."""
    raw = _raw_features().get("paths", {})
    repos_val = raw.get("repos", "~/.openclaw/workspace/REPOS")
    return Path(str(repos_val)).expanduser().resolve()


def git_bin() -> str:
    """Return the git binary to use: GIT env var → config path → ~/.openclaw/bin/git → 'git'."""
    v = os.environ.get("GIT")
    if v:
        return v
    cfg_val = _raw_features().get("paths", {}).get("git_bin")
    if cfg_val:
        return str(Path(str(cfg_val)).expanduser())
    claw_git = Path("~/.openclaw/bin/git").expanduser()
    if claw_git.exists():
        return str(claw_git)
    return "git"


def openclaw_bin_dir() -> Path | None:
    """Return ~/.openclaw/bin if it exists (used for PATH prepend in git operations)."""
    d = Path("~/.openclaw/bin").expanduser()
    return d if d.is_dir() else None


# ---------------------------------------------------------------------------
# Layered config loading
# ---------------------------------------------------------------------------


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def _deep_merge(base: dict[str, Any], overlay_data: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge overlay_data into base; overlay wins on scalar conflicts."""
    result = copy.deepcopy(base)
    for k, v in overlay_data.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = copy.deepcopy(v)
    return result


def _public_base(name: str) -> Path:
    """Return the public config path: NAME.yaml if it exists, else NAME.example.yaml."""
    sr = stack_root()
    live = sr / "config" / f"{name}.yaml"
    if live.exists():
        return live
    return sr / "config" / f"{name}.example.yaml"


def load_layered_config(name: str) -> dict[str, Any]:
    """Load a named config with overlay merge.

    Merge order:
      1. Public base (config/NAME.yaml or config/NAME.example.yaml)
      2. Overlay (OVERLAY/config/NAME.yaml) if overlay is configured
         OR legacy in-repo file if it differs from the public base
    """
    base = _read_yaml(_public_base(name))
    ov = overlay_root()
    if ov:
        over_file = ov / "config" / f"{name}.yaml"
        if over_file.exists():
            base = _deep_merge(base, _read_yaml(over_file))
    else:
        # Legacy support: in-repo private file (e.g. config/incidents.yaml before split)
        # Only applies when the live file is *different* from the public base (example).
        legacy = stack_root() / "config" / f"{name}.yaml"
        example = stack_root() / "config" / f"{name}.example.yaml"
        if (
            legacy.exists()
            and legacy != _public_base(name)
            and (not example.exists() or legacy != example)
        ):
            base = _deep_merge(base, _read_yaml(legacy))
    return base


def resolve_fixture(name: str) -> Path:
    """Return the overlay fixture path if present, else the public in-repo fixture."""
    ov = overlay_root()
    if ov:
        ovp = ov / "fixtures" / "golden" / name
        if ovp.exists():
            return ovp
    return stack_root() / "fixtures" / "golden" / name
