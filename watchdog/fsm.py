"""Upsert watchdog anomalies into shared incidents/active.json."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from incidents.fsm import load_active, save_active, should_notify
from incidents.settings import load_incidents_config
from watchdog.fingerprint import compute_fingerprint, metric_signature


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _format_message(check: dict[str, Any], result: dict[str, Any]) -> str:
    template = str(result.get("message_template") or check.get("message_template") or "")
    if not template:
        return f"Watchdog check {result.get('check_id')} failed: {result.get('reason')}"
    return template.format(
        metric_value=result.get("metric_value"),
        baseline_value=result.get("baseline_value"),
        threshold_pct=result.get("threshold_pct") or check.get("threshold_pct"),
        threshold=check.get("threshold"),
        reason=result.get("reason"),
    )


def upsert_anomaly(
    check: dict[str, Any],
    result: dict[str, Any],
    *,
    renotify_every: int,
    renotify_hours: int,
) -> tuple[dict[str, Any], bool, str]:
    check_id = str(result.get("check_id") or check.get("id") or "")
    sig = metric_signature(
        check_id=check_id,
        metric_value=result.get("metric_value", 0),
        bucket=str(result.get("reason") or "")[:120],
    )
    fp = compute_fingerprint(check_id, sig)

    active = load_active()
    incidents = active.setdefault("incidents", {})
    existing = incidents.get(fp)
    now = _utc_now_iso()
    message = _format_message(check, result)

    notify, reason = should_notify(
        existing,
        renotify_every=renotify_every,
        renotify_hours=renotify_hours,
    )

    if existing is None or existing.get("state") in ("ANOMALY_RESOLVED", "RESOLVED"):
        record = {
            "incident_id": str(uuid.uuid4()),
            "fingerprint": fp,
            "state": "ANOMALY_OPEN",
            "detected_at": now,
            "updated_at": now,
            "source": "watchdog",
            "kind": "anomaly",
            "check_id": check_id,
            "severity": result.get("severity") or check.get("severity") or "warning",
            "repos_name": str(check.get("repos_hint") or "SQL-SCRIPTS"),
            "repos_rel_path": "",
            "module": "watchdog",
            "tool": f"watchdog:{check_id}",
            "message": message,
            "metric_value": result.get("metric_value"),
            "baseline_value": result.get("baseline_value"),
            "threshold_pct": result.get("threshold_pct") or check.get("threshold_pct"),
            "assertion_reason": result.get("reason"),
            "seen_count": 1,
            "last_notified_at": None,
        }
        incidents[fp] = record
        save_active(active)
        return record, notify, "new" if existing is None else "reopened"

    existing["seen_count"] = int(existing.get("seen_count") or 0) + 1
    existing["updated_at"] = now
    existing["message"] = message
    existing["metric_value"] = result.get("metric_value")
    existing["baseline_value"] = result.get("baseline_value")
    existing["assertion_reason"] = result.get("reason")
    if existing.get("state") == "ANOMALY_RESOLVED":
        existing["state"] = "ANOMALY_OPEN"
    save_active(active)
    return existing, notify, reason


def mark_anomaly_notified(record: dict[str, Any]) -> None:
    record["state"] = "ANOMALY_NOTIFIED"
    record["last_notified_at"] = _utc_now_iso()
    record["updated_at"] = record["last_notified_at"]


def mark_anomaly_escalated(record: dict[str, Any], error: str) -> None:
    record["state"] = "ANOMALY_ESCALATED"
    record["escalation_reason"] = error[:500]
    record["updated_at"] = _utc_now_iso()


def resolve_anomaly_by_prefix(active: dict[str, Any], prefix: str) -> dict[str, Any] | None:
    prefix = prefix.lower()
    incidents = active.get("incidents", {})
    matches = [
        (fp, rec)
        for fp, rec in incidents.items()
        if fp.lower().startswith(prefix) and rec.get("source") == "watchdog"
    ]
    if len(matches) != 1:
        return None
    _, rec = matches[0]
    rec["state"] = "ANOMALY_RESOLVED"
    rec["updated_at"] = _utc_now_iso()
    rec["resolved_manually"] = True
    return rec


def list_anomalies(active: dict[str, Any], state_filter: str | None = None) -> list[dict[str, Any]]:
    rows = [
        rec
        for rec in active.get("incidents", {}).values()
        if rec.get("source") == "watchdog" or rec.get("kind") == "anomaly"
    ]
    if state_filter:
        rows = [r for r in rows if r.get("state") == state_filter]
    rows.sort(key=lambda r: str(r.get("updated_at") or ""), reverse=True)
    return rows
