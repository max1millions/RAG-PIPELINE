#!/usr/bin/env python3
"""CLI: Phase 0 incident supervisor."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

STACK_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(STACK_ROOT))

from common.config import require_feature  # noqa: E402
from incidents.fsm import (  # noqa: E402
    list_incidents,
    load_active,
    resolve_by_prefix,
    save_active,
)
from incidents.poll import run_poll  # noqa: E402
from incidents.remediate import remediate_by_prefix  # noqa: E402


def cmd_poll(args: argparse.Namespace) -> int:
    result = run_poll(dry_run=args.dry_run)
    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        if not result.get("ok"):
            print(f"ERROR: {result.get('errors')}", file=sys.stderr)
        print(
            f"Fetched {result.get('fetched')} processed {result.get('processed')} "
            f"notified {len(result.get('notified') or [])} "
            f"remediated {len(result.get('remediated') or [])} "
            f"skipped {len(result.get('skipped') or [])}"
        )
        for item in result.get("remediated") or []:
            print(f"  FIX {item.get('fingerprint')} ({item.get('reason')}): {item.get('detail')}")
        for item in result.get("notified") or []:
            print(f"  NOTIFY {item.get('fingerprint')} ({item.get('reason')}): {item.get('detail')}")
        for err in result.get("errors") or []:
            print(f"  ERROR: {err}", file=sys.stderr)
    return 0 if result.get("ok") else 1


def cmd_list(args: argparse.Namespace) -> int:
    active = load_active()
    rows = list_incidents(active, args.state)
    if args.json:
        print(json.dumps(rows, indent=2, default=str))
    else:
        if not rows:
            print("(no incidents)")
            return 0
        for rec in rows:
            fp = str(rec.get("fingerprint") or "")[:8]
            print(
                f"{fp} {rec.get('state')} {rec.get('tool')} {rec.get('module')} "
                f"seen={rec.get('seen_count')} {rec.get('message', '')[:80]}"
            )
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    active = load_active()
    prefix = args.prefix.lower()
    matches = [
        rec
        for fp, rec in active.get("incidents", {}).items()
        if fp.lower().startswith(prefix)
    ]
    if len(matches) != 1:
        print(f"ERROR: expected 1 match for prefix {args.prefix!r}, got {len(matches)}", file=sys.stderr)
        return 1
    rec = matches[0]
    if args.json:
        print(json.dumps(rec, indent=2, default=str))
    else:
        print(json.dumps(rec, indent=2, default=str))
    return 0


def cmd_resolve(args: argparse.Namespace) -> int:
    active = load_active()
    rec = resolve_by_prefix(active, args.prefix)
    if rec is None:
        print(f"ERROR: no unique incident for prefix {args.prefix!r}", file=sys.stderr)
        return 1
    save_active(active)
    if args.json:
        print(json.dumps(rec, indent=2, default=str))
    else:
        print(f"Resolved {str(rec.get('fingerprint'))[:8]}")
    return 0


def cmd_remediate(args: argparse.Namespace) -> int:
    result = remediate_by_prefix(
        args.prefix,
        push=args.push,
        dry_run=args.dry_run,
    )
    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        if result.get("ok"):
            print(f"OK {result.get('fingerprint')}: {result.get('summary') or result.get('request_preview')}")
            if result.get("pr_url"):
                print(f"PR: {result['pr_url']}")
        else:
            print(f"ERROR: {result.get('error')}", file=sys.stderr)
    return 0 if result.get("ok") else 1


def main() -> int:
    require_feature("incidents", "Incident supervisor")

    parser = argparse.ArgumentParser(description="Orion Phase 0 incident supervisor")
    sub = parser.add_subparsers(dest="command", required=True)

    p_poll = sub.add_parser("poll", help="Poll Mac MCP for recent errors")
    p_poll.add_argument("--dry-run", action="store_true", help="Skip iMessage notifications")
    p_poll.add_argument("--json", action="store_true")
    p_poll.set_defaults(func=cmd_poll)

    p_list = sub.add_parser("list", help="List tracked incidents")
    p_list.add_argument("--state", choices=[
        "DETECTED", "NOTIFYING", "RESOLVED", "ESCALATED",
        "FIXING", "TESTING", "PUSHING", "FIXED", "FIX_FAILED",
    ])
    p_list.add_argument("--json", action="store_true")
    p_list.set_defaults(func=cmd_list)

    p_show = sub.add_parser("show", help="Show incident by fingerprint prefix")
    p_show.add_argument("prefix")
    p_show.add_argument("--json", action="store_true")
    p_show.set_defaults(func=cmd_show)

    p_resolve = sub.add_parser("resolve", help="Manually mark incident RESOLVED")
    p_resolve.add_argument("prefix")
    p_resolve.add_argument("--json", action="store_true")
    p_resolve.set_defaults(func=cmd_resolve)

    p_remed = sub.add_parser("remediate", help="Run orion-fix for an incident fingerprint prefix")
    p_remed.add_argument("prefix")
    p_remed.add_argument("--push", action="store_true", help="Push to origin/orion after tests pass")
    p_remed.add_argument("--dry-run", action="store_true")
    p_remed.add_argument("--json", action="store_true")
    p_remed.set_defaults(func=cmd_remediate)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
