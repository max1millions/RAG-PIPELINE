"""Shell and compile golden cases."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from common.config import load_config


def _repo_path(repo: str) -> Path:
    cfg = load_config()
    return Path(cfg["paths"]["repos"]) / repo


def run_shell(case: dict[str, Any]) -> dict[str, Any]:
    repo = str(case.get("repo") or "")
    cmd = str(case.get("command") or "")
    if not cmd:
        return {"passed": False, "error": "missing command"}
    cwd = _repo_path(repo) if repo and repo != "RAG-PIPELINE" else Path(__file__).resolve().parent.parent
    try:
        proc = subprocess.run(
            cmd,
            shell=True,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=300,
        )
    except subprocess.TimeoutExpired:
        return {"passed": False, "error": "timeout"}
    except FileNotFoundError as exc:
        return {"passed": False, "error": str(exc)}
    return {
        "passed": proc.returncode == 0,
        "exit_code": proc.returncode,
        "stderr": (proc.stderr or "")[:500],
        "stdout": (proc.stdout or "")[:500],
    }
