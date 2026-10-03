#!/usr/bin/env python3
"""Copy BlueBubbles texts sent to Max into Orion's OpenClaw daily memory.

Automated notifications (incidents, LOD, CWR acks, pull-all, CIS-Net, and
the rest) are delivered with ``openclaw message send``. That CLI does not
load plugins and does not write the agent session, so Orion cannot recall
them later. This sync reads the BlueBubbles history for Max's 1:1 chat and
appends each outbound text to ``memory/YYYY-MM-DD.md``, which ``memory_search``
indexes.

Credentials stay in ``~/.openclaw/openclaw.json``. Nothing in this file
prints the BlueBubbles password or the gateway token.

CLI:
    python3 notifications/bb_memory.py sync
    python3 notifications/bb_memory.py sync --dry-run
    python3 notifications/bb_memory.py status

Environment (all optional):
    OPENCLAW_STATE_DIR     default ``~/.openclaw``
    OPENCLAW_WORKSPACE     default from ``agents.list`` id ``main``
    ORION_OVERLAY_ROOT     incidents.yaml notify_targets
    BB_MEMORY_TIMEZONE     default ``America/Chicago``
    BB_MEMORY_HANDLES      comma-separated E.164 overrides
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

# Fictional NANP number. Production overrides this with overlay notify_targets
# or BB_MEMORY_HANDLES so a real handset number is not stored in the public repo.
DEFAULT_HANDLE = "+15551234567"
DEFAULT_TIMEZONE = "America/Chicago"
SECTION_HEADING = "## iMessage to Max (BlueBubbles)"
HINT_HEADING = "## iMessage memory"
HINT_BLOCK = """## iMessage memory

