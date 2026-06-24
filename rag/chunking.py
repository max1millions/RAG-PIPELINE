"""Language-aware chunking with README/doc prioritization metadata."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from rag.settings import MAX_DOC_DEPTH, DOC_FILENAMES, chunk_settings, repos_root


@dataclass
class Chunk:
    text: str
    index: int
    extra: dict = field(default_factory=dict)


def is_readme(path: Path) -> bool:
    return path.name.upper() == "README.MD"


def is_repo_doc(path: Path, root: Path | None = None) -> bool:
    root = root or repos_root()
    if path.name not in DOC_FILENAMES and not is_readme(path):
        return False
    try:
        rel = path.relative_to(root)
    except ValueError:
        return False
    if rel.parts and rel.parts[0] == "SQL-SCRIPTS":
        return False
    return len(rel.parts) <= MAX_DOC_DEPTH


def doc_context_prefix(repo_name: str, path: Path) -> str:
    label = "README" if is_readme(path) else path.name
    return f"[Repo: {repo_name} | {label}]\n"


def _char_chunks(text: str, chunk_size: int, overlap: int) -> list[str]:
    if len(text) <= chunk_size:
        return [text]
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + chunk_size)
        chunks.append(text[start:end])
        if end >= len(text):
            break
        start = max(0, end - overlap)
    return chunks


def _split_by_pattern(text: str, pattern: str) -> list[str]:
    parts = re.split(pattern, text, flags=re.MULTILINE)
    blocks: list[str] = []
    buf: list[str] = []
    for part in parts:
        if re.match(pattern, part, flags=re.MULTILINE):
            if buf:
                blocks.append("".join(buf).strip())
                buf = []
            buf.append(part)
        else:
            buf.append(part)
    if buf:
        blocks.append("".join(buf).strip())
    return [b for b in blocks if b.strip()]


def chunk_file(path: Path, text: str, *, repos_root_path: Path | None = None) -> list[Chunk]:
    root = repos_root_path or repos_root()
    chunk_size, overlap = chunk_settings()
    suffix = path.suffix.lower()
    repo_name = "unknown"
    try:
        rel = path.relative_to(root)
        if rel.parts:
            repo_name = rel.parts[0]
    except ValueError:
        rel = path

    extra_base: dict = {"lang": suffix.lstrip(".") or "txt"}
    if is_repo_doc(path, root):
        extra_base["kind"] = "readme" if is_readme(path) else "doc"
        extra_base["doc_tier"] = "primary"
    else:
        extra_base["kind"] = "code"

    prefix = ""
    if extra_base.get("kind") in ("readme", "doc"):
        prefix = doc_context_prefix(repo_name, path)

    chunks: list[Chunk] = []

    if suffix == ".md":
        sections = _split_by_pattern(text, r"(?=^#{1,3} )")
        if len(sections) > 1:
            for i, section in enumerate(sections):
                hint = ""
                m = re.search(r"^#{1,3}\s+(.+)$", section, re.MULTILINE)
                if m:
                    hint = m.group(1).strip()[:80]
                for piece in _char_chunks(section, chunk_size, overlap):
                    body = f"{prefix}{piece}" if prefix else piece
                    chunks.append(Chunk(body, len(chunks), {**extra_base, "symbol_hint": hint or f"section_{i}"}))
            return chunks

    if suffix in (".py", ".sh"):
        blocks = _split_by_pattern(text, r"(?=^(?:def |class |function ))")
        if len(blocks) > 1:
            for block in blocks:
                m = re.search(r"^(?:def|class)\s+(\w+)", block, re.MULTILINE)
                for piece in _char_chunks(block, chunk_size, overlap):
                    chunks.append(
                        Chunk(
                            piece,
                            len(chunks),
                            {**extra_base, "symbol_hint": m.group(1) if m else ""},
                        )
                    )
            return chunks

    if suffix == ".sql":
        statements = [s.strip() for s in re.split(r";\s*\n", text) if s.strip()]
        if len(statements) > 1:
            buf = ""
            for stmt in statements:
                if len(buf) + len(stmt) > chunk_size and buf:
                    chunks.append(Chunk(buf, len(chunks), extra_base))
                    buf = stmt
                else:
                    buf = f"{buf}\n{stmt}".strip() if buf else stmt
            if buf:
                chunks.append(Chunk(buf, len(chunks), extra_base))
            return chunks

    for piece in _char_chunks(text, chunk_size, overlap):
        body = f"{prefix}{piece}" if prefix and len(chunks) == 0 else piece
        chunks.append(Chunk(body, len(chunks), extra_base))
    return chunks
