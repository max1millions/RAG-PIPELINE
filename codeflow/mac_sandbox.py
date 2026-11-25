"""Client side of the sandboxed Mac handoff.

The poll calls ``submit_handoff``, which SSHs ``orion-mac-handoff <repo>``
and writes the brief on stdin. It does not run cursor-agent, and it does
not send the Cursor API key. That key stays on the Mac, in a file the
seatbelt denies to the agent.
"""

from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import Path
from typing import Any

from codeflow.sandbox_policy import (
    MAX_BRIEF_CHARS,
    PolicyError,
    redact,
    scan_text,
    validate_repo_name,
)
from common.config import feature_enabled, load_config
from common.paths import load_layered_config


def load_mac_sandbox_config() -> dict[str, Any]:
    """Public example merged with the private overlay, if one is configured."""
    cfg = load_layered_config("mac_sandbox")
    return cfg if isinstance(cfg, dict) else {}


def _allow_repos(cfg: dict[str, Any]) -> set[str]:
    repos: set[str] = set()
    for name in cfg.get("allow_repos") or []:
        repos.add(validate_repo_name(str(name)))
    return repos


def sandbox_handoff_allowed(record: dict[str, Any], reason: str) -> bool:
    """True when this incident may be handed to the sandboxed Mac agent.

    Host, portal, network, and credential incidents stay iMessage-only
    because their ``fix_target`` is not ``code``. The feature flag and
    ``mac_sandbox.enabled`` both have to be on. The repo has to be on the
    allowlist.
    """
    if reason not in ("new", "reopened"):
        return False
    if str(record.get("fix_target") or "") != "code":
        return False
    if not feature_enabled("mac_sandbox_auto_fix"):
        return False
    cfg = load_mac_sandbox_config()
    if not cfg.get("enabled"):
        return False
    repo = str(record.get("repos_name") or "")
    try:
        validate_repo_name(repo)
    except PolicyError:
        return False
    return repo in _allow_repos(cfg)


def incident_brief(record: dict[str, Any]) -> str:
    """Build the agent prompt. Refuse if the stream looks like a secret."""
    fp = str(record.get("fingerprint") or "")
    repo = validate_repo_name(str(record.get("repos_name") or ""))
    rel = str(record.get("repos_rel_path") or "")
    message = str(record.get("message") or "")
    stack = str(record.get("stack_trace") or record.get("raw_stderr_tail") or "")
    parts = [
        "Fix this production failure in the repository. Edit source only.",
        "Do not run portal automations, statement importers, or any command that reads credentials.",
        "Do not read or write .env files, keys, or SSH config.",
        "Do not commit, push, or open a pull request. The wrapper does that.",
        f"Incident: {fp[:8]}",
        f"Repo: {repo}.",
    ]
    if rel:
        parts.append(f"Primary file hint: {rel}.")
    if message:
        parts.append(f"Error: {message}")
    if stack:
        parts.append("Stack trace / stderr:\n" + stack[:3000])
    brief = "\n".join(parts)[:MAX_BRIEF_CHARS]
    if scan_text(brief):
        raise PolicyError("incident stream contains secret-shaped text")
    return brief


def _ssh_command(cfg: dict[str, Any], repo: str) -> list[str]:
    host = str(cfg.get("ssh_host") or "").strip()
    user = str(cfg.get("ssh_user") or "cursor-worker").strip()
    if not host or not user:
        raise PolicyError("mac_sandbox ssh_host is not set")
    if user != "cursor-worker":
        raise PolicyError("mac_sandbox ssh_user must be cursor-worker")
    cmd = [
        "ssh",
        "-T",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=15",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        "ClearAllForwardings=yes",
    ]
    identity = str(cfg.get("identity_file") or "").strip()
    if identity:
        cmd.extend(["-i", str(Path(identity).expanduser())])
    cmd.append(f"{user}@{host}")
    cmd.extend(["orion-mac-handoff", repo])
    return cmd


def _parse_result(raw: str) -> dict[str, Any]:
    marker = "__ORION_HANDOFF_JSON__="
    found = ""
    for line in raw.splitlines():
        if line.startswith(marker):
            found = line[len(marker):]
    if not found:
        return {}
    try:
        data = json.loads(found)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def submit_handoff(record: dict[str, Any]) -> dict[str, Any]:
    """SSH the brief to the ForceCommand wrapper. Never runs the agent locally."""
    repo = str(record.get("repos_name") or "")
    fp = str(record.get("fingerprint") or "")
    empty = {
        "ok": False,
        "error": "",
        "summary": "",
        "pr_url": "",
        "commit_sha": "",
        "branch": "",
    }
    try:
        cfg = load_mac_sandbox_config()
        if not cfg.get("enabled") or not feature_enabled("mac_sandbox_auto_fix"):
            empty["error"] = "mac sandbox handoff is disabled"
            return empty
        validate_repo_name(repo)
        if repo not in _allow_repos(cfg):
            empty["error"] = "repo not allowlisted"
            return empty
        brief = incident_brief(record)
        payload = json.dumps({"repo": repo, "fingerprint": fp, "brief": brief})
        if scan_text(payload):
            empty["error"] = "refusing to send secret-shaped text"
            return empty
        cmd = _ssh_command(cfg, repo)
    except PolicyError as exc:
        empty["error"] = str(exc)
        return empty

    limits = load_config().get("limits") or {}
    timeout = int(limits.get("cursor_agent_timeout_s") or 1800)
    try:
        proc = subprocess.run(
            cmd,
            input=payload,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        empty["error"] = f"mac sandbox handoff timed out after {timeout}s"
        return empty
    except OSError as exc:
        empty["error"] = f"mac sandbox ssh failed: {exc}"
        return empty

    raw = redact((proc.stdout or "") + "\n" + (proc.stderr or ""))
    parsed = _parse_result(raw)
    if not parsed:
        empty["error"] = redact((proc.stderr or proc.stdout or "no handoff result")[:500])
        return empty
    ok = bool(parsed.get("ok")) and proc.returncode == 0
    return {
        "ok": ok,
        "error": "" if ok else redact(str(parsed.get("error") or "handoff failed"))[:500],
        "summary": redact(str(parsed.get("summary") or ""))[:1000],
        "pr_url": str(parsed.get("pr_url") or ""),
        "commit_sha": str(parsed.get("commit_sha") or ""),
        "branch": str(parsed.get("branch") or ""),
    }


def ssh_command_for_log(cfg: dict[str, Any], repo: str) -> str:
    """Shell-quoted SSH command with no brief and no secrets. For docs and tests."""
    return " ".join(shlex.quote(part) for part in _ssh_command(cfg, repo))
