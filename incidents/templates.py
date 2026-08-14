"""Programmed iMessage templates for known Mac ops failures.

Matched before the triage LLM so predictable errors (pull-all, disk, MySQL,
backup, API gateway) skip model calls. Variables such as the failing repo
name are filled from the recorded stream. Unmatched incidents still go
through LLM interpretation or heuristics.
"""

from __future__ import annotations

import re
from typing import Any

FIX_HOST = "host"

_PULL_FAIL = re.compile(
    r"FAIL\s+(.+?)\s+\((?:main|submodules|origin/main --ff-only)\)"
)
_MYSQL_HINTS = (
    "can't connect to mysql",
    "cannot connect to mysql",
    "failed to connect to database",
    "access denied for user",
    "can't connect to local mysql",
    "unable to connect to mysql",
)
_DISK_HINTS = ("no space left", "disk quota", "no space left on device")


def _stream(record: dict[str, Any]) -> str:
    parts = [
        str(record.get("message") or ""),
        str(record.get("raw_stderr_tail") or ""),
        str(record.get("kind") or ""),
        str(record.get("tool") or ""),
        str(record.get("module") or ""),
    ]
    payload = record.get("mac_payload")
    if isinstance(payload, dict):
        parts.append(str(payload.get("stderr") or ""))
        parts.append(str(payload.get("stdout") or ""))
    return " ".join(parts)


def _join_repos(names: list[str]) -> str:
    unique: list[str] = []
    seen: set[str] = set()
    for name in names:
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        unique.append(name)
    if not unique:
        return "a repo"
    if len(unique) == 1:
        return f"the {unique[0]} repo"
    if len(unique) == 2:
        return f"the {unique[0]} and {unique[1]} repos"
    head = ", ".join(unique[:-1])
    return f"the {head}, and {unique[-1]} repos"


def _pull_all_template(record: dict[str, Any], greeting: str) -> dict[str, str] | None:
    if str(record.get("tool") or "") != "cron_pull_all":
        return None
    blob = _stream(record)
    names = [m.group(1).strip() for m in _PULL_FAIL.finditer(blob)]
    repos = _join_repos(names)
    return {
        "fix_target": FIX_HOST,
        "user_message": (
            f"{greeting} the scheduled pull-all job failed because {repos} "
            "couldn't be pulled on the production Mac. This is an environment "
            "issue, not a repo patch."
        ),
    }


def _named_host_job(
    tool: str,
    greeting: str,
    body: str,
    record: dict[str, Any],
) -> dict[str, str] | None:
    if str(record.get("tool") or "") != tool:
        return None
    return {
        "fix_target": FIX_HOST,
        "user_message": f"{greeting} {body}",
    }


def _disk_template(record: dict[str, Any], greeting: str) -> dict[str, str] | None:
    blob = _stream(record).lower()
    if not any(h in blob for h in _DISK_HINTS):
        return None
    return {
        "fix_target": FIX_HOST,
        "user_message": (
            f"{greeting} the production Mac is out of disk space. "
            "This is an environment issue, not a repo patch."
        ),
    }


def _mysql_template(record: dict[str, Any], greeting: str) -> dict[str, str] | None:
    blob = _stream(record).lower()
    if "traceback" in blob:
        return None
    if not any(h in blob for h in _MYSQL_HINTS):
        return None
    return {
        "fix_target": FIX_HOST,
        "user_message": (
            f"{greeting} a scheduled job failed because MySQL on the production "
            "Mac could not be reached. This is an environment issue, not a repo patch."
        ),
    }


def match_incident_template(
    record: dict[str, Any],
    greeting: str,
) -> dict[str, str] | None:
    """Return fix_target + user_message when a programmed template matches."""
    matchers = (
        _pull_all_template,
        lambda rec, greet: _named_host_job(
            "cron_sync_backup",
            greet,
            "the scheduled backup sync job failed on the production Mac. "
            "This is an environment issue, not a repo patch.",
            rec,
        ),
        lambda rec, greet: _named_host_job(
            "api_gateway",
            greet,
            "the API gateway on the production Mac exited unexpectedly. "
            "This is an environment issue, not a repo patch.",
            rec,
        ),
        _disk_template,
        _mysql_template,
    )
    for matcher in matchers:
        hit = matcher(record, greeting)
        if hit:
            message = hit["user_message"].strip()
            hit["user_message"] = message[:320]
            return hit
    return None
