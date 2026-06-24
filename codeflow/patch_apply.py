"""Apply search/replace edits from the Coder agent to repo files."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ApplyResult:
    changed_files: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def validate_repo_path(rel: str, repo_path: Path) -> Path:
    if not rel or not rel.strip():
        raise ValueError("empty path")
    if ".." in Path(rel).parts:
        raise ValueError(f"path escape blocked: {rel}")
    target = (repo_path / rel).resolve()
    if not str(target).startswith(str(repo_path.resolve())):
        raise ValueError(f"path escape blocked: {rel}")
    return target


def apply_replace(target: Path, old_str: str, new_str: str) -> None:
    if not target.is_file():
        raise FileNotFoundError(f"file not found: {target.name}")

    content = target.read_text(encoding="utf-8")
    count = content.count(old_str)
    if count == 0:
        hint = content[:200].replace("\n", "\\n")
        raise ValueError(
            f"old_str not found in {target.name} (file starts with: {hint!r})"
        )
    if count > 1:
        raise ValueError(
            f"old_str matched {count} times in {target.name} — include more context"
        )

    target.write_text(content.replace(old_str, new_str, 1), encoding="utf-8")


def apply_create(target: Path, content: str, *, overwrite: bool = False) -> None:
    if target.exists() and not overwrite:
        raise ValueError(f"file already exists: {target.name} (use type replace to edit)")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def apply_delete(target: Path) -> None:
    if not target.is_file():
        raise FileNotFoundError(f"file not found: {target.name}")
    target.unlink()


def _group_edits_by_path(edits: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for edit in edits:
        rel = str(edit.get("path") or "").strip()
        grouped.setdefault(rel, []).append(edit)
    return grouped


def _apply_file_edits(target: Path, rel: str, file_edits: list[dict[str, Any]]) -> None:
    """Apply all edits for one file in memory, then write once."""
    pending_content: str | None = None
    pending_delete = False

    for edit in file_edits:
        edit_type = str(edit.get("type") or "replace").lower()

        if edit_type == "delete":
            if pending_content is not None:
                raise ValueError(f"cannot delete {rel} after in-memory edits")
            pending_delete = True
            continue

        if pending_delete:
            raise ValueError(f"cannot edit {rel} after delete")

        if edit_type == "create":
            content = edit.get("content", "")
            if not isinstance(content, str):
                raise ValueError(f"create edit for {rel} requires string content")
            if target.exists() and pending_content is None:
                raise ValueError(f"file already exists: {rel} (use type replace to edit)")
            pending_content = content
            continue

        if edit_type == "replace":
            old_str = edit.get("old_str", "")
            new_str = edit.get("new_str", "")
            if not isinstance(old_str, str) or not isinstance(new_str, str):
                raise ValueError(f"replace edit for {rel} requires old_str and new_str strings")

            if pending_content is None:
                if not target.is_file():
                    raise FileNotFoundError(f"file not found: {rel}")
                pending_content = target.read_text(encoding="utf-8")

            count = pending_content.count(old_str)
            if count == 0:
                hint = pending_content[:200].replace("\n", "\\n")
                raise ValueError(
                    f"old_str not found in {rel} (content starts with: {hint!r})"
                )
            if count > 1:
                raise ValueError(
                    f"old_str matched {count} times in {rel} — include more context"
                )
            pending_content = pending_content.replace(old_str, new_str, 1)
            continue

        raise ValueError(f"unknown edit type {edit_type!r} for {rel}")

    if pending_delete:
        if target.is_file():
            target.unlink()
        return

    if pending_content is not None:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(pending_content, encoding="utf-8")


def apply_edits(edits: list[dict[str, Any]], repo_path: Path) -> ApplyResult:
    result = ApplyResult()
    if not edits:
        result.errors.append("no edits provided")
        return result

    grouped = _group_edits_by_path(edits)

    # Validate all paths before applying any changes.
    targets: dict[str, Path] = {}
    for rel in grouped:
        try:
            targets[rel] = validate_repo_path(rel, repo_path)
        except ValueError as exc:
            result.errors.append(str(exc))
            return result

    changed: list[str] = []
    for rel, file_edits in grouped.items():
        target = targets[rel]
        try:
            _apply_file_edits(target, rel, file_edits)
            changed.append(rel)
        except (ValueError, FileNotFoundError, OSError) as exc:
            result.errors.append(str(exc))
            return result

    result.changed_files = changed
    return result
