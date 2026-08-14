"""LLM interpretation of production incident streams for iMessage.

Every Mac incident (cron, MCP, API gateway) is classified from the recorded
stdio stream. Known ops failures (pull-all, disk, MySQL, backup, API gateway)
use a programmed iMessage template and skip the LLM. Everything else:

- ``code`` — application/repo bug (traceback, validator, logic error).
- ``host`` — environment (network, auth, disk, database, missing credentials, OS).

Auto-fix is a separate gate (for example CWR-INTERFACE when overlay enables it).
This module only explains the failure and chooses code vs host.

Falls back to heuristics when the LLM is unavailable.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

from incidents.templates import match_incident_template

FIX_CODE = "code"
FIX_HOST = "host"
# Back-compat aliases for records written before the generic classifier.
FIX_CWR_MODULE = FIX_CODE
FIX_MAC_HOST = FIX_HOST

_HOST_HINTS = (
    "authentication failed",
    "connection refused",
    "connection timed out",
    "operation timed out",
    "network/io error",
    "network/io",
    "ssh error",
    "no route to host",
    "no space left",
    "disk quota",
    "can't connect",
    "cannot connect",
    "failed to connect",
    "access denied for user",
    "incomplete credentials",
    "paramiko",
    "banner",
    "errno 61",
    "errno 60",
    "mamp",
    "broken pipe",
    "connection reset",
    "name or service not known",
    "nodename nor servname",
    "fast-forward",
    "non-fast-forward",
)

_CODE_HINTS = (
    "traceback",
    "validationerror",
    "validation failed",
    "generator(s) failed",
    "integrityerror",
    "cwr-validator",
    "syntaxerror",
    "attributeerror",
    "keyerror",
    "typeerror",
    "valueerror",
    "assertionerror",
    "jsondecodeerror",
    "nameerror",
    "indexerror",
    "modulenotfounderror",
)

_SYSTEM = """You interpret production failures for a short iMessage to the operator.

You are given the incident stream (tool, module, kind, returncode, message, stderr, stdout, argv). Use that stream — do not invent files or causes that are not supported by it.

Classify fix_target:
- code: a bug in application/repo code (traceback, validator, logic error, exception in Python). A developer could patch this in git.
- host: environment — network, SSH/SFTP auth, disk, database down, missing credentials, cron/OS, remote service timeouts. Not a git patch.

If unsure, choose host.

Write user_message as one or two short sentences, coworker tone, no emoji, no stack traces, no absolute filesystem paths:
- Start with this greeting exactly (including punctuation): {greeting}
- Explain what broke in plain language from the stream.
- If fix_target is host, say it is happening on the production Mac and is not a repo patch.
- Do not promise that you are fixing it.

Max 320 characters. Return JSON only:
{{"fix_target":"code"|"host","user_message":"..."}}
"""


def operator_greeting() -> str:
    """Greeting prefix from overlay incidents.yaml (public default: 'Hey,')."""
    try:
        from incidents.settings import load_incidents_config

        raw = str(load_incidents_config().get("message_greeting") or "Hey,")
    except Exception:
        raw = "Hey,"
    g = raw.strip() or "Hey,"
    if g[-1] not in ",:!":
        g += ","
    return g


def is_cwr_automation(record: dict[str, Any]) -> bool:
    """True for CWR-INTERFACE cron jobs and MCP CWR tools."""
    if str(record.get("module") or "") == "CWR-INTERFACE":
        return True
    if str(record.get("repos_name") or "") == "CWR-INTERFACE":
        return True
    tool = str(record.get("tool") or "")
    return tool.startswith("cwr_") or tool.startswith("cron_cwr_")


def is_cwr_code_fix(record: dict[str, Any]) -> bool:
    """True when this incident should be orion-fixed in CWR-INTERFACE."""
    target = str(record.get("fix_target") or "")
    if target not in (FIX_CODE, "cwr_module"):
        return False
    return str(record.get("repos_name") or record.get("module") or "") == "CWR-INTERFACE"


def mac_notify_only(record: dict[str, Any]) -> bool:
    """Mac ops stay notify-only, except classified CWR-INTERFACE code bugs."""
    if is_cwr_code_fix(record):
        return False
    tool = str(record.get("tool") or "")
    return (
        record.get("host") == "mac"
        or tool.startswith("cron_")
        or tool == "api_gateway"
    )


def with_fixing_clause(message: str) -> str:
    """Append an 'I'm fixing it' sentence when auto-fix is about to run."""
    text = (message or "").strip()
    if not text:
        return "I'm fixing it."
    if "i'm fixing it" in text.lower():
        return text
    return text.rstrip(".") + ". I'm fixing it."


def _blob(record: dict[str, Any]) -> str:
    parts = [
        str(record.get("message") or ""),
        str(record.get("stack_trace") or ""),
        str(record.get("raw_stderr_tail") or ""),
        str(record.get("kind") or ""),
        str(record.get("tool") or ""),
        str(record.get("module") or ""),
    ]
    payload = record.get("mac_payload")
    if isinstance(payload, dict):
        parts.append(" ".join(str(x) for x in (payload.get("argv") or [])))
        parts.append(str(payload.get("stderr") or ""))
        parts.append(str(payload.get("stdout") or ""))
    return " ".join(parts).lower()


