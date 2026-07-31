#!/usr/bin/env python3
"""Deterministic bulk copy transforms for REPOS (sitewide string append/replace).

Prefer this over orion-fix LLM search/replace for mechanical copy edits
(e.g. append Instagram next to every user-facing email mention).
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

STACK_ROOT = Path(__file__).resolve().parent.parent
WORKSPACE = Path.home() / ".openclaw" / "workspace"
REPOS = WORKSPACE / "REPOS"

DEFAULT_GLOBS = ("*.html", "*.htm", "*.php", "*.json", "*.md", "*.txt", "*.js", "*.css")
DEFAULT_EXCLUDES = (
    ".env",
    ".git/",
    "node_modules/",
    "vendor/",
    "libraries/",
    "__pycache__/",
)


@dataclass
class MatchChange:
    path: str
    line: int
    action: str
    before: str
    after: str


def _repo_path(repo: str) -> Path:
    root = (REPOS / repo).resolve()
    if not root.is_dir():
        raise SystemExit(f"repo not found: {root}")
    return root


def _excluded(rel: str, excludes: list[str]) -> bool:
    norm = rel.replace("\\", "/")
    name = Path(norm).name
    for ex in excludes:
        ex = ex.strip().replace("\\", "/")
        if not ex:
            continue
        if ex.endswith("/") and f"/{ex}" in f"/{norm}/":
            return True
        if ex in norm or name == ex or norm.endswith(ex):
            return True
    return False


def _iter_files(repo_root: Path, globs: list[str], excludes: list[str]) -> list[Path]:
    files: list[Path] = []
    seen: set[Path] = set()
    for pattern in globs:
        for path in repo_root.rglob(pattern):
            if not path.is_file():
                continue
            rel = path.relative_to(repo_root).as_posix()
            if _excluded(rel, excludes):
                continue
            if path in seen:
                continue
            seen.add(path)
            files.append(path)
    return sorted(files)


def _line_no(text: str, index: int) -> int:
    return text.count("\n", 0, index) + 1


def _apply_append(
    content: str,
    *,
    needle: str,
    append: str,
    skip_if_contains: str,
) -> tuple[str, list[tuple[int, str, str]]]:
    """Append after each needle occurrence unless nearby already has skip marker."""
    changes: list[tuple[int, str, str]] = []
    out: list[str] = []
    pos = 0
    while True:
        idx = content.find(needle, pos)
        if idx < 0:
            out.append(content[pos:])
            break
        out.append(content[pos:idx])
        # Look ahead a short window for the skip marker (already updated).
        window = content[idx : idx + len(needle) + max(len(append) * 2, 160)]
        if skip_if_contains and skip_if_contains in window:
            out.append(needle)
            pos = idx + len(needle)
            continue
        before = needle
        after = needle + append
        out.append(after)
        changes.append((_line_no(content, idx), before, after))
        pos = idx + len(needle)
    return "".join(out), changes


def _apply_replace(
    content: str,
    *,
    old: str,
    new: str,
) -> tuple[str, list[tuple[int, str, str]]]:
    if old not in content:
        return content, []
    changes: list[tuple[int, str, str]] = []
    start = 0
    while True:
        idx = content.find(old, start)
        if idx < 0:
            break
        changes.append((_line_no(content, idx), old, new))
        start = idx + len(old)
    return content.replace(old, new), changes


def run(args: argparse.Namespace) -> int:
    repo_root = _repo_path(args.repo)
    globs = [g.strip() for g in args.glob.split(",") if g.strip()] or list(DEFAULT_GLOBS)
    excludes = list(DEFAULT_EXCLUDES)
    if args.exclude:
        excludes.extend(x.strip() for x in args.exclude.split(",") if x.strip())

    only_paths: set[str] | None = None
    if args.paths:
        only_paths = {p.strip().lstrip("./") for p in args.paths.split(",") if p.strip()}

    files = _iter_files(repo_root, globs, excludes)
    if only_paths is not None:
        files = [p for p in files if p.relative_to(repo_root).as_posix() in only_paths]
        missing = sorted(only_paths - {p.relative_to(repo_root).as_posix() for p in files})
        if missing:
            print(f"warning: paths not found or excluded: {', '.join(missing)}", file=sys.stderr)

    all_changes: list[MatchChange] = []
    modified_files: list[str] = []

    for path in files:
        rel = path.relative_to(repo_root).as_posix()
        try:
            original = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue

        if args.mode == "append":
            if args.needle not in original:
                continue
            updated, changes = _apply_append(
                original,
                needle=args.needle,
                append=args.append,
                skip_if_contains=args.skip_if_contains or "",
            )
            action = "append"
        else:
            if args.old not in original:
                continue
            updated, changes = _apply_replace(original, old=args.old, new=args.new)
            action = "replace"

        if not changes or updated == original:
            continue

        for line, before, after in changes:
            all_changes.append(
                MatchChange(
                    path=rel,
                    line=line,
                    action=action,
                    before=before if len(before) < 200 else before[:197] + "...",
                    after=after if len(after) < 240 else after[:237] + "...",
                )
            )
        modified_files.append(rel)
        if args.apply:
            path.write_text(updated, encoding="utf-8")

    result = {
        "repo": args.repo,
        "mode": args.mode,
        "dry_run": not args.apply,
        "files_touched": modified_files,
        "change_count": len(all_changes),
        "changes": [asdict(c) for c in all_changes],
    }

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        verb = "Would update" if not args.apply else "Updated"
        print(f"{verb} {len(modified_files)} file(s), {len(all_changes)} change(s) in {args.repo}")
        for rel in modified_files:
            print(f"  - {rel}")
        if not args.apply:
            print("(dry-run; pass --apply to write)")

    return 0 if all_changes or args.allow_empty else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Deterministic bulk copy append/replace for REPOS (prefer over orion-fix for mechanical copy)."
    )
    p.add_argument("--repo", required=True, help="Repo name under ~/.openclaw/workspace/REPOS/")
    p.add_argument(
        "--mode",
        choices=("append", "replace"),
        default="append",
        help="append after needle, or exact replace",
    )
    p.add_argument("--needle", help="Substring to find (append mode)")
    p.add_argument("--append", help="Text to insert immediately after each needle (append mode)")
    p.add_argument(
        "--skip-if-contains",
        default="Instagram",
        help="Skip a needle hit when this marker already appears in a short window after it",
    )
    p.add_argument("--old", help="Exact old string (replace mode)")
    p.add_argument("--new", help="Exact new string (replace mode)")
    p.add_argument(
        "--glob",
        default=",".join(DEFAULT_GLOBS),
        help="Comma-separated rglob patterns",
    )
    p.add_argument(
        "--exclude",
        default="",
        help="Comma-separated path/name substrings to skip (added to defaults)",
    )
    p.add_argument(
        "--paths",
        default="",
        help="Optional comma-separated relative paths to limit scope",
    )
    p.add_argument("--apply", action="store_true", help="Write changes (default is dry-run)")
    p.add_argument("--json", action="store_true", help="Machine-readable output")
    p.add_argument(
        "--allow-empty",
        action="store_true",
        help="Exit 0 even when no changes are found",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.mode == "append":
        if not args.needle or args.append is None:
            raise SystemExit("append mode requires --needle and --append")
    else:
        if args.old is None or args.new is None:
            raise SystemExit("replace mode requires --old and --new")
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
