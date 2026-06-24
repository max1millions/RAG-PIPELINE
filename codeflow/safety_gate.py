"""Pre-push safety gates for orion-fix commits."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

from common.config import load_config


def _git_diff_stat(repo_path: Path, env: dict[str, str]) -> tuple[int, int, list[str]]:
    proc = subprocess.run(
        ["git", "diff", "--cached", "--shortstat"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )
    stat = (proc.stdout or proc.stderr or "").strip()
    insertions = 0
    deletions = 0
    m_ins = re.search(r"(\d+) insertion", stat)
    m_del = re.search(r"(\d+) deletion", stat)
    if m_ins:
        insertions = int(m_ins.group(1))
    if m_del:
        deletions = int(m_del.group(1))

    names = subprocess.run(
        ["git", "diff", "--cached", "--name-only"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )
    files = [ln.strip() for ln in (names.stdout or "").splitlines() if ln.strip()]
    return insertions, deletions, files


def check_diff(repo_path: Path, *, env: dict[str, str] | None = None) -> dict[str, Any]:
    """Return {passed: bool, reasons: list[str], ...}."""
    cfg = load_config()
    limits = cfg.get("limits") or {}
    max_ins = int(limits.get("max_diff_insertions", 50))
    max_del = int(limits.get("max_diff_deletions", 20))
    patterns = [str(p) for p in (limits.get("forbidden_path_patterns") or [])]

    git_env = env or {}
    insertions, deletions, files = _git_diff_stat(repo_path, git_env)
    reasons: list[str] = []

    if insertions > max_ins:
        reasons.append(f"insertions {insertions} > max {max_ins}")
    if deletions > max_del:
        reasons.append(f"deletions {deletions} > max {max_del}")

    for rel in files:
        for pat in patterns:
            if re.search(pat, rel):
                reasons.append(f"forbidden path {rel!r} matches {pat!r}")
                break

    diff_text = subprocess.run(
        ["git", "diff", "--cached"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        timeout=60,
        env=git_env,
    )
    body = diff_text.stdout or ""
    if "<<<<<<<" in body or "=======" in body and ">>>>>>>" in body:
        reasons.append("unresolved merge conflict markers in staged diff")

    return {
        "passed": len(reasons) == 0,
        "reasons": reasons,
        "insertions": insertions,
        "deletions": deletions,
        "files": files,
    }
