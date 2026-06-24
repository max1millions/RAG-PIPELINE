#!/usr/bin/env python3
"""Run golden fixture cases."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

STACK_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(STACK_ROOT))

from common.config import feature_enabled, load_config  # noqa: E402
from golden.cwr_runner import run_shell  # noqa: E402
from golden.manifest import list_cases, load_manifest  # noqa: E402
from golden.sql_runner import run_sql_rows, run_sql_scalar  # noqa: E402


def _run_case(case: dict[str, Any]) -> dict[str, Any]:
    case_id = str(case.get("id") or "unknown")
    kind = str(case.get("kind") or "")
    out: dict[str, Any] = {"id": case_id, "kind": kind, "passed": False, "skipped": False}

    if kind == "smoke":
        try:
            load_manifest(reload=True)
            out["passed"] = True
        except Exception as exc:
            out["error"] = str(exc)
        return out

    if kind == "sql_scalar":
        if not feature_enabled("local_mysql"):
            out["skipped"] = True
            out["note"] = "local_mysql disabled"
            out["passed"] = True
            return out
        try:
            detail = run_sql_scalar(case)
            out.update(detail)
        except Exception as exc:
            err = str(exc)
            if "Can't connect" in err or "Access denied" in err or "2003" in err:
                out["skipped"] = True
                out["note"] = "mysql unavailable"
                out["passed"] = True
            else:
                out["error"] = err
        return out

    if kind == "sql_rows":
        if not feature_enabled("local_mysql"):
            out["skipped"] = True
            out["note"] = "local_mysql disabled"
            out["passed"] = True
            return out
        try:
            detail = run_sql_rows(case)
            out.update(detail)
        except Exception as exc:
            err = str(exc)
            if "Can't connect" in err or "Access denied" in err or "2003" in err:
                out["skipped"] = True
                out["note"] = "mysql unavailable"
                out["passed"] = True
            else:
                out["error"] = err
        return out

    if kind == "shell":
        detail = run_shell(case)
        out.update(detail)
        return out

    out["error"] = f"unknown kind {kind!r}"
    return out


def run_all(*, repo: str | None = None, case_id: str | None = None) -> dict[str, Any]:
    load_config()
    cases = list_cases(repo=repo)
    if case_id:
        cases = [c for c in cases if str(c.get("id")) == case_id]
    results = [_run_case(c) for c in cases]
    failed = [r for r in results if not r.get("passed") and not r.get("skipped")]
    return {
        "ok": len(failed) == 0,
        "total": len(results),
        "failed": len(failed),
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Orion golden fixture tests")
    parser.add_argument("--repo", help="Filter cases by repo name")
    parser.add_argument("--case", dest="case_id", help="Run single case id")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    summary = run_all(repo=args.repo, case_id=args.case_id)
    if args.json:
        print(json.dumps(summary, indent=2, default=str))
    else:
        print(f"Golden: {summary.get('total')} cases, {summary.get('failed')} failed")
        for r in summary.get("results") or []:
            status = "SKIP" if r.get("skipped") else ("PASS" if r.get("passed") else "FAIL")
            print(f"  {status} {r.get('id')}")
            if r.get("error"):
                print(f"    {r['error']}")
    return 0 if summary.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
