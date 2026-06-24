"""Run per-repo test commands from config/repo_tests.yaml (overlay-aware)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from common.config import STACK_ROOT, load_config
from common.paths import load_layered_config

# Legacy constant kept for any external references.
REPO_TESTS_PATH = STACK_ROOT / "config" / "repo_tests.yaml"

_DEFAULTS_FALLBACK: dict[str, Any] = {
    "repos": {},
    "defaults": {"on_missing": "skip", "timeout_s": 120},
}


def _expand_tokens(cmd: str) -> str:
    """Expand ${STACK_ROOT} tokens so example configs are path-portable."""
    return cmd.replace("${STACK_ROOT}", str(STACK_ROOT))


def load_repo_tests(reload: bool = False) -> dict[str, Any]:
    data = load_layered_config("repo_tests")
    if not data:
        return dict(_DEFAULTS_FALLBACK)
    return data


def _truncate(text: str, limit: int = 4000) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 20] + "\n...(truncated)"


def run_tests(
    repo: str,
    repo_path: Path,
    *,
    changed_files: list[str] | None = None,
    test_cmd_override: str | None = None,
    test_file: str | None = None,
) -> dict[str, Any]:
    """Execute configured test commands; return structured result."""
    cfg = load_config()
    repo_cfg = load_repo_tests()
    defaults = repo_cfg.get("defaults") or {}
    on_missing = str(defaults.get("on_missing") or "skip")
    default_timeout = int(
        defaults.get("timeout_s") or cfg.get("limits", {}).get("subprocess_timeout_s", 120)
    )

    commands: list[str] = []
    timeout_s = default_timeout

    if test_cmd_override:
        commands = [_expand_tokens(test_cmd_override)]
    elif test_file:
        rel = test_file
        if rel.endswith(".py"):
            commands = [f"python3 -m pytest {rel} -q"]
        else:
            commands = [f"bash -n {rel}" if rel.endswith(".sh") else f"test -f {rel}"]
    else:
        entry = (repo_cfg.get("repos") or {}).get(repo)
        if entry is None:
            if on_missing == "fail":
                return {
                    "passed": False,
                    "stdout": "",
                    "stderr": f"No test config for repo {repo!r}",
                    "exit_code": 1,
                    "commands_run": [],
                    "note": "missing_config",
                }
            return {
                "passed": True,
                "stdout": "",
                "stderr": "",
                "exit_code": 0,
                "commands_run": [],
                "note": "no tests configured",
            }
        raw_cmds = list(entry.get("commands") or [])
        commands = [_expand_tokens(c) for c in raw_cmds]
        timeout_s = int(entry.get("timeout_s") or default_timeout)

    if not commands:
        return {
            "passed": True,
            "stdout": "",
            "stderr": "",
            "exit_code": 0,
            "commands_run": [],
            "note": "empty command list",
        }

    all_stdout: list[str] = []
    all_stderr: list[str] = []
    last_code = 0
    ran: list[str] = []

    for cmd in commands:
        ran.append(cmd)
        try:
            proc = subprocess.run(
                cmd,
                shell=True,
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=timeout_s,
            )
        except subprocess.TimeoutExpired:
            return {
                "passed": False,
                "stdout": _truncate("\n".join(all_stdout)),
                "stderr": _truncate(f"TIMEOUT after {timeout_s}s: {cmd}"),
                "exit_code": -1,
                "commands_run": ran,
                "note": "timeout",
            }
        except FileNotFoundError as exc:
            return {
                "passed": False,
                "stdout": "",
                "stderr": str(exc),
                "exit_code": 127,
                "commands_run": ran,
                "note": "runner_missing",
            }

        if proc.stdout:
            all_stdout.append(f"$ {cmd}\n{proc.stdout}")
        if proc.stderr:
            all_stderr.append(f"$ {cmd}\n{proc.stderr}")
        last_code = proc.returncode
        if proc.returncode != 0:
            break

    stdout = _truncate("\n".join(all_stdout))
    stderr = _truncate("\n".join(all_stderr))
    passed = last_code == 0

    return {
        "passed": passed,
        "stdout": stdout,
        "stderr": stderr,
        "exit_code": last_code,
        "commands_run": ran,
        "note": "ok" if passed else "test_failed",
    }


def format_test_results(result: dict[str, Any]) -> str:
    lines = [f"passed={result.get('passed')} exit_code={result.get('exit_code')}"]
    note = result.get("note")
    if note:
        lines.append(f"note: {note}")
    for cmd in result.get("commands_run") or []:
        lines.append(f"cmd: {cmd}")
    if result.get("stdout"):
        lines.append("--- stdout ---")
        lines.append(str(result["stdout"]))
    if result.get("stderr"):
        lines.append("--- stderr ---")
        lines.append(str(result["stderr"]))
    return "\n".join(lines)
