"""Send incident alerts via configured notify_backend (log | bluebubbles)."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from typing import Any

from incidents.settings import load_incidents_config, openclaw_token

_MODULE_LABELS: dict[str, str] = {
    "CWR-INTERFACE": "CWR",
    "CIS-NET-AUTOMATION": "CIS-Net",
    "DATABASE-INSERT": "database import",
    "DATABASE-EXPORT": "database export",
    "MUSO-API": "Muso",
    "ISWC-SERVICE": "ISWC",
}

_TOOL_ACTIONS: dict[str, str] = {
    "cwr_process_acks": "CWR acknowledgement processing",
    "cwr_retrieve": "CWR file retrieval",
    "cwr_dispatch": "CWR dispatch",
    "cwr_clear_local": "CWR local cleanup",
    "cwr_test_generate": "CWR test file generation",
    "cisnet_run_pipeline": "CIS-Net pipeline",
    "cisnet_mlc_automation": "MLC portal automation",
}


def _tool_to_action(tool: str, module: str) -> str:
    if tool in _TOOL_ACTIONS:
        return _TOOL_ACTIONS[tool]
    if tool.startswith("cwr_generate_"):
        society = tool.removeprefix("cwr_generate_").replace("_", " ")
        return f"CWR generation ({society})"
    module_label = _MODULE_LABELS.get(module, module.replace("-", " "))
    readable = tool.replace("_", " ")
    if readable.startswith("cwr "):
        readable = "CWR " + readable[4:]
    return f"{module_label}: {readable}"


def _humanize_error(message: str, kind: str, returncode: Any) -> str:
    if kind == "timeout":
        return "The job timed out after about 2 minutes."

    msg = message.strip()
    msg = re.sub(r"^\[ERR\]\s*", "", msg, flags=re.IGNORECASE)
    msg = re.sub(r"^Error:\s*", "", msg, flags=re.IGNORECASE)

    if not msg or msg == "(no stderr)":
        if returncode is not None and returncode != 0:
            return f"The script failed (exit code {returncode})."
        return "Something went wrong."

    file_missing = re.match(r"(?i)file not found:\s*(.+)", msg)
    if file_missing:
        return f"Couldn't find the file {file_missing.group(1).strip()}."

    if msg.lower().startswith("traceback"):
        return "The script crashed — run orion-incident show for details."

    if msg.endswith("."):
        return msg[0].upper() + msg[1:]
    return msg[0].upper() + msg[1:] + "."


def format_message(record: dict[str, Any]) -> str:
    fp = str(record.get("fingerprint") or "")[:8]
    tool = str(record.get("tool") or "")
    module = str(record.get("module") or "")
    kind = str(record.get("kind") or record.get("severity") or "error")
    raw_message = str(record.get("message") or "")

    cfg = load_incidents_config()
    greeting = str(cfg.get("message_greeting") or "Orion:")
    action = _tool_to_action(tool, module)
    error = _humanize_error(raw_message, kind, record.get("returncode"))
    text = f"{greeting} {action} failed — {error} (ref {fp})"
    return text[:320]


def _send_bluebubbles(
    targets: list[str],
    text: str,
    cfg: dict[str, Any],
) -> tuple[bool, str]:
    """Send via OpenClaw BlueBubbles CLI. Returns (ok, detail)."""
    cli = str(cfg.get("openclaw_cli") or "openclaw")
    channel = str(cfg.get("notify_channel") or "bluebubbles")
    token = openclaw_token()
    if not token:
        return False, "OPENCLAW_TOKEN not set and gateway token not found in openclaw.json"

    env = os.environ.copy()
    env["OPENCLAW_TOKEN"] = token
    prefix = cfg.get("openclaw_path_prefix") or []
    if prefix:
        extra = os.pathsep.join(str(p) for p in prefix if p)
        env["PATH"] = f"{extra}{os.pathsep}{env.get('PATH', '')}"

    errors: list[str] = []
    for target in targets:
        cmd = [cli, "message", "send", "--channel", channel, "--target", str(target), "--message", text]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60, env=env, check=False)
        except FileNotFoundError:
            return False, f"{cli} not found on PATH"
        except subprocess.TimeoutExpired:
            errors.append(f"{target}: timeout")
            continue
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "").strip()[:200]
            errors.append(f"{target}: {err or proc.returncode}")

    if errors:
        return False, "; ".join(errors)
    return True, text


def send_notifications(
    record: dict[str, Any],
    *,
    dry_run: bool = False,
) -> tuple[bool, str]:
    cfg = load_incidents_config()
    targets: list[str] = list(cfg.get("notify_targets") or [])
    backend = str(cfg.get("notify_backend") or "log").strip().lower()
    text = format_message(record)

    if dry_run:
        return True, f"DRY-RUN notify {targets or '(none)'}: {text}"

    if backend == "log" or not targets:
        print(f"[notify] {text}", file=sys.stdout)
        return True, text

    return _send_bluebubbles(targets, text, cfg)


def format_fix_message(
    record: dict[str, Any],
    *,
    success: bool,
    detail: str = "",
    pr_url: str = "",
) -> str:
    fp = str(record.get("fingerprint") or "")[:8]
    tool = str(record.get("tool") or "")
    module = str(record.get("module") or "")
    action = _tool_to_action(tool, module)

    if success:
        text = f"Fixed {action} (ref {fp}). Tests passed."
        if pr_url and pr_url.startswith("http"):
            text += f" PR: {pr_url}"
        elif detail:
            text += f" {detail[:120]}"
    else:
        text = f"Could not auto-fix {action} (ref {fp}). {detail[:180]}"
    return text[:320]


def send_fix_notification(
    record: dict[str, Any],
    *,
    success: bool,
    detail: str = "",
    pr_url: str = "",
    dry_run: bool = False,
) -> tuple[bool, str]:
    cfg = load_incidents_config()
    targets: list[str] = list(cfg.get("notify_targets") or [])
    backend = str(cfg.get("notify_backend") or "log").strip().lower()
    text = format_fix_message(record, success=success, detail=detail, pr_url=pr_url)

    if dry_run:
        return True, f"DRY-RUN fix notify {targets or '(none)'}: {text}"

    if backend == "log" or not targets:
        print(f"[notify] {text}", file=sys.stdout)
        return True, text

    return _send_bluebubbles(targets, text, cfg)
