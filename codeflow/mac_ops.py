"""Remote syntax / git finalize helpers for Mac bridge (public, no secrets)."""

from __future__ import annotations

import shlex
import subprocess
from typing import Any

from codeflow.mac_bridge import load_mac_bridge_config, ssh_run
from common.config import feature_enabled, load_config


def remote_syntax_check(workdir: str, changed_files: list[str]) -> dict[str, Any]:
    """Run py_compile / bash -n / php -l on Mac for changed files."""
    if not changed_files:
        return {"passed": True, "syntax_results": "(no files)"}
    cfg = load_mac_bridge_config()
    timeout = int((load_config().get("limits") or {}).get("subprocess_timeout_s", 120))
    results: list[str] = []
    failed = False
    for rel in changed_files:
        remote = (
            f"cd {shlex.quote(workdir)} && "
            f"f={shlex.quote(rel)}; "
            f'if [ ! -f "$f" ]; then echo "$f: MISSING"; exit 0; fi; '
            f'case "$f" in '
            f'*.py) python3 -m py_compile "$f" && echo "$f: OK" || echo "$f: FAIL";; '
            f'*.sh) bash -n "$f" && echo "$f: OK" || echo "$f: FAIL";; '
            f'*.php) php -l "$f" && echo "$f: OK" || echo "$f: FAIL";; '
            f'*) echo "$f: (no syntax checker)";; '
            f"esac"
        )
        try:
            proc = ssh_run(remote, cfg=cfg, timeout=timeout)
        except (subprocess.TimeoutExpired, OSError) as exc:
            results.append(f"{rel}: TIMEOUT/ERR {exc}")
            failed = True
            continue
        line = (proc.stdout or proc.stderr or "").strip().splitlines()
        msg = line[-1] if line else f"{rel}: (empty)"
        results.append(msg)
        if "FAIL" in msg or "MISSING" in msg:
            failed = True
    return {"passed": not failed, "syntax_results": "\n".join(results)}


def remote_git_commit(
    *,
    workdir: str,
    commit_message: str,
    force_push: bool = False,
    request: str = "",
    repo: str = "",
) -> dict[str, Any]:
    """Commit (and optionally push/PR) on the Mac checkout."""
    cfg = load_mac_bridge_config()
    timeout = int((load_config().get("limits") or {}).get("cursor_agent_timeout_s", 1800))
    should_push = force_push or feature_enabled("auto_push_orion")
    auto_pr = feature_enabled("auto_pr")
    msg = commit_message.replace("'", "")[:200] or request[:72] or "orion cursor fix"

    push_block = ""
    if should_push:
        pr_block = ""
        if auto_pr:
            pr_block = (
                "if command -v gh >/dev/null 2>&1; then "
                "gh pr create --base main --head orion "
                f"--title {shlex.quote(msg[:72])} "
                f"--body {shlex.quote('Orion Cursor auto-fix for ' + repo)} "
                "2>/dev/null || gh pr view orion --json url -q .url 2>/dev/null || true; "
                "fi"
            )
        push_block = (
            "git push -u origin orion 2>&1 || true; "
            + pr_block
        )

    remote = f"""
set -euo pipefail
cd {shlex.quote(workdir)}
git rev-parse --is-inside-work-tree >/dev/null
branch=$(git config --get hooks.allowed-push-branch 2>/dev/null || echo orion)
git checkout "$branch" 2>/dev/null || git checkout -b "$branch"
git add -A
if git diff --cached --quiet; then
  echo "__ORION_NO_COMMIT__"
  exit 0
fi
git commit -m {shlex.quote(msg)}
sha=$(git rev-parse HEAD)
echo "__ORION_SHA__=$sha"
{push_block}
"""
    try:
        proc = ssh_run(remote, cfg=cfg, timeout=timeout)
    except Exception as exc:
        return {
            "ok": False,
            "error": f"mac git finalize failed: {exc}",
            "commit_sha": "",
            "pushed": False,
            "pr_url": "",
            "summary": "",
        }

    out = (proc.stdout or "") + "\n" + (proc.stderr or "")
    if "__ORION_NO_COMMIT__" in out:
        return {
            "ok": True,
            "error": "",
            "commit_sha": "",
            "pushed": False,
            "pr_url": "",
            "summary": "No changes to commit on Mac.",
        }
    sha = ""
    for line in out.splitlines():
        if line.startswith("__ORION_SHA__="):
            sha = line.split("=", 1)[1].strip()
    pr_url = ""
    for line in out.splitlines():
        if line.startswith("https://") and "github.com" in line and "/pull/" in line:
            pr_url = line.strip()
            break
    ok = proc.returncode == 0 and bool(sha)
    return {
        "ok": ok,
        "error": "" if ok else out[:800],
        "commit_sha": sha,
        "pushed": should_push and bool(sha),
        "pr_url": pr_url,
        "summary": f"Mac commit {sha[:8] if sha else '(none)'}"
        + (f" — PR: {pr_url}" if pr_url else ""),
    }
