"""Hand an allowlisted code incident to the sandboxed Mac Cursor agent.

This path does not call ``orion-fix`` and does not use the Mac bridge.
``remediate_record`` still refuses Mac-origin incidents.
"""

from __future__ import annotations

from typing import Any

from codeflow.mac_sandbox import incident_brief, submit_handoff
from codeflow.sandbox_policy import PolicyError
from incidents.fsm import (
    load_active,
    mark_fix_failed,
    mark_fixed,
    mark_fixing,
    mark_pushing,
    mark_testing,
    save_active,
)
from incidents.notify import send_fix_notification


def sandbox_remediate_record(record: dict[str, Any]) -> dict[str, Any]:
    """SSH the incident to ``orion-mac-handoff``. The agent opens a PR, not a merge."""
    fp = str(record.get("fingerprint") or "")
    fp_short = fp[:8]
    try:
        incident_brief(record)
    except PolicyError as exc:
        return {"ok": False, "error": str(exc), "fingerprint": fp_short}

    active = load_active()
    mark_fixing(record)
    mark_testing(record)
    save_active(active)

    try:
        final = submit_handoff(record)
    except Exception as exc:
        active = load_active()
        mark_fix_failed(record, str(exc))
        save_active(active)
        send_fix_notification(record, success=False, detail=str(exc)[:300])
        return {"ok": False, "error": str(exc), "fingerprint": fp_short}

    active = load_active()
    if final.get("ok") and final.get("commit_sha"):
        mark_pushing(record)
        mark_fixed(
            record,
            commit_sha=str(final.get("commit_sha") or ""),
            pr_url=str(final.get("pr_url") or ""),
            summary=str(final.get("summary") or ""),
        )
        save_active(active)
        send_fix_notification(
            record,
            success=True,
            detail=str(final.get("summary") or ""),
            pr_url=str(final.get("pr_url") or ""),
        )
        return {
            "ok": True,
            "fingerprint": fp_short,
            "summary": final.get("summary"),
            "pr_url": final.get("pr_url"),
            "commit_sha": final.get("commit_sha"),
        }

    err = str(final.get("error") or "sandbox handoff failed")
    mark_fix_failed(record, err)
    save_active(active)
    send_fix_notification(record, success=False, detail=err[:300])
    return {"ok": False, "error": err, "fingerprint": fp_short}
