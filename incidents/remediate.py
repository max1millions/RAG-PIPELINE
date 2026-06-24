"""Auto-remediate incidents via orion-fix."""

from __future__ import annotations

from typing import Any

from codeflow.fix import invoke_fix
from common.config import feature_enabled, load_config
from incidents.fsm import (
    load_active,
    mark_escalated,
    mark_fixed,
    mark_fix_failed,
    mark_fixing,
    mark_pushing,
    mark_testing,
    save_active,
)
from incidents.notify import send_fix_notification
from incidents.settings import load_path_map


def _maybe_index_playbook(record: dict[str, Any]) -> None:
    cfg = load_config()
    if not cfg.get("features", {}).get("rag"):
        return
    if not (cfg.get("rag") or {}).get("index_playbooks_on_fix", True):
        return
    try:
        from rag.indexers.playbooks import upsert_record

        upsert_record(record)
    except Exception:
        pass


def _repo_path(repos_name: str):
    cfg = load_config()
    from pathlib import Path

    return Path(cfg["paths"]["repos"]) / repos_name


def remediate_record(
    record: dict[str, Any],
    *,
    push: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    fp = str(record.get("fingerprint") or "")
    repos_name = str(record.get("repos_name") or "")
    if not repos_name or repos_name == "UNKNOWN":
        return {"ok": False, "error": "no repos_name on incident", "fingerprint": fp[:8]}

    path_map = load_path_map()
    if repos_name not in (path_map.get("modules") or {}):
        return {"ok": False, "error": f"repos {repos_name} not in path_map", "fingerprint": fp[:8]}

    repo_path = _repo_path(repos_name)
    if not repo_path.is_dir():
        return {"ok": False, "error": f"local repo missing: {repo_path}", "fingerprint": fp[:8]}

    message = str(record.get("message") or "")
    stack = str(record.get("stack_trace") or record.get("raw_stderr_tail") or "")
    rel = str(record.get("repos_rel_path") or "")
    fp_short = fp[:8]

    request_parts = [
        f"Fix production failure (incident ref {fp_short}).",
        f"Repo: {repos_name}.",
    ]
    if rel:
        request_parts.append(f"Primary file hint: {rel}.")
    if message:
        request_parts.append(f"Error: {message}.")
    if stack:
        request_parts.append(f"Stack trace / stderr:\n{stack[:3000]}")
    request = "\n".join(request_parts)

    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "fingerprint": fp_short,
            "repos_name": repos_name,
            "request_preview": request[:500],
        }

    active = load_active()
    mark_fixing(record)
    save_active(active)

    mark_testing(record)
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
        mark_fix_failed(record, str(exc))
        save_active(active)
        send_fix_notification(record, success=False, detail=str(exc)[:300])
        return {"ok": False, "error": str(exc), "fingerprint": fp_short}

    success = bool(final.get("approved") or final.get("commit_sha"))
    active = load_active()

    if success:
        mark_pushing(record)
        mark_fixed(
            record,
            commit_sha=str(final.get("commit_sha") or ""),
            pr_url=str(final.get("pr_url") or ""),
            summary=str(final.get("summary") or ""),
        )
        save_active(active)
        _maybe_index_playbook(record)
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
    mark_fix_failed(record, err)
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
    ]
    if len(matches) != 1:
        return {"ok": False, "error": f"expected 1 match for {prefix!r}, got {len(matches)}"}
    return remediate_record(matches[0], push=push, dry_run=dry_run)
