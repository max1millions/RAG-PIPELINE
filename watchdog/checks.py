"""Run configured watchdog SQL checks against local MySQL."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from common.config import feature_enabled
from db.connect import execute_sql, execute_sql_with_preamble
from watchdog.baseline import get_baseline, set_baseline
from watchdog.settings import load_watchdog_config, repos_root


def _split_sql_statements(text: str) -> list[str]:
    """Split on semicolons outside block comments (best-effort)."""
    parts: list[str] = []
    buf: list[str] = []
    in_block = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("/*"):
            in_block = True
        if in_block:
            if "*/" in stripped:
                in_block = False
            continue
        if stripped.startswith("--") or not stripped:
            continue
        buf.append(line)
        if stripped.endswith(";"):
            stmt = "\n".join(buf).strip()
            if stmt:
                parts.append(stmt.rstrip(";").strip())
            buf = []
    if buf:
        tail = "\n".join(buf).strip()
        if tail:
            parts.append(tail.rstrip(";").strip())
    return parts


SAMPLE_ROWS_LIMIT = 5


def _normalize_fix_sql_file(raw: Any) -> str | None:
    if raw is None:
        return None
    text = str(raw).strip()
    if not text or text.lower() in ("null", "none"):
        return None
    return text


def auto_fix_config(check: dict[str, Any]) -> dict[str, Any]:
    """Normalize per-check auto_fix block.

    mode:
      - sql  — apply fix_sql_file (default when fix_sql_file is set)
      - code — invoke RAG + orion-fix (default when enabled without fix_sql_file)
    """
    raw = check.get("auto_fix") if isinstance(check.get("auto_fix"), dict) else {}
    fix_file = _normalize_fix_sql_file(raw.get("fix_sql_file"))
    mode_raw = str(raw.get("mode") or "").strip().lower()
    if mode_raw in ("code", "sql"):
        mode = mode_raw
    elif fix_file:
        mode = "sql"
    else:
        mode = "code"
    repo = raw.get("repo")
    repo_str = str(repo).strip() if repo not in (None, "", "null", "none") else None
    return {
        "enabled": bool(raw.get("enabled")),
        "mode": mode,
        "repo": repo_str,
        "fix_sql_file": fix_file,
    }


def should_attempt_auto_fix(check: dict[str, Any]) -> bool:
    if not feature_enabled("watchdog_auto_fix"):
        return False
    cfg = auto_fix_config(check)
    if not cfg["enabled"]:
        return False
    if cfg["mode"] == "sql":
        return bool(cfg.get("fix_sql_file"))
    # mode == code: dual-gate only (repo resolved at remediate time)
    return True


def _sample_rows(rows: list[Any], *, limit: int = SAMPLE_ROWS_LIMIT) -> list[Any]:
    if not rows:
        return []
    return list(rows[:limit])


def _load_sql_file_statements(sql_file: str) -> list[str]:
    path = repos_root() / str(sql_file)
    if not path.is_file():
        raise FileNotFoundError(f"SQL file not found: {path}")
    text = path.read_text(encoding="utf-8", errors="replace")
    statements = _split_sql_statements(text)
    if not statements:
        raise ValueError(f"No SQL statements in {path}")
    return statements


def _load_sql(check: dict[str, Any]) -> str:
    if check.get("sql"):
        return str(check["sql"]).strip()
    sql_file = check.get("sql_file")
    if not sql_file:
        raise ValueError(f"check {check.get('id')}: missing sql or sql_file")
    statements = _load_sql_file_statements(str(sql_file))
    idx = int(check.get("sql_statement_index") or 0)
    if idx >= len(statements):
        raise IndexError(
            f"sql_statement_index {idx} out of range for {sql_file} ({len(statements)} stmts)"
        )
    return statements[idx]


def _scalar_from_result(result: dict[str, Any]) -> float:
    if result.get("type") == "rows":
        rows = result.get("rows") or []
        if not rows:
            return 0.0
        row = rows[0]
        if len(row) == 1:
            val = next(iter(row.values()))
            try:
                return float(val)
            except (TypeError, ValueError):
                return 0.0
        for key in ("cnt", "count", "total_revenue", "total", "value"):
            if key in row:
                try:
                    return float(row[key])
                except (TypeError, ValueError):
                    pass
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
    return float(result.get("rowcount") or 0)


def _evaluate_assertion(
    check: dict[str, Any],
    metric_value: float,
    *,
    row_count: int,
) -> tuple[bool, str, float | None]:
    assertion = str(check.get("assertion") or "max_rows")
    check_id = str(check.get("id") or "")

    if assertion == "max_rows":
        threshold = float(check.get("threshold") or 0)
        effective = row_count if row_count > 1 else metric_value
        failed = effective > threshold
        reason = f"count {effective} > max {threshold}"
        return (not failed, reason, None)

    if assertion == "min_scalar":
        threshold = float(check.get("threshold") or 1)
        failed = metric_value < threshold
        reason = f"scalar {metric_value} < min {threshold}"
        return (not failed, reason, None)

    if assertion == "baseline_delta_pct":
        threshold_pct = float(check.get("threshold_pct") or 25)
        baseline = get_baseline(check_id)
        if baseline is None or baseline == 0:
            set_baseline(check_id, metric_value)
            return True, "baseline established", metric_value
        delta_pct = abs(metric_value - baseline) / abs(baseline) * 100.0
        failed = delta_pct > threshold_pct
        reason = f"delta {delta_pct:.2f}% > {threshold_pct}% (baseline={baseline}, current={metric_value})"
        if not failed:
            set_baseline(check_id, metric_value)
        return (not failed, reason, baseline)

    return True, "unknown assertion (skipped)", None


def _execute_check_sql(check: dict[str, Any], sql: str) -> dict[str, Any]:
    preamble = check.get("sql_preamble")
    if preamble:
        return execute_sql_with_preamble(sql, preamble=str(preamble), allow_write=False)
    return execute_sql(sql, allow_write=False)


def run_check(check: dict[str, Any]) -> dict[str, Any]:
    check_id = str(check.get("id") or "unknown")
    out: dict[str, Any] = {
        "check_id": check_id,
        "ok": True,
        "passed": True,
        "error": "",
        "auto_fix_attempted": False,
    }
    try:
        sql = _load_sql(check)
        result = _execute_check_sql(check, sql)
        row_count = len(result.get("rows") or []) if result.get("type") == "rows" else 0
        metric_value = (
            _scalar_from_result(result)
            if result.get("type") == "rows"
            else float(result.get("rowcount") or 0)
        )
        if assertion := check.get("assertion"):
            if assertion == "max_rows" and result.get("type") == "rows" and row_count == 1:
                metric_value = _scalar_from_result(result)
        passed, reason, baseline_value = _evaluate_assertion(
            check,
            metric_value,
            row_count=row_count if check.get("assertion") == "max_rows" else int(metric_value),
        )
        display_metric = metric_value
        if check.get("assertion") == "max_rows" and row_count > 1:
            display_metric = float(row_count)
        sample: list[Any] = []
        if not passed and result.get("type") == "rows":
            sample = _sample_rows(list(result.get("rows") or []))
        out.update(
            {
                "passed": passed,
                "ok": True,
                "metric_value": display_metric,
                "row_count": row_count,
                "baseline_value": baseline_value,
                "reason": reason,
                "severity": check.get("severity") or "warning",
                "repos_hint": check.get("repos_hint") or "",
                "message_template": check.get("message_template") or "",
                "threshold_pct": check.get("threshold_pct"),
                "sql_file": str(check.get("sql_file") or ""),
                "sample_rows": sample,
            }
        )
    except Exception as exc:
        out.update({"ok": False, "passed": False, "error": str(exc)})
    return out


def run_auto_fix(check: dict[str, Any]) -> tuple[bool, str]:
    """Run configured fix SQL file (write). Returns (ok, detail). mode: sql only."""
    cfg = auto_fix_config(check)
    if cfg.get("mode") != "sql":
        return False, f"run_auto_fix is for mode=sql, got mode={cfg.get('mode')}"
    fix_file = cfg.get("fix_sql_file")
    if not fix_file:
        return False, "no fix_sql_file configured"
    path = repos_root() / str(fix_file)
    if not path.is_file():
        return False, f"fix SQL file not found: {path}"
    text = path.read_text(encoding="utf-8", errors="replace")
    statements = _split_sql_statements(text)
    if not statements:
        return False, f"no SQL statements in {path}"
    try:
        for stmt in statements:
            execute_sql(stmt, allow_write=True)
    except Exception as exc:
        return False, str(exc)
    return True, f"applied {len(statements)} statement(s) from {fix_file}"


def run_all_checks() -> list[dict[str, Any]]:
    cfg = load_watchdog_config()
    checks = cfg.get("checks") or []
    return [run_check(c) for c in checks if isinstance(c, dict)]
