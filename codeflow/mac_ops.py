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


def _remote_branch_script(workdir: str, branch: str, *, main_only: bool) -> str:
    """Shell that checks out the chat branch (or main) on the Mac checkout."""
    return f"""
cd {shlex.quote(workdir)}
git rev-parse --is-inside-work-tree >/dev/null
configured=$(git config --get hooks.allowed-push-branch 2>/dev/null || echo cursor)
if [ "$configured" = "main" ] || [ {shlex.quote("1" if main_only else "0")} = "1" ]; then
  git checkout main
  echo "__ORION_BRANCH__=main"
  echo "__ORION_MAIN_ONLY__=1"
else
  branch={shlex.quote(branch)}
  case "$branch" in
    cursor/*) ;;
    *) echo "refusing branch $branch" >&2; exit 2 ;;
  esac
  if git show-ref --verify --quiet "refs/heads/$branch"; then
    git checkout "$branch"
  elif git fetch origin "$branch" && git show-ref --verify --quiet "refs/remotes/origin/$branch"; then
    git checkout -b "$branch" --track "origin/$branch"
  elif git fetch origin main && git show-ref --verify --quiet refs/remotes/origin/main; then
    git checkout -b "$branch" origin/main
  else
    git checkout -b "$branch"
  fi
  echo "__ORION_BRANCH__=$(git branch --show-current)"
  echo "__ORION_MAIN_ONLY__=0"
fi
"""


def remote_prepare_branch(
    *,
    workdir: str,
    branch: str,
    main_only: bool = False,
) -> dict[str, Any]:
    """Check out the chat branch on the Mac before Cursor edits."""
    cfg = load_mac_bridge_config()
    timeout = int((load_config().get("limits") or {}).get("subprocess_timeout_s", 120))
    remote = "set -euo pipefail\n" + _remote_branch_script(workdir, branch, main_only=main_only)
    try:
        proc = ssh_run(remote, cfg=cfg, timeout=timeout)
    except Exception as exc:
        return {"ok": False, "error": f"mac branch checkout failed: {exc}", "branch": branch}
    out = (proc.stdout or "") + "\n" + (proc.stderr or "")
    if proc.returncode != 0:
        return {"ok": False, "error": out[:800], "branch": branch}
    checked = branch
    main = main_only
    for line in out.splitlines():
        if line.startswith("__ORION_BRANCH__="):
            checked = line.split("=", 1)[1].strip() or checked
        if line.startswith("__ORION_MAIN_ONLY__="):
            main = line.split("=", 1)[1].strip() == "1"
    return {"ok": True, "branch": checked, "main_only": main, "error": "", "note": ""}


def remote_git_commit(
    *,
    workdir: str,
    commit_message: str,
    force_push: bool = False,
    request: str = "",
    repo: str = "",
    branch: str = "",
    main_only: bool = False,
) -> dict[str, Any]:
    """Commit (and optionally push/PR) on the Mac checkout."""
    cfg = load_mac_bridge_config()
    timeout = int((load_config().get("limits") or {}).get("cursor_agent_timeout_s", 1800))
    should_push = force_push or feature_enabled("auto_push_orion")
    auto_pr = feature_enabled("auto_pr")
    msg = commit_message.replace("'", "")[:200] or request[:72] or "orion cursor fix"
    head = branch or "cursor/change-chat"

    push_block = ""
    if should_push:
        pr_block = ""
        if auto_pr:
            pr_block = (
                'if [ "$branch" != "main" ] && command -v gh >/dev/null 2>&1; then '
                "gh pr create --base main --head \"$branch\" "
                f"--title {shlex.quote(msg[:72])} "
                f"--body {shlex.quote('Orion Cursor auto-fix for ' + repo)} "
                "2>/dev/null || gh pr view \"$branch\" --json url -q .url 2>/dev/null || true; "
                "fi"
            )
        push_block = (
            'git push -u origin "$branch" 2>&1 || true; '
            + pr_block
        )

    checkout = _remote_branch_script(workdir, head, main_only=main_only)
    remote = f"""
set -euo pipefail
{checkout}
branch=$(git branch --show-current)
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
