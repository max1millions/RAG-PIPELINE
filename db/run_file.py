"""Run a (possibly multi-statement) .sql file via the `mysql` CLI.

Mirrors the shell-out pattern already used by db/import_dump.sh and
REPOS/CIS-NET-AUTOMATION/phase3.py::execute_sql_file: pipe the file straight
into `mysql` as stdin instead of trying to split/parse statements in Python.
This is what makes SET @vars, multiple SELECTs, USE, and transactions in
SQL-SCRIPTS files work correctly. Shared by orion-db (--file) and
orion-sql-test.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from common.config import mysql_config


def mysql_binary() -> str:
    return shutil.which("mysql") or "mysql"


def build_mysql_cmd(
    *,
    database: str | None = None,
    extra_args: list[str] | None = None,
) -> list[str]:
    """Build the mysql CLI invocation from mysql_config() / overlay .env."""
    cfg = mysql_config()
    cmd = [
        mysql_binary(),
        "-h", str(cfg["host"]),
        "-P", str(cfg["port"]),
        "-u", cfg["user"],
        f"-p{cfg['password']}",
    ]
    cmd.extend(extra_args or [])
    cmd.append(database or cfg["database"])
    return cmd


def run_sql_file(
    path: str | Path,
    *,
    database: str | None = None,
    extra_args: list[str] | None = None,
    capture: bool = False,
) -> subprocess.CompletedProcess:
    """Run `path`'s SQL through the mysql CLI, streaming it in as stdin.

    No statement splitting: the whole file is handed to `mysql` exactly as
    `mysql ... db < path` would. When capture=False (default) mysql's
    stdout/stderr are inherited so output streams live; when capture=True
    they're captured on the returned CompletedProcess (used by
    orion-sql-test for assertions).
    """
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(p)
    cmd = build_mysql_cmd(database=database, extra_args=extra_args)
    with p.open("r", encoding="utf-8") as sql_fh:
        return subprocess.run(
            cmd,
            stdin=sql_fh,
            capture_output=capture,
            text=True,
        )
