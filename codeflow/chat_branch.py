"""Per-chat git branches for Orion code changes.

One OpenClaw session (the iMessage thread until /new or /reset) maps to one
``cursor/<slug>-<id>`` branch name. Every repo touched in that session uses
that name. A new session id starts a new branch. Incident runs get their own
``cursor/incident-<fingerprint>`` branch so they do not land on the user's chat.

Main-only repos (hooks.allowed-push-branch=main) stay on main.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import subprocess
import tempfile
import traceback
from pathlib import Path
from typing import Any, Callable

from common.paths import data_dir, git_bin, openclaw_bin_dir

BRANCH_RE = re.compile(r"^cursor/[a-z0-9][a-z0-9._-]{0,80}$")
_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(text: str, limit: int = 40) -> str:
    first = (text or "").strip().splitlines()[0] if text else ""
    slug = _SLUG_RE.sub("-", first.lower()).strip("-")
    if not slug:
        return "change"
    return slug[:limit].strip("-") or "change"


def short_suffix(session_id: str) -> str:
    compact = re.sub(r"[^a-z0-9]", "", (session_id or "").lower())
    return compact[-4:] or "chat"


def incident_branch(fingerprint: str) -> str:
    compact = re.sub(r"[^a-f0-9]", "", (fingerprint or "").lower())[:8] or "unknown"
    return f"cursor/incident-{compact}"


def branch_name_for(session_id: str, request: str) -> str:
    name = f"cursor/{slugify(request)}-{short_suffix(session_id)}"
    if not BRANCH_RE.match(name):
        name = f"cursor/change-{short_suffix(session_id)}"
    return name


def _running_unit_test() -> bool:
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return True
    for frame in traceback.extract_stack():
        name = frame.filename.replace("\\", "/")
        if "/codeflow/test_" in name:
            return True
    return False


def store_path() -> Path:
    override = os.environ.get("ORION_CHAT_BRANCH_STORE", "").strip()
    if override:
        return Path(override).expanduser()
    if _running_unit_test():
        return Path(tempfile.gettempdir()) / "orion-chat-branches-unittest.json"
    return data_dir("chat-branches") / "index.json"


def current_session_id() -> str:
    """OpenClaw session id for the iMessage thread. /new replaces this id."""
    for key in ("ORION_SESSION_ID", "OPENCLAW_SESSION_ID"):
        val = os.environ.get(key, "").strip()
        if val:
            return val
    if _running_unit_test():
        return "pytest-session"
    state = Path(os.environ.get("OPENCLAW_STATE_DIR", str(Path.home() / ".openclaw")))
    agent = os.environ.get("ORION_AGENT_ID", "main").strip() or "main"
    path = state / "agents" / agent / "sessions" / "sessions.json"
    if not path.is_file():
        return ""
    try:
        store = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    if not isinstance(store, dict):
        return ""
    entry = store.get(f"agent:{agent}:main")
    if isinstance(entry, dict) and entry.get("sessionId"):
        return str(entry["sessionId"]).strip()
    return ""


def _empty_store() -> dict[str, Any]:
    return {"sessions": {}}


def _read_store() -> dict[str, Any]:
    path = store_path()
    if not path.is_file():
        return _empty_store()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _empty_store()
    if not isinstance(data, dict):
        return _empty_store()
    sessions = data.get("sessions")
    if not isinstance(sessions, dict):
        data["sessions"] = {}
    return data


def _write_store(data: dict[str, Any]) -> None:
    path = store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _locked_update(mutator: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    path = store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        data = _read_store()
        mutator(data)
        _write_store(data)
        return data


def allocate_session_branch(session_id: str, request: str) -> str:
    """Return the stable branch name for this session, creating it on first use."""
    found: dict[str, str] = {}

    def mutate(data: dict[str, Any]) -> None:
        sessions = data.setdefault("sessions", {})
        rec = sessions.get(session_id)
        if not isinstance(rec, dict):
            rec = {}
        existing = str(rec.get("branch") or "")
        if existing and BRANCH_RE.match(existing):
            found["branch"] = existing
            sessions[session_id] = rec
            return
        slug = str(rec.get("slug") or "") or slugify(request)
        branch = f"cursor/{slug}-{short_suffix(session_id)}"
        if not BRANCH_RE.match(branch):
            branch = f"cursor/change-{short_suffix(session_id)}"
        rec["slug"] = slug
        rec["branch"] = branch
        rec.setdefault("repos", {})
        sessions[session_id] = rec
        found["branch"] = branch

    _locked_update(mutate)
    return found["branch"]


def remember_repo(session_id: str, repo: str, branch: str) -> None:
    if not session_id or not repo:
        return

    def mutate(data: dict[str, Any]) -> None:
        sessions = data.setdefault("sessions", {})
        rec = sessions.get(session_id)
        if not isinstance(rec, dict):
            rec = {"branch": branch, "repos": {}}
        repos = rec.get("repos")
        if not isinstance(repos, dict):
            repos = {}
        repos[repo] = branch
        rec["repos"] = repos
        rec["branch"] = branch
        sessions[session_id] = rec

    _locked_update(mutate)


def session_repo_branches(session_id: str) -> dict[str, str]:
    rec = _read_store().get("sessions", {}).get(session_id) or {}
    repos = rec.get("repos") if isinstance(rec, dict) else None
    if not isinstance(repos, dict):
        return {}
    out: dict[str, str] = {}
    for repo, branch in repos.items():
        if isinstance(repo, str) and isinstance(branch, str) and BRANCH_RE.match(branch):
            out[repo] = branch
    return out


def forget_repo(session_id: str, repo: str) -> None:
    """Drop a repo after its branch was merged. Keep the session branch name."""

    def mutate(data: dict[str, Any]) -> None:
        rec = (data.get("sessions") or {}).get(session_id)
        if not isinstance(rec, dict):
            return
        repos = rec.get("repos")
        if isinstance(repos, dict):
            repos.pop(repo, None)

    _locked_update(mutate)


def _git_env() -> dict[str, str]:
    env = os.environ.copy()
    claw = openclaw_bin_dir()
    if claw:
        env["PATH"] = str(claw) + os.pathsep + env.get("PATH", "")
    return env


def git_run(repo: Path, args: list[str], timeout: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [git_bin(), *args],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=_git_env(),
    )


def is_main_only(repo: Path) -> bool:
    if not (repo / ".git").exists() and not (repo / ".git").is_file():
        return False
    proc = git_run(repo, ["config", "--get", "hooks.allowed-push-branch"])
    return proc.returncode == 0 and (proc.stdout or "").strip() == "main"


def _current_branch(repo: Path) -> str:
    proc = git_run(repo, ["rev-parse", "--abbrev-ref", "HEAD"])
    if proc.returncode != 0:
        return ""
    return (proc.stdout or "").strip()


def _dirty(repo: Path) -> bool:
    proc = git_run(repo, ["status", "--porcelain", "--untracked-files=no"])
    return bool((proc.stdout or "").strip())


def _ref_exists(repo: Path, ref: str) -> bool:
    return git_run(repo, ["show-ref", "--verify", "--quiet", ref]).returncode == 0


def checkout_branch(repo: Path, branch: str, *, main_only: bool = False) -> dict[str, Any]:
    """Check out ``branch``, creating chat branches from origin/main."""
    if main_only:
        branch = "main"
    if not BRANCH_RE.match(branch) and branch != "main":
        return {"ok": False, "error": f"refusing branch name {branch!r}", "branch": branch}

    current = _current_branch(repo)
    note = ""
    if current != branch and _dirty(repo):
        stash = git_run(
            repo,
            ["stash", "push", "-m", f"orion-chat-branch leftover from {current or 'HEAD'}"],
        )
        if stash.returncode != 0:
            err = (stash.stderr or stash.stdout or "stash failed").strip()
            return {
                "ok": False,
                "error": (
                    f"working tree on {current or 'detached HEAD'} has uncommitted changes "
                    f"and could not be stashed: {err[:300]}"
                ),
                "branch": branch,
            }
        note = f"Stashed uncommitted changes from {current}."

    if current == branch:
        return {"ok": True, "branch": branch, "created": False, "note": note}

    if branch == "main" or _ref_exists(repo, f"refs/heads/{branch}"):
        co = git_run(repo, ["checkout", branch])
        if co.returncode != 0:
            err = (co.stderr or co.stdout or "checkout failed").strip()
            return {"ok": False, "error": err[:400], "branch": branch, "note": note}
        return {"ok": True, "branch": branch, "created": False, "note": note}

    git_run(repo, ["fetch", "origin", branch])
    if _ref_exists(repo, f"refs/remotes/origin/{branch}"):
        co = git_run(repo, ["checkout", "-b", branch, "--track", f"origin/{branch}"])
        if co.returncode == 0:
            return {"ok": True, "branch": branch, "created": False, "note": note}

    git_run(repo, ["fetch", "origin", "main"])
    if _ref_exists(repo, "refs/remotes/origin/main"):
        co = git_run(repo, ["checkout", "-b", branch, "origin/main"])
    else:
        co = git_run(repo, ["checkout", "-b", branch])
    if co.returncode != 0:
        err = (co.stderr or co.stdout or "checkout failed").strip()
        return {"ok": False, "error": err[:400], "branch": branch, "note": note}
    return {"ok": True, "branch": branch, "created": True, "note": note}


def resolve_branch(
    *,
    request: str,
    repo: str,
    repo_path: Path,
    incident_fingerprint: str = "",
    session_id: str = "",
) -> dict[str, Any]:
    """Choose the branch name. Does not check it out."""
    if is_main_only(repo_path):
        return {"ok": True, "branch": "main", "main_only": True, "session_id": ""}
    fp = (incident_fingerprint or "").strip()
    if fp:
        return {
            "ok": True,
            "branch": incident_branch(fp),
            "main_only": False,
            "session_id": "",
        }
    sid = (session_id or "").strip() or current_session_id()
    if not sid:
        return {
            "ok": False,
            "error": (
                "No OpenClaw session id. Code changes from iMessage use the current "
                "chat's branch; /new starts a new one. Set ORION_SESSION_ID to override."
            ),
            "branch": "",
            "main_only": False,
            "session_id": "",
        }
    branch = allocate_session_branch(sid, request)
    return {"ok": True, "branch": branch, "main_only": False, "session_id": sid, "repo": repo}


def prepare_repo_branch(
    repo_path: Path,
    *,
    request: str,
    repo: str,
    incident_fingerprint: str = "",
    session_id: str = "",
) -> dict[str, Any]:
    resolved = resolve_branch(
        request=request,
        repo=repo,
        repo_path=repo_path,
        incident_fingerprint=incident_fingerprint,
        session_id=session_id,
    )
    if not resolved.get("ok"):
        return resolved
    checked = checkout_branch(
        repo_path,
        str(resolved["branch"]),
        main_only=bool(resolved.get("main_only")),
    )
    if not checked.get("ok"):
        return {**resolved, **checked, "ok": False}
    sid = str(resolved.get("session_id") or "")
    if sid and not resolved.get("main_only"):
        remember_repo(sid, repo, str(checked["branch"]))
    return {**resolved, **checked, "ok": True}


def ensure_pr(
    repo_path: Path,
    *,
    head: str,
    title: str,
    body: str,
    timeout: int = 60,
) -> str:
    """Return the PR URL for ``head``, creating it against main when missing."""
    view = subprocess.run(
        ["gh", "pr", "view", head, "--json", "url,state"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if view.returncode == 0 and view.stdout.strip():
        try:
            data = json.loads(view.stdout)
            url = str(data.get("url") or "")
            if url and str(data.get("state") or "").upper() != "MERGED":
                return url
        except json.JSONDecodeError:
            pass
    create = subprocess.run(
        [
            "gh",
            "pr",
            "create",
            "--base",
            "main",
            "--head",
            head,
            "--title",
            title[:72] or head,
            "--body",
            body,
        ],
        cwd=repo_path,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if create.returncode == 0:
        return (create.stdout or "").strip()
    err = (create.stderr or create.stdout or "").strip()
    if "already exists" in err.lower():
        view2 = subprocess.run(
            ["gh", "pr", "view", head, "--json", "url"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if view2.returncode == 0:
            try:
                return str(json.loads(view2.stdout).get("url") or "")
            except json.JSONDecodeError:
                pass
    return f"(PR not created: {err[:200]})"


def merge_branch(
    repo_path: Path,
    *,
    repo: str,
    branch: str,
    session_id: str = "",
    title: str = "",
    timeout: int = 120,
) -> dict[str, Any]:
    """Push ``branch`` and merge its PR into main. Does not run unless asked."""
    if is_main_only(repo_path):
        return {
            "ok": False,
            "error": f"{repo} pushes straight to main. There is no chat branch to merge.",
            "repo": repo,
            "branch": "main",
        }
    if not BRANCH_RE.match(branch):
        return {"ok": False, "error": f"refusing to merge {branch!r}", "repo": repo, "branch": branch}

    checked = checkout_branch(repo_path, branch)
    if not checked.get("ok"):
        return {"ok": False, "error": checked.get("error") or "checkout failed", "repo": repo, "branch": branch}

    push = git_run(repo_path, ["push", "-u", "origin", branch], timeout=timeout)
    if push.returncode != 0:
        err = (push.stderr or push.stdout or "push failed").strip()
        return {"ok": False, "error": err[:500], "repo": repo, "branch": branch, "pushed": False}

    pr_title = title or f"{repo}: {branch}"
    pr_url = ensure_pr(
        repo_path,
        head=branch,
        title=pr_title,
        body=f"Merge chat branch `{branch}` into main for {repo}.",
        timeout=timeout,
    )
    if not pr_url.startswith("http"):
        return {
            "ok": False,
            "error": pr_url or "could not open a pull request",
            "repo": repo,
            "branch": branch,
            "pushed": True,
            "pr_url": pr_url,
        }

    # Merge on GitHub. Never merge the chat branch into local main.
    merged = subprocess.run(
        ["gh", "pr", "merge", pr_url, "--merge", "--delete-branch"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if merged.returncode != 0:
        err = (merged.stderr or merged.stdout or "gh pr merge failed").strip()
        return {
            "ok": False,
            "error": err[:500],
            "repo": repo,
            "branch": branch,
            "pushed": True,
            "pr_url": pr_url,
            "merged": False,
        }

    view = subprocess.run(
        ["gh", "pr", "view", pr_url, "--json", "state,baseRefName,mergeCommit,url"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    merge_sha = ""
    if view.returncode == 0 and view.stdout.strip():
        try:
            data = json.loads(view.stdout)
        except json.JSONDecodeError:
            data = {}
        state = str(data.get("state") or "")
        base = str(data.get("baseRefName") or "")
        merge_sha = str((data.get("mergeCommit") or {}).get("oid") or "")
        if state != "MERGED" or base != "main":
            return {
                "ok": False,
                "error": f"GitHub PR is {state or 'unknown'} into {base or 'unknown'}, not merged into main",
                "repo": repo,
                "branch": branch,
                "pushed": True,
                "pr_url": pr_url,
                "merged": False,
            }
    else:
        err = (view.stderr or view.stdout or "could not confirm GitHub merge").strip()
        return {
            "ok": False,
            "error": err[:500],
            "repo": repo,
            "branch": branch,
            "pushed": True,
            "pr_url": pr_url,
            "merged": False,
        }

    # Local main only fast-forwards to the commit GitHub already created.
    git_run(repo_path, ["checkout", "main"])
    git_run(repo_path, ["fetch", "origin", "main"], timeout=timeout)
    git_run(repo_path, ["merge", "--ff-only", "origin/main"], timeout=timeout)
    git_run(repo_path, ["branch", "-D", branch])
    if session_id:
        forget_repo(session_id, repo)
    short = merge_sha[:8] if merge_sha else "merged"
    return {
        "ok": True,
        "repo": repo,
        "branch": branch,
        "pushed": True,
        "merged": True,
        "merge_sha": merge_sha,
        "pr_url": pr_url,
        "summary": (
            f"Merged {branch} into main on GitHub ({short}) for {repo}. PR: {pr_url}"
        ),
    }
