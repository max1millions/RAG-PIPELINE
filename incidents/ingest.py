"""Normalize Mac incidents.jsonl rows into FSM records."""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Any

from incidents.fingerprint import compute_fingerprint
from incidents.settings import load_path_map

_TRACEBACK_FILE = re.compile(
    r'File "([^"]+)", line (\d+)',
)


def _severity(kind: str) -> str:
    if kind == "timeout":
        return "timeout"
    return "error"


def _message_from_stderr(stderr: str) -> str:
    lines = [ln.strip() for ln in stderr.splitlines() if ln.strip()]
    if not lines:
        return "(no stderr)"
    for ln in reversed(lines):
        if ln.startswith(("Traceback", "  File ", "During handling")):
            continue
        return ln[:240]
    return lines[-1][:240]


def _repos_rel_path(
    module: str,
    cwd: str,
    argv: list[Any],
    stderr: str,
    production_root: str,
) -> str:
    prefix = f"{production_root.rstrip('/')}/{module}/"
    for match in _TRACEBACK_FILE.finditer(stderr):
        path = match.group(1)
        if path.startswith(prefix):
            return path[len(prefix) :]
        if f"/REPOS/{module}/" in path:
            idx = path.find(f"/REPOS/{module}/")
            return path[idx + len(f"/REPOS/{module}/") :]
    if argv:
        first = str(argv[0])
        if first.startswith("./"):
            return first[2:]
        if not first.startswith("/") and "/" not in first:
            return first
    if cwd.startswith(prefix):
        return PurePosixPath(cwd[len(prefix) :]).name or "."
    return ""


def normalize_mac_row(row: dict[str, Any]) -> dict[str, Any]:
    path_map = load_path_map()
    production_root = str(path_map.get("production_root") or "")
    if not production_root:
        raise ValueError(
            "path_map.production_root is not set. "
            "Set production_root in ORION_OVERLAY_ROOT/config/path_map.yaml."
        )
    modules = path_map.get("modules") or {}

    module = str(row.get("module") or "")
    module_cfg = modules.get(module) or {}
    repos_name = str(module_cfg.get("repos_name") or module or "UNKNOWN")

    stderr = str(row.get("stderr") or "")
    kind = str(row.get("kind") or "nonzero_exit")
    fp = compute_fingerprint(row)

    return {
        "fingerprint": fp,
        "detected_at": str(row.get("ts") or ""),
        "source": "mcp_tool",
        "tool": str(row.get("tool") or ""),
        "module": module,
        "repos_name": repos_name,
        "repos_rel_path": _repos_rel_path(
            module,
            str(row.get("cwd") or ""),
            list(row.get("argv") or []),
            stderr,
            production_root,
        ),
        "severity": _severity(kind),
        "kind": kind,
        "returncode": row.get("returncode"),
        "message": _message_from_stderr(stderr),
        "stack_trace": stderr if "Traceback" in stderr else "",
        "raw_stderr_tail": stderr,
        "mac_payload": row,
    }
