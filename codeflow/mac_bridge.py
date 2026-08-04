"""SSH Mac Mini bridge for orion-fix Cursor delegation.

Public logic only. Real host/user/identity live in
$ORION_OVERLAY_ROOT/config/mac_bridge.yaml (see mac_bridge.example.yaml).
"""

from __future__ import annotations

import base64
import os
import shlex
import subprocess
from pathlib import Path
from typing import Any

from codeflow.cursor_backend import build_brief
from common.config import cursor_api_key, load_config
from common.paths import load_layered_config


def load_mac_bridge_config() -> dict[str, Any]:
    """Layered public example + overlay mac_bridge.yaml."""
    return load_layered_config("mac_bridge")


def resolve_target(
    *,
    explicit: str | None,
    request: str,
    repo: str,
) -> str:
    """Return 'node' or 'mac'. explicit may be node|mac|auto|None."""
    choice = (explicit or "auto").strip().lower()
    if choice in ("node", "mac"):
        return choice

    cfg = load_mac_bridge_config()
    if not cfg.get("enabled"):
        return "node"

    prefixes = [str(p).rstrip("/") for p in (cfg.get("mac_only_prefixes") or [])]
    hay = f"{repo} {request}".replace("\\", "/")
    for pref in prefixes:
        if pref and pref in hay:
            return "mac"
    # Path-style hints
    lowered = hay.lower()
    if "other/scripts" in lowered or "launchagents" in lowered or "/cron" in lowered:
        return "mac"
    return "node"


def mac_workdir(repo: str, cfg: dict[str, Any] | None = None) -> Path:
    """Absolute path on the Mac for this repo/module under developer_root."""
    cfg = cfg or load_mac_bridge_config()
    root = Path(str(cfg.get("developer_root") or "")).expanduser()
    if not str(root):
        raise RuntimeError("mac_bridge.developer_root is not set in overlay config")

    path_map = load_layered_config("path_map")
    prod_root = str(path_map.get("production_root") or root / "REPOS")
    modules = path_map.get("modules") or {}

    # Mac-only trees (OTHER/scripts) live under developer_root, not REPOS.
    mac_only = [str(p).rstrip("/") for p in (cfg.get("mac_only_prefixes") or [])]
    for pref in mac_only:
        if repo == pref or repo.startswith(pref + "/") or pref.endswith(repo):
            return (root / pref).resolve()
        if repo.upper() == "OTHER" or repo == "OTHER/scripts":
            return (root / "OTHER" / "scripts").resolve()

    if repo in modules:
        return (Path(prod_root) / repo).resolve()
    # Default: REPOS/<repo> under developer_root
    return (Path(prod_root) / repo).resolve()


def _ssh_base_cmd(cfg: dict[str, Any]) -> list[str]:
    host = str(cfg.get("ssh_host") or "").strip()
    user = str(cfg.get("ssh_user") or "").strip()
    identity = str(cfg.get("identity_file") or "").strip()
    if not host or not user:
        raise RuntimeError(
            "mac_bridge enabled but ssh_host/ssh_user missing — "
            "set them in $ORION_OVERLAY_ROOT/config/mac_bridge.yaml"
        )
    cmd = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=15",
        "-o",
        "StrictHostKeyChecking=accept-new",
    ]
    if identity:
        cmd.extend(["-i", str(Path(identity).expanduser())])
    cmd.append(f"{user}@{host}")
    return cmd


