"""Delegate the coding loop to Cursor Agent (local or via Mac SSH bridge).

Public module — no secrets. Credentials come from CURSOR_API_KEY in the
overlay .env (loaded by common.config). Agent binary paths may be set in
overlay mac_bridge.yaml or auto-discovered.
"""

from __future__ import annotations

import glob as _glob
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from common.config import cursor_api_key, load_config
from common.paths import git_bin


DEFAULT_NODE_AGENT_GLOBS = (
    "~/.cursor-server/data/User/globalStorage/anysphere.cursor-agent-worker/"
    "agent-cli/.local/share/cursor-agent/versions/*/cursor-agent",
)


def build_brief(
    *,
    request: str,
    repo: str,
    repo_path: str,
    rag_context: str = "",
    db_context: str = "",
    plan: str = "",
    test_feedback: str = "",
    target: str = "node",
) -> str:
    """Assemble the Cursor agent prompt. Orion owns commit/push after the run."""
    parts = [
        "You are the coding worker for Orion (RightsTune operator agent).",
        "Orion owns git commit, push, and PR creation — do NOT commit or push.",
        "Do NOT read, print, or edit .env files or credential files.",
        "Stay inside the workspace cwd only. Prefer minimal, correct edits.",
        f"Target host: {target}.",
        f"Repo name: {repo}.",
        f"Working directory: {repo_path}.",
        "",
        "## Request",
        request.strip(),
    ]
    if plan:
        parts.extend(["", "## Plan", plan[:12000]])
    if rag_context:
        parts.extend(["", "## RAG context (may be partial)", rag_context[:8000]])
    if db_context:
        parts.extend(["", "## DB / schema context", db_context[:4000]])
    if test_feedback:
        parts.extend(["", "## Fix these validation failures", test_feedback[:6000]])
    parts.extend(
        [
            "",
            "## Done criteria",
            "- Apply the requested code changes in this workspace.",
            "- Leave a clean working tree ready for Orion to `git add`/`commit`.",
            "- Summarize changed files in your final reply.",
        ]
    )
    return "\n".join(parts)


def discover_cursor_agent_bin(*, explicit: str | None = None) -> str:
    """Resolve cursor-agent binary on the local machine."""
    if explicit:
        p = Path(explicit).expanduser()
        if p.is_file() and os.access(p, os.X_OK):
            return str(p.resolve())
        raise FileNotFoundError(f"cursor_agent_bin not executable: {explicit}")

    which = shutil.which("cursor-agent") or shutil.which("agent")
    if which:
        return which

    matches: list[str] = []
    for pattern in DEFAULT_NODE_AGENT_GLOBS:
        matches.extend(_glob.glob(str(Path(pattern).expanduser())))
    matches = sorted(m for m in matches if os.path.isfile(m) and os.access(m, os.X_OK))
    if matches:
        return matches[-1]
    raise FileNotFoundError(
        "cursor-agent not found. Install Cursor Agent CLI or set cursor_agent_bin "
        "in overlay mac_bridge.yaml / features."
    )


def _git_changed_files(repo_path: Path) -> list[str]:
    git = git_bin()
    env = os.environ.copy()
    files: list[str] = []
    for args in (
        [git, "diff", "--name-only", "HEAD"],
        [git, "diff", "--name-only", "--cached"],
        [git, "ls-files", "--others", "--exclude-standard"],
    ):
        try:
            proc = subprocess.run(
                args,
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=60,
                env=env,
            )
        except (subprocess.TimeoutExpired, OSError):
            continue
        for ln in (proc.stdout or "").splitlines():
            rel = ln.strip()
            if rel and rel not in files:
                files.append(rel)
    return files


def run_cursor_agent_local(
    *,
    brief: str,
    cwd: Path,
    model: str | None = None,
    timeout_s: int | None = None,
    agent_bin: str | None = None,
) -> dict[str, Any]:
    """Run Cursor Agent headlessly against cwd. Returns status dict (no secrets)."""
    cfg = load_config()
    limits = cfg.get("limits") or {}
    timeout = int(timeout_s or limits.get("cursor_agent_timeout_s", 1800))
    model = model or str((cfg.get("models") or {}).get("cursor") or "auto")
    bin_path = discover_cursor_agent_bin(explicit=agent_bin)

    api_key = cursor_api_key(required=False)
    env = os.environ.copy()
    if api_key:
        env["CURSOR_API_KEY"] = api_key

    cmd = [
        bin_path,
        "-p",
        "--trust",
        "--force",
        "--output-format",
        "text",
        "--workspace",
        str(cwd),
    ]
    if model and model != "auto":
        cmd.extend(["--model", model])
    cmd.append(brief)

    started = time.time()
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "error": f"cursor-agent timed out after {timeout}s",
            "changed_files": _git_changed_files(cwd),
            "summary": "",
            "agent_bin": bin_path,
            "elapsed_s": round(time.time() - started, 1),
        }
    except FileNotFoundError as exc:
        return {
            "ok": False,
            "error": str(exc),
            "changed_files": [],
            "summary": "",
            "agent_bin": bin_path,
            "elapsed_s": round(time.time() - started, 1),
        }

    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    changed = _git_changed_files(cwd)
    ok = proc.returncode == 0
    combined = f"{out}\n{err}".strip()
    if not ok and "Authentication required" in combined:
        return {
            "ok": False,
            "error": "Cursor auth failed — set CURSOR_API_KEY in overlay config/.env",
            "changed_files": changed,
            "summary": out[:2000],
            "agent_bin": bin_path,
            "elapsed_s": round(time.time() - started, 1),
        }

    commit_message = ""
    for line in out.splitlines():
        lower = line.lower()
        if lower.startswith("commit:") or lower.startswith("commit message:"):
            commit_message = line.split(":", 1)[-1].strip()
            break

    return {
        "ok": ok or bool(changed),
        "error": "" if (ok or changed) else (err or out or f"exit {proc.returncode}")[:1000],
        "changed_files": changed,
        "summary": out[:4000],
        "commit_message": commit_message,
        "agent_bin": bin_path,
        "elapsed_s": round(time.time() - started, 1),
        "returncode": proc.returncode,
    }


def effective_diff_limits() -> tuple[int, int]:
    """Return (max_insertions, max_deletions) for the active code backend."""
    cfg = load_config()
    limits = cfg.get("limits") or {}
    backend = str((cfg.get("features") or {}).get("code_backend") or "langgraph")
    if backend == "cursor":
        return (
            int(limits.get("cursor_max_diff_insertions", 500)),
            int(limits.get("cursor_max_diff_deletions", 500)),
        )
    return (
        int(limits.get("max_diff_insertions", 50)),
        int(limits.get("max_diff_deletions", 20)),
    )
