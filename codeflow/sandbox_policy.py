"""Rules for the Mac cursor-worker handoff. Standard library only.

The ForceCommand script and the Linux poll both import this module. It does
not read secret files and it does not start a process.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

REPO_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$")
COMMAND_RE = re.compile(r"^orion-mac-handoff ([A-Za-z0-9][A-Za-z0-9._-]{0,80})$")
FINGERPRINT_RE = re.compile(r"^[0-9a-fA-F]{8,64}$")
BRANCH_RE = re.compile(r"^cursor/incident-[0-9a-f]{8,12}$")
GIT_URL_RE = re.compile(
    r"^(git@github\.com:|https://github\.com/)[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(\.git)?$"
)

MAX_BRIEF_CHARS = 12000
MAX_INSERTIONS = 500
MAX_DELETIONS = 500

DEFAULT_ALLOW_REPOS = (
    "CIS-NET-AUTOMATION",
    "DATABASE-INSERT",
    "CWR-INTERFACE",
)

_FORBIDDEN_PATHS = (
    re.compile(r"(^|/)\.env($|\.)"),
    re.compile(r"\.pem$", re.IGNORECASE),
    re.compile(r"\.key$", re.IGNORECASE),
    re.compile(r"(^|/)credentials\.json$"),
    re.compile(r"(^|/)\.github/"),
    re.compile(r"(^|/)id_(ed25519|rsa)"),
)

_SECRET_TEXT = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bcrsr_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bgho_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    re.compile(
        r"(?i)\b(password|passwd|secret|api_key|apikey)\b\s*[:=]\s*['\"][^'\"]{8,}['\"]"
    ),
)

_REDACT = (
    re.compile(r"crsr_[A-Za-z0-9]+"),
    re.compile(r"ghp_[A-Za-z0-9]+"),
    re.compile(r"gho_[A-Za-z0-9]+"),
    re.compile(r"sk-[A-Za-z0-9]+"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
)

_DENIED_HOME: Path | None = None


class PolicyError(ValueError):
    """The handoff request or the resulting diff is not allowed."""


def redact(text: str) -> str:
    """Replace secret-shaped substrings. Safe to put in logs."""
    out = text or ""
    for pattern in _REDACT:
        out = pattern.sub("[redacted]", out)
    return out


def validate_repo_name(name: str) -> str:
    repo = (name or "").strip()
    if not REPO_RE.fullmatch(repo):
        raise PolicyError("rejected repo name")
    return repo


def validate_original_command(command: str, allow: set[str] | frozenset[str]) -> str:
    """Return the repo from ``SSH_ORIGINAL_COMMAND`` or raise ``PolicyError``.

    The command must be exactly ``orion-mac-handoff <repo>`` with no shell
    syntax. The repo must be on the allowlist.
    """
    match = COMMAND_RE.fullmatch((command or "").strip())
    if not match:
        raise PolicyError("rejected command")
    repo = match.group(1)
    if repo not in allow:
        raise PolicyError("repo not allowlisted")
    return repo


def branch_for(fingerprint: str) -> str:
    if not FINGERPRINT_RE.fullmatch(fingerprint or ""):
        raise PolicyError("rejected fingerprint")
    return f"cursor/incident-{fingerprint[:12].lower()}"


def assert_push_branch(branch: str) -> None:
    """Allow a handoff branch only. ``main`` is never a push target."""
    if not BRANCH_RE.fullmatch(branch or ""):
        raise PolicyError("refusing branch")
    if branch == "main" or branch.startswith("main/"):
        raise PolicyError("refusing to push main")


def validate_git_url(url: str) -> str:
    if not GIT_URL_RE.fullmatch((url or "").strip()):
        raise PolicyError("rejected git url")
    return url.strip()


def configure_denied_home(home: Path) -> Path:
    """Remember the operator home. Later path checks refuse anything inside it.

    ``home`` comes from ``denied_home`` in the handoff config. The public
    template uses ``/Users/YOU``; setup replaces that in the installed copy.
    """
    global _DENIED_HOME
    text = str(home).strip()
    if not text:
        raise PolicyError("denied_home is not set")
    resolved = Path(text).expanduser().resolve()
    if resolved.parent == resolved:
        raise PolicyError("rejected operator home")
    _DENIED_HOME = resolved
    return resolved


def assert_outside_origin(path: Path) -> Path:
    """Resolved path must not sit inside the configured operator home."""
    if _DENIED_HOME is None:
        raise PolicyError("operator home is not configured")
    resolved = Path(path).expanduser().resolve()
    if resolved == _DENIED_HOME or _DENIED_HOME in resolved.parents:
        raise PolicyError("refusing path under the operator home")
    return resolved


def scan_text(text: str) -> list[str]:
    """Return reasons a blob must not be sent to the agent or committed."""
    reasons: list[str] = []
    blob = text or ""
    for pattern in _SECRET_TEXT:
        if pattern.search(blob):
            reasons.append("secret-shaped text")
            break
    return reasons


def check_paths(files: list[str]) -> list[str]:
    reasons: list[str] = []
    for rel in files:
        for pattern in _FORBIDDEN_PATHS:
            if pattern.search(rel):
                reasons.append(f"forbidden path {rel}")
                break
    return reasons


def diff_totals(numstat: str) -> tuple[int, int]:
    insertions = 0
    deletions = 0
    for line in (numstat or "").splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        if parts[0] != "-":
            insertions += int(parts[0])
        if parts[1] != "-":
            deletions += int(parts[1])
    return insertions, deletions


def check_diff_size(insertions: int, deletions: int) -> list[str]:
    reasons: list[str] = []
    if insertions > MAX_INSERTIONS:
        reasons.append(f"insertions {insertions} > max {MAX_INSERTIONS}")
    if deletions > MAX_DELETIONS:
        reasons.append(f"deletions {deletions} > max {MAX_DELETIONS}")
    return reasons


def _scalar(value: str) -> Any:
    if value in ('""', "''"):
        return ""
    if value == "true":
        return True
    if value == "false":
        return False
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def parse_handoff_config(text: str) -> dict[str, Any]:
    """Parse the handoff YAML subset. No PyYAML, so the worker user can load it.

    Supports comments, ``key: value``, nested maps, and lists of scalars.
    ``key: ""`` is an empty string. ``key:`` with indented ``-`` items is a list.
    """
    lines: list[tuple[int, str]] = []
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        lines.append((len(raw) - len(raw.lstrip(" ")), stripped))

    def parse_block(start: int, indent: int) -> tuple[dict[str, Any], int]:
        node: dict[str, Any] = {}
        index = start
        while index < len(lines):
            ind, line = lines[index]
            if ind < indent:
                break
            if ind > indent or line.startswith("- "):
                raise PolicyError("bad config indent")
            key, sep, raw_value = line.partition(":")
            if not sep:
                raise PolicyError("bad config line")
            key = key.strip()
            raw_value = raw_value.strip()
            if raw_value in ('""', "''"):
                node[key] = ""
                index += 1
                continue
            if raw_value == "":
                if index + 1 >= len(lines) or lines[index + 1][0] <= ind:
                    node[key] = ""
                    index += 1
                    continue
                child_indent = lines[index + 1][0]
                if lines[index + 1][1].startswith("- "):
                    items: list[Any] = []
                    index += 1
                    while (
                        index < len(lines)
                        and lines[index][0] == child_indent
                        and lines[index][1].startswith("- ")
                    ):
                        items.append(_scalar(lines[index][1][2:].strip()))
                        index += 1
                    node[key] = items
                    continue
                child, index = parse_block(index + 1, child_indent)
                node[key] = child
                continue
            node[key] = _scalar(raw_value)
            index += 1
        return node, index

    if not lines:
        return {}
    parsed, index = parse_block(0, lines[0][0])
    if index != len(lines):
        raise PolicyError("config was not fully parsed")
    return parsed


def secret_file_mode_ok(mode: int) -> bool:
    """True when a secret file is not readable by group or others."""
    return (mode & 0o077) == 0


def agent_environment(api_key: str, home: Path, tmp: Path, agent_bin: Path) -> dict[str, str]:
    """Environment for the sandboxed agent. The API key is the only secret.

    ``PATH`` is the agent directory plus the system directories. The production
    user's environment is not copied.
    """
    if not api_key or not api_key.strip():
        raise PolicyError("cursor api key is empty")
    return {
        "PATH": f"{agent_bin.parent}:/usr/bin:/bin:/usr/sbin:/sbin",
        "HOME": str(home),
        "TMPDIR": str(tmp),
        "LANG": "C.UTF-8",
        "CURSOR_API_KEY": api_key.strip(),
    }