def classify_heuristic(record: dict[str, Any]) -> str:
    """Conservative classify: unknown connection-ish failures stay host."""
    blob = _blob(record)
    kind = str(record.get("kind") or "")
    if kind == "timeout":
        return FIX_HOST
    if any(h in blob for h in _HOST_HINTS):
        return FIX_HOST
    if any(h in blob for h in _CODE_HINTS):
        return FIX_CODE
    return FIX_HOST


def _action_label(record: dict[str, Any]) -> str:
    tool = str(record.get("tool") or "")
    module = str(record.get("module") or "")
    labels = {
        "cron_cwr_auto_submit": "weekly CWR submit",
        "cron_cwr_generate": "CWR file generation",
        "cron_cwr_dispatch": "CWR SFTP dispatch",
        "cron_cwr_auto_acks": "hourly CWR ACK poll",
        "cron_cwr_retrieve": "CWR SFTP retrieve",
        "cron_cwr_process_acks": "CWR acknowledgement processing",
        "cron_update_popularity_score": "popularity score update",
        "cron_email_reader": "email reader",
        "cron_run_cron_job": "maintenance SQL",
        "cron_sync_backup": "backup sync",
        "cron_pull_all": "repo pull-all",
        "cron_lod_pending_check": "pending LOD check",
        "cwr_dispatch": "CWR dispatch",
        "cwr_retrieve": "CWR retrieve",
        "cwr_process_acks": "CWR acknowledgement processing",
        "cwr_generate_all": "CWR generate-all",
        "api_gateway": "API gateway",
        "cisnet_run_pipeline": "CIS-Net pipeline",
        "cisnet_mlc_automation": "MLC portal automation",
    }
    if tool in labels:
        return labels[tool]
    if tool.startswith("cwr_generate_"):
        return f"CWR generation ({tool.removeprefix('cwr_generate_').replace('_', ' ')})"
    if tool.startswith("cron_"):
        return tool.removeprefix("cron_").replace("_", " ")
    if module:
        return module.replace("-", " ")
    return tool.replace("_", " ") or "a job"


def fallback_user_message(
    record: dict[str, Any],
    fix_target: str,
    greeting: str | None = None,
) -> str:
    greet = greeting if greeting is not None else operator_greeting()
    action = _action_label(record)
    raw = str(record.get("message") or "").strip()
    if raw and raw != "(no stderr)" and not raw.lower().startswith("traceback"):
        snippet = raw.splitlines()[0][:120].rstrip(".")
        what = snippet[0].lower() + snippet[1:] if snippet else action
    else:
        what = action
    if fix_target in (FIX_CODE, "cwr_module"):
        return f"{greet} {what} — looks like a code bug."
    return (
        f"{greet} {what} on the production Mac. This looks like a machine, "
        "network, or credentials issue — not something I can patch in the repo."
    )


def _llm_interpret(record: dict[str, Any]) -> dict[str, str] | None:
    try:
        from langchain_anthropic import ChatAnthropic
        from langchain_core.messages import HumanMessage, SystemMessage

        from common.config import anthropic_api_key, load_config
    except Exception:
        return None

    try:
        anthropic_api_key()
        model = str((load_config().get("models") or {}).get("triage") or "claude-sonnet-4-6")
        payload = record.get("mac_payload") if isinstance(record.get("mac_payload"), dict) else {}
        argv = payload.get("argv") if isinstance(payload, dict) else []
        greeting = operator_greeting()
        user = json.dumps(
            {
                "tool": record.get("tool"),
                "module": record.get("module"),
                "kind": record.get("kind"),
                "returncode": record.get("returncode"),
                "message": str(record.get("message") or "")[:800],
                "stderr_tail": str(record.get("raw_stderr_tail") or "")[:2500],
                "stdout_tail": str((payload or {}).get("stdout") or "")[-2000:],
                "argv": argv,
            },
            default=str,
        )
        system = _SYSTEM.format(greeting=greeting)
        chat = ChatAnthropic(model=model, max_tokens=400, temperature=0)
        resp = chat.invoke([SystemMessage(content=system), HumanMessage(content=user)])
        content = resp.content
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(block.get("text", ""))
                elif hasattr(block, "text"):
                    parts.append(block.text)
            content = "".join(parts)
        text = str(content).strip()
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        data = json.loads(text)
        target = str(data.get("fix_target") or "").strip()
        if target in ("cwr_module", "mac_host"):
            target = FIX_CODE if target == "cwr_module" else FIX_HOST
        message = str(data.get("user_message") or "").strip()
        if target not in (FIX_CODE, FIX_HOST) or not message:
            return None
        greet_head = greeting.split(",")[0].lower()
        if not message.lower().startswith(greet_head):
            message = f"{greeting} {message[0].lower() + message[1:]}" if message else greeting
        return {"fix_target": target, "user_message": message[:320]}
    except Exception:
        return None


def interpret_incident(record: dict[str, Any]) -> dict[str, str]:
    """Return ``fix_target`` and ``user_message`` (template, LLM, else heuristics)."""
    greeting = operator_greeting()
    templated = match_incident_template(record, greeting)
    if templated:
        return {
            "fix_target": templated["fix_target"],
            "user_message": templated["user_message"][:320],
        }
    if os.environ.get("ORION_INCIDENT_LLM", "1").strip().lower() not in (
        "0",
        "false",
        "no",
    ):
        llm = _llm_interpret(record)
        if llm:
            return llm
    target = classify_heuristic(record)
    return {
        "fix_target": target,
        "user_message": fallback_user_message(record, target, greeting=greeting)[:320],
    }