- Texts sent to Max on iMessage (BlueBubbles), including automated notifications (LOD, CWR acknowledgements, incidents, pull-all, backups, CIS-Net, and similar), are appended to `memory/YYYY-MM-DD.md` under "iMessage to Max (BlueBubbles)". When Max asks about a notification, an iMessage, or something that was texted to him, use `memory_search` before answering.
"""
PAGE_LIMIT = 200
TEXT_LIMIT = 4000
_PHONE_RE = re.compile(r"\+\d{8,15}")


class BlueBubblesError(RuntimeError):
    """BlueBubbles request failed. The message never includes the password."""


def state_dir() -> Path:
    return Path(os.environ.get("OPENCLAW_STATE_DIR", Path.home() / ".openclaw"))


def timezone() -> ZoneInfo:
    name = os.environ.get("BB_MEMORY_TIMEZONE", DEFAULT_TIMEZONE).strip() or DEFAULT_TIMEZONE
    return ZoneInfo(name)


def load_openclaw_config() -> dict[str, Any]:
    path = state_dir() / "openclaw.json"
    if not path.is_file():
        raise FileNotFoundError(f"OpenClaw config not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def main_workspace(cfg: dict[str, Any]) -> Path:
    override = os.environ.get("OPENCLAW_WORKSPACE", "").strip()
    if override:
        return Path(override)
    agents = ((cfg.get("agents") or {}).get("list")) or []
    for agent in agents:
        if isinstance(agent, dict) and agent.get("id") == "main" and agent.get("workspace"):
            return Path(str(agent["workspace"]))
    return state_dir() / "workspace"


def memory_dir(cfg: dict[str, Any]) -> Path:
    return main_workspace(cfg) / "memory"


def memory_hint_path(cfg: dict[str, Any]) -> Path:
    return main_workspace(cfg) / "MEMORY.md"


def runtime_dir() -> Path:
    path = state_dir() / "bb-max-memory"
    path.mkdir(parents=True, exist_ok=True)
    return path


def state_path() -> Path:
    return runtime_dir() / "state.json"


def log_path() -> Path:
    path = state_dir() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path / "bb-max-memory.log"


def bluebubbles_account(cfg: dict[str, Any]) -> tuple[str, str]:
    channel = ((cfg.get("channels") or {}).get("bluebubbles")) or {}
    base = str(channel.get("serverUrl") or "").strip().rstrip("/")
    password = str(channel.get("password") or "")
    if not base or not password:
        raise BlueBubblesError(
            "channels.bluebubbles.serverUrl and password must be set in openclaw.json"
        )
    return base, password


def handles_from_incidents_text(text: str) -> list[str]:
    """Return E.164 values listed under ``notify_targets`` only."""
    found: list[str] = []
    in_block = False
    for line in text.splitlines():
        if re.match(r"^notify_targets\s*:", line):
            found.extend(_PHONE_RE.findall(line))
            in_block = True
            continue
        if in_block:
            if re.match(r"^\S", line):
                break
            found.extend(_PHONE_RE.findall(line))
    return found


def load_handles(_cfg: dict[str, Any] | None = None) -> list[str]:
    """Max's DM handle, plus any ``notify_targets`` phones.

    ``BB_MEMORY_HANDLES`` replaces the list when set (comma-separated E.164).
    """
    override = os.environ.get("BB_MEMORY_HANDLES", "").strip()
    if override:
        handles = [part.strip() for part in override.split(",") if part.strip()]
        return _dedupe(handles)
    handles = [DEFAULT_HANDLE]
    overlay = Path(
        os.environ.get(
            "ORION_OVERLAY_ROOT",
            Path.home() / ".openclaw" / "local" / "rag-pipeline",
        )
    )
    incidents = overlay / "config" / "incidents.yaml"
    if incidents.is_file():
        handles.extend(handles_from_incidents_text(incidents.read_text(encoding="utf-8")))
    # allowFrom lists every person Orion may talk to. Only Max's DM and
    # incidents.yaml notify_targets are ingested. ``_cfg`` is accepted so
    # callers can pass the loaded OpenClaw config without using allowFrom.
    return _dedupe(handles)


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            out.append(value)
    return out


def chat_fields(message: dict[str, Any]) -> tuple[str, str]:
    chats = message.get("chats")
    chat: dict[str, Any] | None = None
    if isinstance(chats, list) and chats and isinstance(chats[0], dict):
        chat = chats[0]
    elif isinstance(message.get("chat"), dict):
        chat = message["chat"]
    if not chat:
        return "", ""
    return str(chat.get("guid") or ""), str(chat.get("chatIdentifier") or "")


def is_group_guid(guid: str) -> bool:
    parts = guid.split(";")
    return len(parts) >= 3 and parts[1] == "+"


def is_direct_to_handle(guid: str, identifier: str, handle: str) -> bool:
    """True for a 1:1 BlueBubbles chat with ``handle``.

    BlueBubbles guids look like ``any;-;+15551234567`` for a DM and
    ``any;+;<group-id>`` for a group. Group chats are not notification DMs.
    """
    handle = handle.strip()
    if not handle or is_group_guid(guid):
        return False
    if identifier == handle:
        return True
    return guid.endswith(";" + handle)


def is_direct_to_any(guid: str, identifier: str, handles: list[str]) -> bool:
    return any(is_direct_to_handle(guid, identifier, handle) for handle in handles)


def message_guid(message: dict[str, Any]) -> str:
    guid = message.get("guid") or message.get("messageId") or message.get("id")
    return str(guid or "").strip()


def is_reaction(message: dict[str, Any]) -> bool:
    associated = message.get("associatedMessageGuid") or message.get("associated_message_guid")
    return bool(str(associated or "").strip())


def message_text(message: dict[str, Any]) -> str:
    raw = message.get("text") or ""
    return str(raw).strip()


def should_remember(message: dict[str, Any], handles: list[str]) -> bool:
    """Outbound text in Max's 1:1 chat, skipping tapbacks and empty bubbles."""
    if not message.get("isFromMe"):
        return False
    if is_reaction(message):
        return False
    if not message_text(message):
        return False
    if not message_guid(message):
        return False
    guid, identifier = chat_fields(message)
    return is_direct_to_any(guid, identifier, handles)


def message_datetime(message: dict[str, Any], tz: ZoneInfo) -> datetime | None:
    raw = message.get("dateCreated")
    if raw is None:
        raw = message.get("date")
    if not isinstance(raw, (int, float)):
        return None
    value = float(raw)
    if value > 1e18:
        ms = value / 1e6
    elif value > 1e15:
        ms = value / 1e3
    elif value > 1e12:
        ms = value
    else:
        ms = value * 1000
    return datetime.fromtimestamp(ms / 1000, tz)


def entry_token(guid: str) -> str:
    return hashlib.sha256(guid.encode("utf-8")).hexdigest()[:16]


def format_entry(when: datetime, text: str, guid: str) -> str:
    flat = re.sub(r"\s+", " ", text).strip()
    if len(flat) > TEXT_LIMIT:
        flat = flat[: TEXT_LIMIT - 1] + "…"
    stamp = when.strftime("%Y-%m-%d %H:%M %Z")
    token = entry_token(guid)
    return f"<!-- bb:{token} -->\n- **{stamp}** — {flat}\n"


