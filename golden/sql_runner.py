"""SQL golden case execution."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from common.config import load_config
from db.connect import execute_sql


def _repos_root() -> Path:
    cfg = load_config()
    return Path(cfg["paths"]["repos"])


def _load_sql(case: dict[str, Any]) -> str:
    if case.get("sql"):
        return str(case["sql"]).strip()
    sql_file = case.get("sql_file")
    if not sql_file:
        raise ValueError("sql case missing sql or sql_file")
    path = _repos_root() / str(sql_file).removeprefix("REPOS/")
    if str(sql_file).startswith("SQL-SCRIPTS"):
        path = _repos_root() / str(sql_file)
    elif not path.is_file():
        path = _repos_root() / str(sql_file)
    if not path.is_file():
        raise FileNotFoundError(path)
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = [ln for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("/*")]
    return "\n".join(lines).split(";")[0].strip()


def _scalar(result: dict[str, Any]) -> float:
    if result.get("type") != "rows":
        return float(result.get("rowcount") or 0)
    rows = result.get("rows") or []
    if not rows:
        return 0.0
    row = rows[0]
    for val in row.values():
        if isinstance(val, (int, float)):
            return float(val)
        if isinstance(val, str):
            cleaned = re.sub(r"[^\d.-]", "", val.replace(",", ""))
            if cleaned:
                try:
                    return float(cleaned)
                except ValueError:
                    continue
    return float(len(rows))


def run_sql_scalar(case: dict[str, Any]) -> dict[str, Any]:
    expect = case.get("expect") or {}
    sql = _load_sql(case)
    result = execute_sql(sql, allow_write=False)
    scalar = _scalar(result)
    min_scalar = float(expect.get("min_scalar", 0))
    passed = scalar >= min_scalar
    return {
        "passed": passed,
        "scalar": scalar,
        "min_scalar": min_scalar,
        "row_count": len(result.get("rows") or []),
    }


def run_sql_rows(case: dict[str, Any]) -> dict[str, Any]:
    expect = case.get("expect") or {}
    sql = _load_sql(case)
    result = execute_sql(sql, allow_write=False)
    row_count = len(result.get("rows") or []) if result.get("type") == "rows" else 0
    min_rows = int(expect.get("min_rows", 1))
    max_rows = int(expect.get("max_rows", 999999))
    passed = min_rows <= row_count <= max_rows
    return {
        "passed": passed,
        "row_count": row_count,
        "min_rows": min_rows,
        "max_rows": max_rows,
    }
