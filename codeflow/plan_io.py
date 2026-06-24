"""Read/write implementation plans under workspace plans/."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from common.config import load_config


def workspace_root() -> Path:
    cfg = load_config()
    return Path(cfg.get("paths", {}).get("workspace", "~/.openclaw/workspace")).expanduser()


def plans_dir() -> Path:
    d = workspace_root() / "plans"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _slug(text: str, max_len: int = 50) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()[:max_len]).strip("-")
    return slug or "task"


def new_plan_path(repo: str, request: str) -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return plans_dir() / f"{repo}__{_slug(request)}__{ts}.md"


def write_plan(repo: str, request: str, body: str) -> Path:
    path = new_plan_path(repo, request)
    header = (
        f"---\n"
        f"repo: {repo}\n"
        f"request: {request[:200]}\n"
        f"created: {datetime.now(timezone.utc).isoformat()}\n"
        f"planner: claude-opus-4-6\n"
        f"---\n\n"
    )
    path.write_text(header + body.strip() + "\n", encoding="utf-8")
    return path


def read_plan(path: str | Path) -> str:
    p = Path(path).expanduser()
    if not p.is_file():
        raise FileNotFoundError(f"plan not found: {p}")
    return p.read_text(encoding="utf-8")


def resolve_plan_path(path: str | Path) -> Path:
    p = Path(path).expanduser()
    if not p.is_absolute():
        candidate = workspace_root() / p
        if candidate.is_file():
            return candidate
        plans_candidate = plans_dir() / p.name if p.parent == Path(".") else plans_dir() / p
        if plans_candidate.is_file():
            return plans_candidate
        p = candidate
    if not p.is_file():
        raise FileNotFoundError(f"plan not found: {p}")
    return p
