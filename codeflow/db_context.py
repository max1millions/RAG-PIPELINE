"""Gather read-only local MySQL schema context for code-fix prompts."""

from __future__ import annotations

import re
from typing import Any

from common.config import feature_enabled

_DB_REPOS = frozenset({"DATABASE-INSERT", "DATABASE-EXPORT", "SQL-SCRIPTS", "SCHEMA", "ISWC-SERVICE"})
_TABLE_PATTERN = re.compile(r"\b([a-z_][a-z0-9_]{2,})\b", re.IGNORECASE)
_SKIP_WORDS = frozenset(
    {
        "select",
        "from",
        "where",
        "update",
        "insert",
        "delete",
        "table",
        "schema",
        "database",
        "mysql",
        "the",
        "and",
        "for",
        "with",
        "into",
        "join",
        "left",
        "right",
        "inner",
        "outer",
        "group",
        "order",
        "limit",
        "fix",
        "error",
        "file",
        "line",
        "python",
        "script",
    }
)


def _needs_db_context(repo: str, request: str, rag_context: str) -> bool:
    if repo in _DB_REPOS:
        return True
    blob = f"{request}\n{rag_context}".lower()
    keywords = ("sql", "schema", "mysql", "table", "column", "database", "writer", "work", "ipi")
    return any(k in blob for k in keywords)


def _extract_table_names(*texts: str, max_tables: int = 5) -> list[str]:
    seen: list[str] = []
    for text in texts:
        for match in _TABLE_PATTERN.finditer(text):
            name = match.group(1).lower()
            if name in _SKIP_WORDS or name.isdigit():
                continue
            if name not in seen:
                seen.append(name)
            if len(seen) >= max_tables:
                return seen
    return seen


def gather(
    repo: str,
    request: str,
    *,
    rag_context: str = "",
    force: bool = False,
) -> str:
    if not feature_enabled("local_mysql"):
        return ""
    if not force and not _needs_db_context(repo, request, rag_context):
        return ""

    try:
        from db.connect import execute_sql
    except Exception as exc:
        return f"(local MySQL unavailable: {exc})"

    parts: list[str] = []
    try:
        tables_result = execute_sql("SHOW TABLES", allow_write=False)
        if tables_result.get("type") == "rows":
            rows = tables_result.get("rows") or []
            table_names = []
            for row in rows:
                if row:
                    table_names.append(str(next(iter(row.values()))))
            parts.append(f"Tables ({len(table_names)}): " + ", ".join(table_names[:40]))
            if len(table_names) > 40:
                parts.append(f"... and {len(table_names) - 40} more")
    except Exception as exc:
        return f"(SHOW TABLES failed: {exc})"

    candidates = _extract_table_names(request, rag_context)
    described = 0
    for name in candidates:
        if described >= 5:
            break
        safe = re.sub(r"[^a-zA-Z0-9_]", "", name)
        if not safe:
            continue
        try:
            desc = execute_sql(f"DESCRIBE `{safe}`", allow_write=False)
            if desc.get("type") != "rows":
                continue
            cols = desc.get("rows") or []
            col_summary = ", ".join(
                f"{r.get('Field')}:{r.get('Type')}" for r in cols[:12] if r.get("Field")
            )
            parts.append(f"DESCRIBE {safe}: {col_summary}")
            described += 1
        except Exception:
            continue

    text = "\n".join(parts)
    if len(text) > 2000:
        return text[:1980] + "\n...(truncated)"
    return text
