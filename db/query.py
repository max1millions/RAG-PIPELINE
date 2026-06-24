#!/usr/bin/env python3
"""CLI: run SQL against local MySQL (read-only by default)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

STACK_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(STACK_ROOT))

from common.config import require_feature  # noqa: E402
from db.connect import execute_sql  # noqa: E402


def main() -> int:
    require_feature("local_mysql", "Local MySQL")

    parser = argparse.ArgumentParser(description="Run SQL against local rightstune MySQL")
    parser.add_argument("sql", nargs="?", help="SQL statement or query")
    parser.add_argument("--file", "-f", help="Read SQL from file")
    parser.add_argument("--write", action="store_true", help="Allow write/DDL statements")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    if args.file:
        sql = Path(args.file).read_text(encoding="utf-8")
    elif args.sql:
        sql = args.sql
    else:
        sql = sys.stdin.read()

    if not sql.strip():
        print("ERROR: no SQL provided", file=sys.stderr)
        return 1

    try:
        result = execute_sql(sql, allow_write=args.write)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(result, default=str, indent=2))
        return 0

    if result["type"] == "rows":
        rows = result["rows"]
        if not rows:
            print("(no rows)")
            return 0
        cols = list(rows[0].keys())
        print("\t".join(cols))
        for row in rows:
            print("\t".join(str(row[c]) for c in cols))
        print(f"\n({len(rows)} row(s))")
    else:
        print(f"OK ({result['rowcount']} row(s) affected)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