def append_entry(memory_root: Path, when: datetime, entry: str) -> bool:
    """Append one note. Return False when that guid is already in the file."""
    memory_root.mkdir(parents=True, exist_ok=True)
    day = when.strftime("%Y-%m-%d")
    path = memory_root / f"{day}.md"
    token_line = entry.splitlines()[0]
    existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    if token_line in existing:
        return False
    if not existing.strip():
        body = f"# {day}\n\n{SECTION_HEADING}\n\n{entry}"
    elif SECTION_HEADING not in existing:
        body = existing.rstrip() + f"\n\n{SECTION_HEADING}\n\n{entry}"
    else:
        body = existing.rstrip() + "\n" + entry
    path.write_text(body, encoding="utf-8")
    return True


def ensure_memory_hint(path: Path) -> bool:
    """Add the recall instruction to MEMORY.md once. Return True if written."""
    existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    if HINT_HEADING in existing:
        return False
    prefix = existing.rstrip()
    body = (prefix + "\n\n" if prefix else "") + HINT_BLOCK.strip() + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return True


def load_state() -> dict[str, Any]:
    path = state_path()
    if not path.is_file():
        return {"version": 1, "seen": [], "index_dirty": False}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"version": 1, "seen": [], "index_dirty": True}
    if not isinstance(data, dict):
        return {"version": 1, "seen": [], "index_dirty": True}
    data.setdefault("version", 1)
    data.setdefault("seen", [])
    data.setdefault("index_dirty", False)
    return data


def save_state(data: dict[str, Any]) -> None:
    path = state_path()
    payload = json.dumps(data, indent=2, sort_keys=True) + "\n"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(payload, encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def _scrub(message: str, password: str) -> str:
    if not password:
        return message
    redacted = message.replace(password, "***")
    quoted = urllib.parse.quote(password, safe="")
    if quoted:
        redacted = redacted.replace(quoted, "***")
    return redacted


def query_messages(
    base: str,
    password: str,
    *,
    offset: int,
    limit: int = PAGE_LIMIT,
) -> tuple[list[dict[str, Any]], int | None]:
    """Page ``isFromMe`` messages, newest first. Returns (rows, total)."""
    body = json.dumps(
        {
            "limit": limit,
            "offset": offset,
            "sort": "DESC",
            "with": ["chat"],
            "where": [
                {
                    "statement": "message.isFromMe = :fromMe",
                    "args": {"fromMe": 1},
                }
            ],
        }
    ).encode("utf-8")
    url = f"{base}/api/v1/message/query?password={urllib.parse.quote(password, safe='')}"
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:180]
        raise BlueBubblesError(
            f"BlueBubbles query HTTP {exc.code}: {_scrub(detail, password)}"
        ) from None
    except urllib.error.URLError as exc:
        raise BlueBubblesError(
            f"BlueBubbles query failed: {_scrub(str(exc.reason), password)}"
        ) from None
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise BlueBubblesError("BlueBubbles query returned no data array")
    messages = [row for row in rows if isinstance(row, dict)]
    meta = payload.get("metadata") if isinstance(payload, dict) else None
    total = meta.get("total") if isinstance(meta, dict) else None
    return messages, total if isinstance(total, int) else None


def iter_new_messages(
    base: str,
    password: str,
    seen: set[str],
    *,
    backfill: bool,
) -> list[dict[str, Any]]:
    """Return unseen from-me rows.

    After the first backfill, stop at the first page whose guids are all
    already recorded. New texts sort to the front, so one page is enough
    once history has been ingested.
    """
    collected: list[dict[str, Any]] = []
    offset = 0
    while True:
        rows, _total = query_messages(base, password, offset=offset)
        if not rows:
            break
        page_guids = [message_guid(row) for row in rows]
        page_guids = [guid for guid in page_guids if guid]
        unseen_rows = [row for row in rows if message_guid(row) and message_guid(row) not in seen]
        collected.extend(unseen_rows)
        if not backfill and page_guids and all(guid in seen for guid in page_guids):
            break
        if len(rows) < PAGE_LIMIT:
            break
        if not backfill and not unseen_rows:
            break
        offset += len(rows)
        if offset > 20000:
            break
    return collected


def remember_messages(
    messages: list[dict[str, Any]],
    *,
    handles: list[str],
    memory_root: Path,
    tz: ZoneInfo,
    seen: set[str],
    dry_run: bool,
) -> tuple[int, int]:
    """Append ingestible messages. Mark every returned guid seen.

    Guids are marked even when the row is not written (other chats, reactions)
    so later polls do not walk the whole history again.
    """
    writable: list[tuple[datetime, str, str]] = []
    for message in messages:
        guid = message_guid(message)
        if guid:
            seen.add(guid)
        if not should_remember(message, handles):
            continue
        when = message_datetime(message, tz)
        if when is None:
            continue
        writable.append((when, message_text(message), guid))
    writable.sort(key=lambda item: item[0])
    written = 0
    for when, text, guid in writable:
        entry = format_entry(when, text, guid)
        if dry_run:
            written += 1
            continue
        if append_entry(memory_root, when, entry):
            written += 1
    # ``written`` is new bullets. ``len(writable)`` is every new Max DM text,
    # including ones already in the file after a crash. Either one means the
    # index may be missing those notes.
    return written, len(writable)


