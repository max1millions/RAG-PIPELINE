"""Notifications for watchdog anomalies (supports log | bluebubbles backend)."""

from __future__ import annotations

import os
import subprocess
import sys
from typing import Any

from incidents.settings import load_incidents_config, openclaw_token


def _notify_suffix(*, auto_fix_attempted: bool) -> str:
    if auto_fix_attempted:
        return "Auto-fix attempted — review remaining issues."
    return "Review locally — no auto-fix."


def format_anomaly_message(record: dict[str, Any], *, auto_fix_attempted: bool = False) -> str:
    fp = str(record.get("fingerprint") or "")[:8]
    check_id = str(record.get("check_id") or record.get("tool") or "check")
    message = str(record.get("message") or "Local data anomaly detected.")
    attempted = auto_fix_attempted or bool(record.get("auto_fix_attempted"))
    suffix = _notify_suffix(auto_fix_attempted=attempted)
    text = f"Watchdog ({check_id}): {message} (ref {fp}). {suffix}"
    return text[:320]


def format_fix_message(
    record: dict[str, Any],
    *,
    success: bool,
    detail: str = "",
    pr_url: str = "",
) -> str:
    fp = str(record.get("fingerprint") or "")[:8]
    check_id = str(record.get("check_id") or "check")
    if success:
        text = f"Watchdog auto-fix OK ({check_id}, ref {fp})."
        if pr_url:
            text += f" PR: {pr_url}"
        elif detail:
            text += f" {detail[:120]}"
    else:
        text = f"Watchdog auto-fix failed ({check_id}, ref {fp}). {detail[:180]}"
    return text[:320]


def _send_bluebubbles(
    targets: list[str],
    text: str,
    cfg: dict[str, Any],
) -> tuple[bool, str]:
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


def send_anomaly_notification(
    record: dict[str, Any],
    *,
    dry_run: bool = False,
    auto_fix_attempted: bool = False,
) -> tuple[bool, str]:
    cfg = load_incidents_config()
    targets: list[str] = list(cfg.get("notify_targets") or [])
    backend = str(cfg.get("notify_backend") or "log").strip().lower()
    text = format_anomaly_message(record, auto_fix_attempted=auto_fix_attempted)

    if dry_run:
        return True, f"DRY-RUN notify {targets or '(none)'}: {text}"

    if backend == "log" or not targets:
        print(f"[notify] {text}", file=sys.stdout)
        return True, text

    return _send_bluebubbles(targets, text, cfg)


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
