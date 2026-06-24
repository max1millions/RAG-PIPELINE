"""MySQL connection factory for local dev database."""

from __future__ import annotations

from typing import Any

import mysql.connector
from mysql.connector import MySQLConnection

from common.config import mysql_config, require_feature


def connect(*, read_only: bool = False) -> MySQLConnection:
    require_feature("local_mysql", "Local MySQL")
    cfg = mysql_config()
    conn = mysql.connector.connect(
        host=cfg["host"],
        port=cfg["port"],
        user=cfg["user"],
        password=cfg["password"],
        database=cfg["database"],
        autocommit=read_only,
    )
    return conn


def execute_sql_with_preamble(
    sql: str,
    *,
    preamble: str | None = None,
    allow_write: bool = False,
) -> dict[str, Any]:
    """Execute optional preamble statement(s) then main SQL on one connection."""
    conn = connect(read_only=not allow_write)
    try:
        cursor = conn.cursor(dictionary=True)
        if preamble and preamble.strip():
            cursor.execute(preamble.strip().rstrip(";"))
        cursor.execute(sql)
        if cursor.with_rows:
            rows = cursor.fetchall()
            return {"type": "rows", "rows": rows, "rowcount": len(rows)}
        conn.commit()
        return {"type": "ok", "rowcount": cursor.rowcount}
    finally:
        conn.close()


def execute_sql(sql: str, *, allow_write: bool = False) -> dict[str, Any]:
    """Execute SQL and return rows or affected row count."""
    stripped = sql.strip().lstrip("(").strip()
    upper = stripped.upper()
    write_prefixes = (
        "INSERT",
        "UPDATE",
        "DELETE",
        "REPLACE",
        "CREATE",
        "DROP",
        "ALTER",
        "TRUNCATE",
        "GRANT",
        "REVOKE",
    )
    is_write = any(upper.startswith(p) for p in write_prefixes)

    if is_write and not allow_write:
        raise PermissionError(
            "Write SQL blocked. Re-run with --write to allow INSERT/UPDATE/DELETE/DDL."
        )

    conn = connect(read_only=not is_write)
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(sql)
        if cursor.with_rows:
            rows = cursor.fetchall()
            return {"type": "rows", "rows": rows, "rowcount": len(rows)}
        conn.commit()
        return {"type": "ok", "rowcount": cursor.rowcount}
    finally:
        conn.close()
