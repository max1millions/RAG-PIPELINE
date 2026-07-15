"""Auto-remediate watchdog anomalies via orion-fix (RAG + LangGraph)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from codeflow.fix import invoke_fix
from common.config import feature_enabled, load_config
from incidents.fsm import load_active, save_active
from watchdog.checks import auto_fix_config
from watchdog.fsm import mark_anomaly_fix_failed, mark_anomaly_fixed, mark_anomaly_fixing
from watchdog.notify import send_fix_notification
from watchdog.settings import load_watchdog_config


def _repo_path(repos_name: str) -> Path:
    cfg = load_config()
    return Path(cfg["paths"]["repos"]) / repos_name


def resolve_fix_repo(record: dict[str, Any], check: dict[str, Any] | None = None) -> str:
    """Prefer auto_fix.repo, then record.repos_name, then check repos_hint."""
    if check:
        cfg = auto_fix_config(check)
        if cfg.get("repo"):
            return str(cfg["repo"])
    name = str(record.get("repos_name") or "").strip()
    if name and name != "UNKNOWN":
        return name
    if check:
        hint = str(check.get("repos_hint") or "").strip()
        if hint:
            return hint
    return "SQL-SCRIPTS"


def build_remediation_request(
    record: dict[str, Any],
    *,
    repos_name: str,
) -> str:
    """Build orion-fix prompt from a watchdog anomaly record."""
    fp = str(record.get("fingerprint") or "")[:8]
    check_id = str(record.get("check_id") or "")
    message = str(record.get("message") or "")
    reason = str(record.get("assertion_reason") or "")
    sql_file = str(record.get("sql_file") or record.get("repos_rel_path") or "")
    sample = record.get("sample_rows") or []

    parts = [
        f"Fix local data anomaly from SQL validation (watchdog ref {fp}).",
        f"Repo: {repos_name}.",
        "Fix the pipeline/code that produces bad data — do not write to production MySQL.",
        "Prefer REPOS code changes; if SQL remediation artifacts are needed, follow SQL-GENERATION conventions "
        "(UPDATE_DATABASE.sql for local review, never apply to production via MCP).",
    ]
    if check_id:
        parts.append(f"Watchdog check_id: {check_id}.")
    if sql_file:
        parts.append(f"Validation SQL: {sql_file}.")
    if message:
        parts.append(f"Anomaly: {message}.")
    if reason:
        parts.append(f"Assertion: {reason}.")
    if sample:
        try:
            sample_text = json.dumps(sample, indent=2, default=str)
        except (TypeError, ValueError):
            sample_text = str(sample)
        parts.append(f"Sample failing rows (truncated):\n{sample_text[:3000]}")
    return "\n".join(parts)


def _check_def_for_record(record: dict[str, Any]) -> dict[str, Any]:
    check_id = str(record.get("check_id") or "")
    cfg = load_watchdog_config()
    for c in cfg.get("checks") or []:
        if isinstance(c, dict) and str(c.get("id")) == check_id:
            return c
    return {"id": check_id, "repos_hint": record.get("repos_name")}


def remediate_record(
    record: dict[str, Any],
    *,
    push: bool = False,
    dry_run: bool = False,
    check: dict[str, Any] | None = None,
) -> dict[str, Any]:
    fp = str(record.get("fingerprint") or "")
    fp_short = fp[:8]
    check_def = check or _check_def_for_record(record)
    repos_name = resolve_fix_repo(record, check_def)

    if not repos_name or repos_name == "UNKNOWN":
        return {"ok": False, "error": "no repos_name on anomaly", "fingerprint": fp_short}

    repo_path = _repo_path(repos_name)
    if not repo_path.is_dir():
        return {"ok": False, "error": f"local repo missing: {repo_path}", "fingerprint": fp_short}

    request = build_remediation_request(record, repos_name=repos_name)

    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "fingerprint": fp_short,
            "repos_name": repos_name,
            "request_preview": request[:500],
        }

    active = load_active()
    mark_anomaly_fixing(record)
    save_active(active)

    try:
        final = invoke_fix(
            request=request,
            repo=repos_name,
            repo_path=repo_path,
            push=push or feature_enabled("auto_push_orion"),
            incident_fingerprint=fp,
        )
    except Exception as exc:
        active = load_active()
        mark_anomaly_fix_failed(record, str(exc))
        save_active(active)
        send_fix_notification(record, success=False, detail=str(exc)[:300])
        return {"ok": False, "error": str(exc), "fingerprint": fp_short}

    success = bool(final.get("approved") or final.get("commit_sha"))
    active = load_active()

    if success:
        mark_anomaly_fixed(
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

    err = str(final.get("summary") or final.get("error") or "fix failed")
    mark_anomaly_fix_failed(record, err)
    save_active(active)
    send_fix_notification(record, success=False, detail=err[:300])
    return {"ok": False, "error": err, "fingerprint": fp_short}


def remediate_by_prefix(prefix: str, *, push: bool = False, dry_run: bool = False) -> dict[str, Any]:
    active = load_active()
    prefix_lower = prefix.lower()
    matches = [
        rec
        for fp, rec in active.get("incidents", {}).items()
        if fp.lower().startswith(prefix_lower)
        and (rec.get("source") == "watchdog" or rec.get("kind") == "anomaly")
    ]
    if len(matches) != 1:
        return {"ok": False, "error": f"expected 1 watchdog anomaly for {prefix!r}, got {len(matches)}"}
    return remediate_record(matches[0], push=push, dry_run=dry_run)