def ssh_run(
    remote_command: str,
    *,
    cfg: dict[str, Any] | None = None,
    timeout: int = 120,
) -> subprocess.CompletedProcess[str]:
    cfg = cfg or load_mac_bridge_config()
    cmd = _ssh_base_cmd(cfg) + [remote_command]
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def run_cursor_agent_mac(
    *,
    request: str,
    repo: str,
    rag_context: str = "",
    db_context: str = "",
    plan: str = "",
    test_feedback: str = "",
    model: str | None = None,
    timeout_s: int | None = None,
) -> dict[str, Any]:
    """SSH to Mac, run cursor-agent against developer_root workdir, harvest diff names."""
    cfg = load_mac_bridge_config()
    if not cfg.get("enabled"):
        return {
            "ok": False,
            "error": "mac_bridge.enabled is false — enable in overlay mac_bridge.yaml",
            "changed_files": [],
            "summary": "",
            "target": "mac",
        }

    workdir = mac_workdir(repo, cfg)
    brief = build_brief(
        request=request,
        repo=repo,
        repo_path=str(workdir),
        rag_context=rag_context,
        db_context=db_context,
        plan=plan,
        test_feedback=test_feedback,
        target="mac",
    )
    limits = (load_config().get("limits") or {})
    timeout = int(timeout_s or limits.get("cursor_agent_timeout_s", 1800))
    model = model or str(cfg.get("model") or (load_config().get("models") or {}).get("cursor") or "auto")
    agent_bin = str(cfg.get("cursor_agent_bin") or "").strip()

    # Pass API key via env on the remote only for the agent invocation.
    # Use a here-doc over SSH so the key is not placed in argv of a login shell history.
    api_key = cursor_api_key(required=False) or ""
    brief_b64 = base64.b64encode(brief.encode("utf-8")).decode("ascii")
    key_b64 = base64.b64encode(api_key.encode("utf-8")).decode("ascii") if api_key else ""

    remote_script = f"""
set -euo pipefail
WORKDIR={shlex.quote(str(workdir))}
AGENT_BIN={shlex.quote(agent_bin)}
MODEL={shlex.quote(model)}
BRIEF_B64={shlex.quote(brief_b64)}
KEY_B64={shlex.quote(key_b64)}
if [ ! -d "$WORKDIR" ]; then
  echo "ERROR: workdir missing: $WORKDIR" >&2
  exit 2
fi
if [ -z "$AGENT_BIN" ] || [ ! -x "$AGENT_BIN" ]; then
  AGENT_BIN=$(ls -1 "$HOME"/.cursor-server/data/User/globalStorage/anysphere.cursor-agent-worker/agent-cli/.local/share/cursor-agent/versions/*/cursor-agent 2>/dev/null | tail -1 || true)
fi
if [ -z "$AGENT_BIN" ] || [ ! -x "$AGENT_BIN" ]; then
  echo "ERROR: cursor-agent not found on Mac" >&2
  exit 2
fi
BRIEF=$(printf '%s' "$BRIEF_B64" | base64 -d)
if [ -n "$KEY_B64" ]; then
  export CURSOR_API_KEY=$(printf '%s' "$KEY_B64" | base64 -d)
fi
cd "$WORKDIR"
BEFORE=$(git status --porcelain 2>/dev/null || true)
set +e
if [ "$MODEL" != "auto" ] && [ -n "$MODEL" ]; then
  OUT=$("$AGENT_BIN" -p --trust --force --output-format text --workspace "$WORKDIR" --model "$MODEL" "$BRIEF" 2>&1)
  RC=$?
else
  OUT=$("$AGENT_BIN" -p --trust --force --output-format text --workspace "$WORKDIR" "$BRIEF" 2>&1)
  RC=$?
fi
set -e
AFTER=$(git status --porcelain 2>/dev/null || true)
CHANGED=$(git diff --name-only HEAD 2>/dev/null; git ls-files --others --exclude-standard 2>/dev/null)
printf '%s\\n' "$OUT"
echo "__ORION_CURSOR_RC__=$RC"
echo "__ORION_CURSOR_CHANGED_BEGIN__"
printf '%s\\n' "$CHANGED" | sort -u
echo "__ORION_CURSOR_CHANGED_END__"
"""

    try:
        proc = ssh_run(remote_script, cfg=cfg, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "error": f"mac bridge SSH/agent timed out after {timeout}s",
            "changed_files": [],
            "summary": "",
            "target": "mac",
            "repo_path": str(workdir),
        }
    except Exception as exc:
        return {
            "ok": False,
            "error": f"mac bridge SSH failed: {exc}",
            "changed_files": [],
            "summary": "",
            "target": "mac",
            "repo_path": str(workdir),
        }

    raw = (proc.stdout or "") + "\n" + (proc.stderr or "")
    if proc.returncode != 0 and "__ORION_CURSOR_RC__" not in raw:
        return {
            "ok": False,
            "error": f"mac SSH failed (exit {proc.returncode}): {(proc.stderr or proc.stdout or '')[:800]}",
            "changed_files": [],
            "summary": "",
            "target": "mac",
            "repo_path": str(workdir),
        }

    rc = 1
    changed: list[str] = []
    summary_lines: list[str] = []
    in_changed = False
    for line in raw.splitlines():
        if line.startswith("__ORION_CURSOR_RC__="):
            try:
                rc = int(line.split("=", 1)[1])
            except ValueError:
                rc = 1
            continue
        if line.strip() == "__ORION_CURSOR_CHANGED_BEGIN__":
            in_changed = True
            continue
        if line.strip() == "__ORION_CURSOR_CHANGED_END__":
            in_changed = False
            continue
        if in_changed:
            rel = line.strip()
            if rel and rel not in changed:
                changed.append(rel)
        else:
            summary_lines.append(line)

    summary = "\n".join(summary_lines).strip()
    if "Authentication required" in summary or "keychain is locked" in summary.lower():
        return {
            "ok": False,
            "error": "Cursor auth failed on Mac — set CURSOR_API_KEY in overlay .env "
            "(keychain unlock not available over SSH)",
            "changed_files": changed,
            "summary": summary[:2000],
            "target": "mac",
            "repo_path": str(workdir),
        }

    ok = rc == 0 or bool(changed)
    return {
        "ok": ok,
        "error": "" if ok else (summary[:1000] or f"cursor-agent exit {rc}"),
        "changed_files": changed,
        "summary": summary[:4000],
        "commit_message": "",
        "target": "mac",
        "repo_path": str(workdir),
        "returncode": rc,
    }
