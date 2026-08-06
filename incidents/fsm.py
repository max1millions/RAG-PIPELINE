"""Persistent JSON FSM for active incidents."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from incidents.settings import ACTIVE_PATH, STATE_PATH, ensure_data_dir


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _parse_ts(ts: str) -> datetime | None:
    if not ts:
        return None
    try:
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        return datetime.fromisoformat(ts)
    except ValueError:
        return None


def _empty_active() -> dict[str, Any]:
    return {"incidents": {}}


def _empty_state() -> dict[str, Any]:
    return {"last_poll_at": None, "last_poll_count": 0, "last_error": None}


def load_active() -> dict[str, Any]:
    ensure_data_dir()
    if not ACTIVE_PATH.exists():
        return _empty_active()
    try:
        data = json.loads(ACTIVE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return _empty_active()
    if "incidents" not in data or not isinstance(data["incidents"], dict):
        data["incidents"] = {}
    return data


def save_active(data: dict[str, Any]) -> None:
    ensure_data_dir()
    ACTIVE_PATH.write_text(json.dumps(data, indent=2, default=str) + "\n", encoding="utf-8")


def load_state() -> dict[str, Any]:
    ensure_data_dir()
    if not STATE_PATH.exists():
        return _empty_state()
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return _empty_state()
    return {**_empty_state(), **data}


def save_state(data: dict[str, Any]) -> None:
    ensure_data_dir()
    STATE_PATH.write_text(json.dumps(data, indent=2, default=str) + "\n", encoding="utf-8")


def get_incident(active: dict[str, Any], fingerprint: str) -> dict[str, Any] | None:
    rec = active.get("incidents", {}).get(fingerprint)
    return rec if isinstance(rec, dict) else None


def should_notify(
    record: dict[str, Any] | None,
    *,
    renotify_every: int,
    renotify_hours: int,
    new_detected_at: str | None = None,
) -> tuple[bool, str]:
    """Decide whether to send an iMessage for this fingerprint.

    Crash/ops path (Mac jsonl): after a successful text the record is ``NOTIFIED``
    and must not be re-texted while the same Mac row (same ``ts``) is still present.
    ``RESOLVED`` only reopens when ``new_detected_at`` differs from the prior
    ``detected_at`` (a genuinely new failure). Callers that omit ``new_detected_at``
    (watchdog) keep the legacy "RESOLVED ⇒ reopen" behavior.
    """
    if record is None:
        return True, "new"
    state = record.get("state")
    # Already delivered once for this open incident — do not spam.
    if state == "NOTIFIED":
        return False, "already_notified"
    if state == "RESOLVED":
        prev_ts = str(record.get("detected_at") or "")
        if new_detected_at is None:
            # Watchdog / legacy callers: treat reappearance after resolve as reopen.
            return True, "reopened"
        if new_detected_at and new_detected_at != prev_ts:
            return True, "reopened"
        return False, "resolved_same_event"
    if state == "ESCALATED" and not record.get("last_notified_at"):
        return True, "escalated_retry"
    seen = int(record.get("seen_count") or 1)
    if seen > 1 and seen % renotify_every == 0:
        return True, "renotify_count"
    detected = _parse_ts(str(record.get("detected_at") or ""))
    last_notified = _parse_ts(str(record.get("last_notified_at") or ""))
    ref = last_notified or detected
    if ref and renotify_hours > 0:
        age_h = (datetime.now(timezone.utc) - ref).total_seconds() / 3600.0
        if age_h >= renotify_hours:
            return True, "renotify_age"
    return False, "duplicate"


def _new_detected_record(normalized: dict[str, Any], *, now: str) -> dict[str, Any]:
    return {
        "incident_id": str(uuid.uuid4()),
        "fingerprint": normalized["fingerprint"],
        "state": "DETECTED",
        "detected_at": normalized.get("detected_at") or now,
        "updated_at": now,
        "source": normalized.get("source"),
        "host": normalized.get("host") or "mac",
        "tool": normalized.get("tool"),
        "module": normalized.get("module"),
        "repos_name": normalized.get("repos_name"),
        "repos_rel_path": normalized.get("repos_rel_path"),
        "severity": normalized.get("severity"),
        "kind": normalized.get("kind"),
        "returncode": normalized.get("returncode"),
        "message": normalized.get("message"),
        "stack_trace": normalized.get("stack_trace"),
        "raw_stderr_tail": normalized.get("raw_stderr_tail"),
        "seen_count": 1,
        "last_notified_at": None,
        "mac_payload": normalized.get("mac_payload"),
    }


def upsert_from_mac(
    active: dict[str, Any],
    normalized: dict[str, Any],
    *,
    renotify_every: int,
    renotify_hours: int,
) -> tuple[dict[str, Any], bool, str]:
    fp = normalized["fingerprint"]
    incidents = active.setdefault("incidents", {})
    existing = incidents.get(fp)
    new_ts = str(normalized.get("detected_at") or "")
    notify, reason = should_notify(
        existing,
        renotify_every=renotify_every,
        renotify_hours=renotify_hours,
        new_detected_at=new_ts or None,
    )

    now = _utc_now_iso()
    if existing is None:
        record = _new_detected_record(normalized, now=now)
        incidents[fp] = record
        return record, notify, "new"

    if existing.get("state") == "RESOLVED":
        if notify and reason == "reopened":
            record = _new_detected_record(normalized, now=now)
            incidents[fp] = record
            return record, True, "reopened"
        # Same Mac row still present after resolve — do not reset or re-text.
        existing["seen_count"] = int(existing.get("seen_count") or 0) + 1
        existing["updated_at"] = now
        existing["mac_payload"] = normalized.get("mac_payload")
        return existing, False, "resolved_same_event"

    existing["seen_count"] = int(existing.get("seen_count") or 0) + 1
    existing["updated_at"] = now
    existing["message"] = normalized.get("message") or existing.get("message")
    existing["mac_payload"] = normalized.get("mac_payload")
    if normalized.get("host"):
        existing["host"] = normalized.get("host")
    return existing, notify, reason


def mark_notifying(record: dict[str, Any]) -> None:
    record["state"] = "NOTIFYING"
    record["updated_at"] = _utc_now_iso()


def mark_notified(record: dict[str, Any]) -> None:
    """Mark that iMessage was delivered; keep open until manual/Mac resolve."""
    record["state"] = "NOTIFIED"
    record["last_notified_at"] = _utc_now_iso()
    record["updated_at"] = record["last_notified_at"]


def mark_resolved(record: dict[str, Any]) -> None:
    record["state"] = "RESOLVED"
    record["updated_at"] = _utc_now_iso()
    if not record.get("last_notified_at"):
        record["last_notified_at"] = record["updated_at"]


def mark_escalated(record: dict[str, Any], error: str) -> None:
    record["state"] = "ESCALATED"
    record["escalation_reason"] = error[:500]
    record["updated_at"] = _utc_now_iso()


def mark_fixing(record: dict[str, Any]) -> None:
    record["state"] = "FIXING"
    record["updated_at"] = _utc_now_iso()


def mark_testing(record: dict[str, Any]) -> None:
    record["state"] = "TESTING"
    record["updated_at"] = _utc_now_iso()


def mark_pushing(record: dict[str, Any]) -> None:
    record["state"] = "PUSHING"
    record["updated_at"] = _utc_now_iso()


def mark_fixed(
    record: dict[str, Any],
    *,
    commit_sha: str = "",
    pr_url: str = "",
    summary: str = "",
) -> None:
    record["state"] = "FIXED"
    record["fix_commit_sha"] = commit_sha
    record["fix_pr_url"] = pr_url
    record["fix_summary"] = summary[:500]
    record["last_notified_at"] = _utc_now_iso()
    record["updated_at"] = record["last_notified_at"]


def mark_fix_failed(record: dict[str, Any], error: str) -> None:
    record["state"] = "FIX_FAILED"
    record["fix_error"] = error[:500]
    record["updated_at"] = _utc_now_iso()


def resolve_by_prefix(active: dict[str, Any], prefix: str) -> dict[str, Any] | None:
    prefix = prefix.lower()
    incidents = active.get("incidents", {})
    matches = [fp for fp in incidents if fp.lower().startswith(prefix)]
    if len(matches) != 1:
        return None
    rec = incidents[matches[0]]
    rec["state"] = "RESOLVED"
    rec["updated_at"] = _utc_now_iso()
    rec["resolved_manually"] = True
    return rec


def list_incidents(active: dict[str, Any], state_filter: str | None = None) -> list[dict[str, Any]]:
    rows = list(active.get("incidents", {}).values())
    if state_filter:
        rows = [r for r in rows if r.get("state") == state_filter]
    rows.sort(key=lambda r: str(r.get("updated_at") or ""), reverse=True)
    return rows
