#!/usr/bin/env python3
"""CLI: thin SQL test harness for repo_tests.yaml — shells out to mysql.

Not a SQL parser or statement splitter. Two modes:
  --glob PATTERN   Smoke-test every matching .sql file: pass/fail is just
                    the mysql exit code (syntax + live-schema check).
  --file PATH      Run one file; optionally --expect-empty to assert no
                    rows are returned (validation scripts).

Paths are resolved against the workspace root (config paths.workspace), not
cwd, so glob patterns work whether this is invoked from a repo root (as
codeflow/test_runner.py does) or elsewhere.
"""

from __future__ import annotations

import argparse
import fnmatch
import sys
from pathlib import Path

STACK_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(STACK_ROOT))

from common.config import load_config, require_feature  # noqa: E402
from db.run_file import run_sql_file  # noqa: E402


def _workspace_root() -> Path:
    cfg = load_config()
    return Path(cfg["paths"]["workspace"])


def _resolve_glob(pattern: str, root: Path) -> list[Path]:
    return sorted(p for p in root.glob(pattern) if p.is_file())


def _resolve_file(file_arg: str, root: Path) -> Path:
    p = Path(file_arg)
    return p if p.is_absolute() else root / file_arg


def _is_excluded(path: Path, root: Path, excludes: list[str]) -> bool:
    try:
        rel = str(path.relative_to(root))
    except ValueError:
        rel = str(path)
    return any(fnmatch.fnmatch(rel, f"*{pat}") or pat in rel for pat in excludes)


def _run_one(path: Path, *, expect_empty: bool) -> tuple[bool, str]:
    extra_args = ["--batch", "--skip-column-names"] if expect_empty else []
    try:
        proc = run_sql_file(path, extra_args=extra_args, capture=True)
    except FileNotFoundError:
        return False, f"file not found: {path}"

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        return False, f"mysql exited {proc.returncode}: {detail}"

    if expect_empty and proc.stdout.strip():
        preview = proc.stdout.strip()[:2000]
        return False, f"expected empty result, got:\n{preview}"

    return True, ""


def main() -> int:
    require_feature("local_mysql", "Local MySQL")

    parser = argparse.ArgumentParser(
        description="Shell-out SQL test harness (no statement parsing)"
    )
    parser.add_argument("--glob", help="Glob pattern relative to workspace root, e.g. '**/*.sql'")
    parser.add_argument("--file", help="Single .sql file, relative to workspace root or absolute")
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        help="Path substring/fnmatch to skip (repeatable); relative to workspace root",
    )
    parser.add_argument(
        "--expect-empty",
        action="store_true",
        help="Assert the file returns no rows (validation scripts)",
    )
    args = parser.parse_args()

    if not args.glob and not args.file:
        print("ERROR: pass --glob or --file", file=sys.stderr)
        return 2

    root = _workspace_root()

    if args.file:
        files = [_resolve_file(args.file, root)]
    else:
        files = [f for f in _resolve_glob(args.glob, root) if not _is_excluded(f, root, args.exclude)]

    if not files:
        print("ERROR: no files matched", file=sys.stderr)
        return 1

    failures: list[Path] = []
    for f in files:
        ok, reason = _run_one(f, expect_empty=args.expect_empty)
        try:
            label = f.relative_to(root)
        except ValueError:
            label = f
        if ok:
            print(f"OK   {label}")
        else:
            failures.append(f)
            print(f"FAIL {label}: {reason}")

    print(f"\n{len(files) - len(failures)}/{len(files)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
