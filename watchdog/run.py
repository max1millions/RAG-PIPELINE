#!/usr/bin/env python3
"""CLI: Phase 4 local watchdog (proactive anomaly detection)."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

STACK_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(STACK_ROOT))

from common.config import feature_enabled, require_feature  # noqa: E402
from incidents.fsm import load_active, save_active  # noqa: E402
from incidents.settings import load_incidents_config  # noqa: E402
from watchdog.baseline import reset_baseline  # noqa: E402
from watchdog.checks import (  # noqa: E402
    auto_fix_config,
    run_all_checks,
    run_auto_fix,
    run_check,
    should_attempt_auto_fix,
)
from watchdog.fsm import (  # noqa: E402
    list_anomalies,
    mark_anomaly_escalated,
    mark_anomaly_notified,
    resolve_anomaly_by_prefix,
    upsert_anomaly,
)
from watchdog.notify import send_anomaly_notification  # noqa: E402
from watchdog.remediate import remediate_by_prefix, remediate_record  # noqa: E402
from watchdog.settings import RUN_HISTORY_PATH, ensure_data_dir, load_watchdog_config  # noqa: E402


def _save_run_history(result: dict) -> None:
    ensure_data_dir()
    history: list = []
    if RUN_HISTORY_PATH.exists():
        try:
            history = json.loads(RUN_HISTORY_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            history = []
    if not isinstance(history, list):
        history = []
    history.append(result)
    history = history[-100:]
    RUN_HISTORY_PATH.write_text(json.dumps(history, indent=2, default=str) + "\n", encoding="utf-8")


def run_watchdog(*, dry_run: bool = False) -> dict:
    require_feature("watchdog", "Watchdog")
    cfg_inc = load_incidents_config()
    renotify_every = int(cfg_inc.get("dedupe_renotify_every") or 10)
    renotify_hours = int(cfg_inc.get("dedupe_renotify_hours") or 24)
    notify_enabled = feature_enabled("watchdog_notify") and not dry_run

    load_watchdog_config()
    checks_cfg = load_watchdog_config().get("checks") or []
    check_by_id = {str(c.get("id")): c for c in checks_cfg if isinstance(c, dict)}

    result: dict = {
        "ok": True,
        "dry_run": dry_run,
        "ran_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "checks_run": 0,
        "failures": [],
        "notified": [],
        "skipped_notify": [],
        "auto_fixed": [],
        "errors": [],
    }

    for check_result in run_all_checks():
        result["checks_run"] += 1
        check_id = str(check_result.get("check_id") or "")
        check_def = check_by_id.get(check_id) or {"id": check_id}

        if not check_result.get("ok"):
            result["errors"].append(f"{check_id}: {check_result.get('error')}")
            continue

        if check_result.get("passed"):
            continue

        auto_fix_attempted = False
        if should_attempt_auto_fix(check_def) and not dry_run:
            af = auto_fix_config(check_def)
            if af.get("mode") == "code":
                # Persist anomaly first so remediate has a record + sample rows.
                record, _, _ = upsert_anomaly(
                    check_def,
                    check_result,
                    renotify_every=renotify_every,
                    renotify_hours=renotify_hours,
                )
                fix_result = remediate_record(record, check=check_def)
                auto_fix_attempted = True
                record["auto_fix_attempted"] = True
                if fix_result.get("ok"):
                    result["auto_fixed"].append(
                        {
                            "check_id": check_id,
                            "mode": "code",
                            "detail": fix_result.get("summary") or fix_result.get("commit_sha") or "ok",
                        }
                    )
                    check_result = run_check(check_def)
                    check_result["auto_fix_attempted"] = True
                    if check_result.get("passed"):
                        continue
                else:
                    result["errors"].append(
                        f"auto_fix code {check_id}: {fix_result.get('error', 'unknown')}"
                    )
            else:
                fix_ok, fix_detail = run_auto_fix(check_def)
                auto_fix_attempted = fix_ok
                if fix_ok:
                    result["auto_fixed"].append(
                        {"check_id": check_id, "mode": "sql", "detail": fix_detail}
                    )
                    check_result = run_check(check_def)
                    check_result["auto_fix_attempted"] = True
                    if check_result.get("passed"):
                        continue
                else:
                    result["errors"].append(f"auto_fix sql {check_id}: {fix_detail}")

        check_result["auto_fix_attempted"] = auto_fix_attempted
        result["failures"].append(check_result)
        record, should_send, reason = upsert_anomaly(
            check_def,
            check_result,
            renotify_every=renotify_every,
            renotify_hours=renotify_hours,
        )
        record["auto_fix_attempted"] = auto_fix_attempted

        if not should_send:
            result["skipped_notify"].append(
                {"fingerprint": str(record.get("fingerprint"))[:8], "reason": reason}
            )
            continue

        if not notify_enabled:
            result["skipped_notify"].append(
                {
                    "fingerprint": str(record.get("fingerprint"))[:8],
                    "reason": "dry_run_or_notify_disabled",
                }
            )
            continue

        ok, detail = send_anomaly_notification(
            record, dry_run=dry_run, auto_fix_attempted=auto_fix_attempted
        )
        active = load_active()
        if ok:
            mark_anomaly_notified(record)
            save_active(active)
            result["notified"].append(
                {"fingerprint": str(record.get("fingerprint"))[:8], "check_id": check_id, "detail": detail}
            )
        else:
            mark_anomaly_escalated(record, detail)
            save_active(active)
            result["errors"].append(f"notify {check_id}: {detail}")

    if result["errors"]:
        result["ok"] = False

    _save_run_history(result)
    return result


def cmd_run(args: argparse.Namespace) -> int:
    result = run_watchdog(dry_run=args.dry_run)
    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(
            f"Checks {result.get('checks_run')} failures {len(result.get('failures') or [])} "
            f"notified {len(result.get('notified') or [])}"
        )
        for f in result.get("failures") or []:
            print(f"  FAIL {f.get('check_id')}: {f.get('reason')}")
        for n in result.get("notified") or []:
            print(f"  NOTIFY {n.get('fingerprint')} ({n.get('check_id')})")
        for item in result.get("auto_fixed") or []:
            print(f"  AUTO-FIX {item.get('check_id')} ({item.get('mode')}): {item.get('detail')}")
        for err in result.get("errors") or []:
            print(f"  ERROR: {err}", file=sys.stderr)
    return 0 if result.get("ok") else 1


def cmd_list(args: argparse.Namespace) -> int:
    active = load_active()
    rows = list_anomalies(active, args.state)
    if args.json:
        print(json.dumps(rows, indent=2, default=str))
    else:
        if not rows:
            print("(no watchdog anomalies)")
            return 0
        for rec in rows:
            fp = str(rec.get("fingerprint") or "")[:8]
            print(
                f"{fp} {rec.get('state')} {rec.get('check_id')} "
                f"seen={rec.get('seen_count')} {str(rec.get('message', ''))[:80]}"
            )
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    active = load_active()
    prefix = args.prefix.lower()
    matches = [
        rec
        for fp, rec in active.get("incidents", {}).items()
        if fp.lower().startswith(prefix)
        and (rec.get("source") == "watchdog" or rec.get("kind") == "anomaly")
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
    rec = resolve_anomaly_by_prefix(active, args.prefix)
    if rec is None:
        print(f"ERROR: no unique watchdog anomaly for prefix {args.prefix!r}", file=sys.stderr)
        return 1
    save_active(active)
    if args.json:
        print(json.dumps(rec, indent=2, default=str))
    else:
        print(f"Resolved anomaly {str(rec.get('fingerprint'))[:8]}")
    return 0


def cmd_remediate(args: argparse.Namespace) -> int:
    """Manual RAG/orion-fix path — does not require watchdog_auto_fix."""
    require_feature("langgraph_multiagent", "LangGraph multi-agent")
    result = remediate_by_prefix(args.prefix, push=args.push, dry_run=args.dry_run)
    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        if result.get("ok"):
            if result.get("dry_run"):
                print(f"DRY-RUN remediate {result.get('fingerprint')} repo={result.get('repos_name')}")
                print(result.get("request_preview", "")[:500])
            else:
                print(
                    f"Remediated {result.get('fingerprint')}: "
                    f"{result.get('summary') or result.get('commit_sha') or 'ok'}"
                )
                if result.get("pr_url"):
                    print(f"  PR: {result['pr_url']}")
        else:
            print(f"ERROR: {result.get('error')}", file=sys.stderr)
    return 0 if result.get("ok") else 1


def cmd_baseline_reset(args: argparse.Namespace) -> int:
    reset_baseline(args.check_id)
    if args.json:
        print(json.dumps({"ok": True, "check_id": args.check_id}, indent=2))
    else:
        label = args.check_id or "all"
        print(f"Reset watchdog baseline: {label}")
    return 0


def main() -> int:
    require_feature("watchdog", "Watchdog")

    parser = argparse.ArgumentParser(description="Orion Phase 4 local watchdog")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="Run all watchdog checks")
    p_run.add_argument("--dry-run", action="store_true", help="No iMessage notifications")
    p_run.add_argument("--json", action="store_true")
    p_run.set_defaults(func=cmd_run)

    p_list = sub.add_parser("list", help="List watchdog anomalies")
    p_list.add_argument(
        "--state",
        choices=["ANOMALY_OPEN", "ANOMALY_NOTIFIED", "ANOMALY_RESOLVED", "ANOMALY_ESCALATED"],
    )
    p_list.add_argument("--json", action="store_true")
    p_list.set_defaults(func=cmd_list)

    p_show = sub.add_parser("show", help="Show anomaly by fingerprint prefix")
    p_show.add_argument("prefix")
    p_show.add_argument("--json", action="store_true")
    p_show.set_defaults(func=cmd_show)

    p_resolve = sub.add_parser("resolve", help="Mark anomaly ANOMALY_RESOLVED")
    p_resolve.add_argument("prefix")
    p_resolve.add_argument("--json", action="store_true")
    p_resolve.set_defaults(func=cmd_resolve)

    p_remed = sub.add_parser(
        "remediate",
        help="Run orion-fix for a watchdog anomaly (manual; does not need watchdog_auto_fix)",
    )
    p_remed.add_argument("prefix", help="Fingerprint prefix")
    p_remed.add_argument("--dry-run", action="store_true")
    p_remed.add_argument("--push", action="store_true", help="Push after commit when auto_push allows")
    p_remed.add_argument("--json", action="store_true")
    p_remed.set_defaults(func=cmd_remediate)

    p_bl = sub.add_parser("baseline", help="Baseline management")
    bl_sub = p_bl.add_subparsers(dest="baseline_cmd", required=True)
    p_reset = bl_sub.add_parser("reset", help="Reset stored baselines")
    p_reset.add_argument("--check-id", dest="check_id", default=None)
    p_reset.add_argument("--json", action="store_true")
    p_reset.set_defaults(func=cmd_baseline_reset)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
