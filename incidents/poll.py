"""Poll Mac MCP for recent production incidents."""

from __future__ import annotations

from typing import Any

from common.config import feature_enabled
from incidents.fsm import (
    load_active,
    load_state,
    mark_escalated,
    mark_notifying,
    mark_resolved,
    save_active,
    save_state,
    upsert_from_mac,
)
from incidents.ingest import normalize_mac_row
from incidents.mcp_client import fetch_recent_errors_sync
from incidents.notify import send_notifications
from incidents.remediate import remediate_record
from incidents.settings import load_incidents_config


def run_poll(*, dry_run: bool = False) -> dict[str, Any]:
    if not feature_enabled("incidents_poll") and not dry_run:
        return {
            "ok": True,
            "dry_run": dry_run,
            "skipped": True,
            "reason": "incidents_poll_disabled",
            "fetched": 0,
            "processed": 0,
            "notified": [],
            "remediated": [],
            "errors": [],
        }

    cfg = load_incidents_config()
    limit = int(cfg.get("poll_limit") or 50)
    server = str(cfg.get("mcp_server") or "")
    timeout_s = float(cfg.get("mcp_timeout_s") or 30)
    renotify_every = int(cfg.get("dedupe_renotify_every") or 10)
    renotify_hours = int(cfg.get("dedupe_renotify_hours") or 24)
    auto_fix = feature_enabled("auto_fix_incidents") and not dry_run
    notify = feature_enabled("incidents_notify") and not dry_run

    active = load_active()
    state = load_state()
    result: dict[str, Any] = {
        "ok": True,
        "dry_run": dry_run,
        "auto_fix": auto_fix,
        "notify": notify,
        "fetched": 0,
        "processed": 0,
        "notified": [],
        "remediated": [],
        "skipped": [],
        "errors": [],
    }

    try:
        payload = fetch_recent_errors_sync(
            limit=limit,
            server_name=server or None,
            timeout_s=timeout_s,
        )
    except Exception as exc:
        state["last_poll_at"] = state.get("last_poll_at")
        state["last_error"] = str(exc)
        save_state(state)
        result["ok"] = False
        result["errors"].append(str(exc))
        return result

    incidents = payload.get("incidents") or []
    if not isinstance(incidents, list):
        result["ok"] = False
        result["errors"].append("Invalid MCP response: incidents is not a list")
        return result

    result["fetched"] = len(incidents)
    result["mac_path"] = payload.get("path")

    for row in incidents:
        if not isinstance(row, dict):
            continue
        normalized = normalize_mac_row(row)
        
        # Ignore CMRRA generation timeouts since it is a known long-running background task
        if normalized.get("tool") == "cwr_generate_cmrra" and normalized.get("kind") == "timeout":
            result["skipped"].append({"fingerprint": normalized["fingerprint"][:8], "reason": "ignored_cmrra_timeout"})
            continue

        record, should_send, reason = upsert_from_mac(
            active,
            normalized,
            renotify_every=renotify_every,
            renotify_hours=renotify_hours,
        )
        result["processed"] += 1
        fp = record["fingerprint"]

        if not should_send:
            result["skipped"].append({"fingerprint": fp[:8], "reason": reason})
            continue

        if auto_fix and reason in ("new", "reopened"):
            if dry_run:
                result["remediated"].append(
                    {"fingerprint": fp[:8], "reason": reason, "detail": "DRY-RUN would auto-fix"}
                )
                continue
            fix_result = remediate_record(record, push=feature_enabled("auto_push_orion"))
            if fix_result.get("ok"):
                result["remediated"].append(
                    {
                        "fingerprint": fp[:8],
                        "reason": reason,
                        "detail": fix_result.get("summary") or fix_result.get("pr_url"),
                    }
                )
            else:
                result["errors"].append(
                    f"remediate {fp[:8]}: {fix_result.get('error', 'unknown')}"
                )
            continue

        if not notify:
            result["skipped"].append({"fingerprint": fp[:8], "reason": "notify_disabled"})
            continue

        if not dry_run:
            mark_notifying(record)
        ok, detail = send_notifications(record, dry_run=dry_run)
        if ok:
            if not dry_run:
                mark_resolved(record)
            result["notified"].append(
                {
                    "fingerprint": fp[:8],
                    "reason": reason,
                    "detail": detail,
                }
            )
        else:
            if not dry_run:
                mark_escalated(record, detail)
            result["errors"].append(f"notify {fp[:8]}: {detail}")

    from datetime import datetime, timezone

    state["last_poll_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    state["last_poll_count"] = result["processed"]
    state["last_error"] = result["errors"][0] if result["errors"] else None

    save_active(active)
    save_state(state)
    return result