def reindex_memory(agent: str = "main") -> tuple[bool, str]:
    env = os.environ.copy()
    bin_dir = str(Path.home() / ".npm-global" / "bin")
    env["PATH"] = bin_dir + os.pathsep + env.get("PATH", "")
    try:
        proc = subprocess.run(
            ["openclaw", "memory", "index", "--agent", agent],
            capture_output=True,
            text=True,
            timeout=900,
            env=env,
            check=False,
        )
    except FileNotFoundError:
        return False, "openclaw not found on PATH"
    except subprocess.TimeoutExpired:
        return False, "openclaw memory index timed out"
    detail = (proc.stderr or proc.stdout or "").strip()
    detail = re.sub(r"\x1b\[[0-9;]*m", "", detail)
    # Drop the noisy doctor banner; keep the last meaningful lines.
    lines = [line.strip() for line in detail.splitlines() if line.strip()]
    tail = " | ".join(lines[-4:])[:500]
    if proc.returncode != 0:
        return False, tail or f"exit {proc.returncode}"
    return True, tail or "indexed"


def log_line(message: str) -> None:
    stamp = datetime.now(timezone()).strftime("%Y-%m-%d %H:%M:%S %Z")
    line = f"{stamp} {message}"
    print(line)
    with log_path().open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def acquire_lock():
    path = runtime_dir() / "sync.lock"
    handle = path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        return None
    return handle


def sync(*, dry_run: bool = False, backfill: bool = False) -> int:
    lock = None if dry_run else acquire_lock()
    if lock is None and not dry_run:
        log_line("sync skipped; another run holds the lock")
        return 0
    try:
        cfg = load_openclaw_config()
        base, password = bluebubbles_account(cfg)
        handles = load_handles(cfg)
        root = memory_dir(cfg)
        tz = timezone()
        state = load_state()
        seen = {str(guid) for guid in state.get("seen") or [] if guid}
        full = backfill or not seen
        fresh = iter_new_messages(base, password, seen, backfill=full)
        written, pending = remember_messages(
            fresh,
            handles=handles,
            memory_root=root,
            tz=tz,
            seen=seen,
            dry_run=dry_run,
        )
        if dry_run:
            log_line(
                f"dry-run handles={','.join(handles)} unseen_rows={len(fresh)} "
                f"would_write={written}"
            )
            return 0
        hint = ensure_memory_hint(memory_hint_path(cfg))
        state["seen"] = sorted(seen)
        state["index_dirty"] = bool(pending) or bool(state.get("index_dirty")) or hint
        save_state(state)
        indexed = True
        index_detail = "not needed"
        if state["index_dirty"]:
            indexed, index_detail = reindex_memory()
            state["index_dirty"] = not indexed
            save_state(state)
        log_line(
            f"sync wrote={written} seen={len(seen)} hint={int(hint)} "
            f"indexed={int(indexed)} {index_detail}"
        )
        return 0 if indexed else 1
    finally:
        if lock is not None:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
            lock.close()


def status() -> int:
    state = load_state()
    seen = state.get("seen") or []
    print(f"seen={len(seen)} index_dirty={bool(state.get('index_dirty'))}")
    print(f"state={state_path()}")
    cfg_path = state_dir() / "openclaw.json"
    if cfg_path.is_file():
        cfg = load_openclaw_config()
        print(f"memory_dir={memory_dir(cfg)}")
        print(f"handles={','.join(load_handles(cfg))}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Ingest BlueBubbles texts sent to Max into OpenClaw daily memory."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sync_parser = sub.add_parser("sync", help="Ingest new texts and reindex memory")
    sync_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Count texts that would be written; do not touch memory or state",
    )
    sync_parser.add_argument(
        "--backfill",
        action="store_true",
        help="Walk the full from-me history instead of stopping at the first known page",
    )
    sub.add_parser("status", help="Show ingest state without contacting BlueBubbles")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "status":
            return status()
        return sync(dry_run=args.dry_run, backfill=args.backfill)
    except (BlueBubblesError, FileNotFoundError, OSError, json.JSONDecodeError) as exc:
        log_line(f"sync failed: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
