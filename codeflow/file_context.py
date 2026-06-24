"""Gather target file contents for the Coder agent prompt."""

from __future__ import annotations

import re
from pathlib import Path

_PATH_EXT_RE = re.compile(
    r"(?:^|[\s`'\"(])([\w./-]+\.(?:py|sh|php|sql|yaml|yml|json|md|txt|js|ts))(?:[\s`'\",):]|$)",
    re.IGNORECASE,
)
_RAG_HEADER_RE = re.compile(
    r"^###\s+\[[^\]]+\]\s+(?:[\w-]+/)?(.+?)\s+\(chunk",
    re.MULTILINE,
)
_LINE_HINT_RE = re.compile(r"line\s+(\d+)", re.IGNORECASE)


def _normalize_rel(path: str, repo: str | None = None) -> str | None:
    p = path.strip().strip("`\"'")
    if not p or p.startswith("http"):
        return None
    for prefix in ("REPOS/", "repos/"):
        if p.startswith(prefix):
            p = p.split("/", 1)[1]
            break
    if repo and p.startswith(f"{repo}/"):
        p = p[len(repo) + 1 :]
    if p.startswith("/") or ".." in Path(p).parts:
        return None
    return p


def discover_paths(
    *,
    request: str,
    plan: str,
    rag_context: str,
    repo: str = "",
    changed_files: list[str] | None = None,
) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []

    def add(path: str | None) -> None:
        if not path or path in seen:
            return
        seen.add(path)
        ordered.append(path)

    for rel in changed_files or []:
        add(_normalize_rel(rel, repo))

    for text in (plan, request):
        for match in _PATH_EXT_RE.finditer(text):
            add(_normalize_rel(match.group(1), repo))

    for match in _RAG_HEADER_RE.finditer(rag_context or ""):
        add(_normalize_rel(match.group(1), repo))

    return ordered


def _line_hint_for_path(path: str, request: str, rag_context: str) -> int | None:
    for text in (request, rag_context):
        if path not in text:
            continue
        match = _LINE_HINT_RE.search(text)
        if match:
            return int(match.group(1))
    return None


def _format_with_line_numbers(content: str) -> str:
    lines = content.splitlines()
    width = max(4, len(str(len(lines))))
    return "\n".join(f"{i:0{width}d}| {line}" for i, line in enumerate(lines, 1))


def _truncate_content(
    content: str,
    *,
    max_chars: int,
    center_line: int | None = None,
) -> str:
    if len(content) <= max_chars:
        return _format_with_line_numbers(content)

    lines = content.splitlines()
    if not lines:
        return ""

    if center_line is not None and 1 <= center_line <= len(lines):
        half_window = max(20, max_chars // 80)
        start = max(1, center_line - half_window)
        end = min(len(lines), center_line + half_window)
        window = lines[start - 1 : end]
        header = f"... [lines 1-{start - 1} truncated] ...\n" if start > 1 else ""
        footer = (
            f"\n... [lines {end + 1}-{len(lines)} truncated] ..."
            if end < len(lines)
            else ""
        )
        body = _format_with_line_numbers("\n".join(window))
        # Re-number from actual line offset.
        numbered = []
        width = max(4, len(str(len(lines))))
        for i, line in enumerate(window, start):
            numbered.append(f"{i:0{width}d}| {line}")
        return header + "\n".join(numbered) + footer

    # Head + tail fallback.
    head_lines = max_chars // 2 // 80
    tail_lines = max_chars // 2 // 80
    head = lines[: max(head_lines, 1)]
    tail = lines[-max(tail_lines, 1) :] if len(lines) > head_lines else []
    omitted = len(lines) - len(head) - len(tail)
    parts: list[str] = []
    width = max(4, len(str(len(lines))))
    for i, line in enumerate(head, 1):
        parts.append(f"{i:0{width}d}| {line}")
    if omitted > 0:
        parts.append(f"... [{omitted} lines truncated] ...")
        tail_start = len(lines) - len(tail) + 1
        for i, line in enumerate(tail, tail_start):
            parts.append(f"{i:0{width}d}| {line}")
    return "\n".join(parts)


def gather_file_context(
    *,
    repo_path: Path,
    request: str,
    plan: str,
    rag_context: str,
    repo: str = "",
    changed_files: list[str] | None = None,
    max_file_chars: int = 8000,
    max_total_chars: int = 40000,
) -> str:
    paths = discover_paths(
        request=request,
        plan=plan,
        rag_context=rag_context,
        repo=repo,
        changed_files=changed_files,
    )
    if not paths:
        return "(no target files identified — use paths from the plan or request)"

    blocks: list[str] = []
    total = 0

    for rel in paths:
        target = repo_path / rel
        if not target.is_file():
            block = f"### {rel}\n(file not found in repo)"
        else:
            try:
                raw = target.read_text(encoding="utf-8")
            except OSError as exc:
                block = f"### {rel}\n(read error: {exc})"
            else:
                center = _line_hint_for_path(rel, request, rag_context)
                body = _truncate_content(
                    raw, max_chars=max_file_chars, center_line=center
                )
                block = f"### {rel}\n```\n{body}\n```"

        if total + len(block) > max_total_chars:
            blocks.append(
                f"... [{len(paths) - len(blocks)} more files omitted — context limit reached]"
            )
            break
        blocks.append(block)
        total += len(block)

    return "\n\n".join(blocks)
