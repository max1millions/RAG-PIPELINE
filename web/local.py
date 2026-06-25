"""Load web_local.yaml and run local Playwright smoke tests."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

from common.config import STACK_ROOT, load_config, require_feature
from common.paths import load_layered_config, repos_root

WEB_LOCAL_PATH = STACK_ROOT / "config" / "web_local.yaml"
SMOKE_SCRIPT = STACK_ROOT / "web" / "smoke_pages.mjs"


def load_web_local() -> dict[str, Any]:
    data = load_layered_config("web_local")
    if not data:
        raise FileNotFoundError(
            "Missing web_local config. I expect ORION_OVERLAY_ROOT/config/web_local.yaml "
            "(see config/web_local.example.yaml) with docroot set."
        )
    return data


def _test_dir(cfg: dict[str, Any] | None = None) -> Path:
    """Resolve the npm test directory from config.

    Looks for web_local.yaml keys:
      docroot    — path relative to repos_root() (e.g. "REPOS/my-app" or "my-app")
      test_subdir — subdirectory inside docroot containing package.json (default "scripts/test")
    """
    if cfg is None:
        cfg = load_web_local()
    root = repos_root()
    docroot_val = str(cfg.get("docroot") or "")
    # Strip leading "REPOS/" prefix if present (legacy web_local.yaml format)
    if docroot_val.startswith("REPOS/"):
        docroot_val = docroot_val[len("REPOS/"):]
    docroot = root / docroot_val if docroot_val else root
    test_sub = str(cfg.get("test_subdir") or "scripts/test")
    return docroot / test_sub


def boot_script() -> Path:
    ws = Path(load_config()["paths"]["workspace"])
    return ws / "scripts" / "boot_local.sh"


def run_boot(cmd: str) -> subprocess.CompletedProcess[str]:
    script = boot_script()
    if not script.is_file():
        raise FileNotFoundError(f"Missing {script}")
    return subprocess.run(
        [str(script), cmd],
        capture_output=True,
        text=True,
        timeout=120,
    )


def ensure_stack_running() -> None:
    proc = run_boot("status")
    if proc.returncode == 0:
        return
    start = run_boot("start")
    if start.returncode != 0:
        raise RuntimeError(
            f"boot_local.sh start failed (exit {start.returncode}):\n"
            f"{start.stdout}\n{start.stderr}\n"
            "Local nginx not installed; boot_local.sh install-nginx is required first."
        )


def run_playwright_smoke(*, skip_boot: bool = False) -> int:
    require_feature("local_web", "Local web tests")
    cfg = load_web_local()
    base_url = str(cfg.get("base_url") or "http://127.0.0.1:8080").rstrip("/")
    test_dir = _test_dir(cfg)

    if not skip_boot:
        try:
            ensure_stack_running()
        except RuntimeError as exc:
            print(f"WARN: {exc}", file=sys.stderr)
            print(
                "Skipping HTTP smoke tests (nginx/php not ready). "
                "Unit tests will still run if configured.",
                file=sys.stderr,
            )
            return run_unit_tests_only(base_url, test_dir)

    if not test_dir.is_dir():
        print(f"ERROR: test dir missing: {test_dir}", file=sys.stderr)
        return 1

    import json as _json

    env = os.environ.copy()
    env["BASE_URL"] = base_url
    health_paths = cfg.get("health_paths") or ["/"]
    env["SMOKE_PATHS"] = _json.dumps(health_paths)

    unit = subprocess.run(
        ["npm", "run", "test:unit"],
        cwd=test_dir,
        env=env,
        timeout=180,
    )
    if unit.returncode != 0:
        return unit.returncode

    if skip_boot:
        return 0

    if not SMOKE_SCRIPT.is_file():
        print(f"ERROR: smoke script missing: {SMOKE_SCRIPT}", file=sys.stderr)
        return 1

    smoke = subprocess.run(
        ["node", str(SMOKE_SCRIPT)],
        cwd=test_dir,
        env=env,
        timeout=300,
    )
    return smoke.returncode


def run_unit_tests_only(base_url: str, test_dir: Path | None = None) -> int:
    if test_dir is None:
        test_dir = _test_dir()
    env = os.environ.copy()
    env["BASE_URL"] = base_url
    proc = subprocess.run(
        ["npm", "run", "test:unit"],
        cwd=test_dir,
        env=env,
        timeout=180,
    )
    return proc.returncode


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Run local web smoke tests")
    parser.add_argument("--skip-boot", action="store_true", help="Do not start nginx; only run npm tests")
    parser.add_argument("--status-only", action="store_true", help="Only run boot_local.sh status")
    args = parser.parse_args(argv)

    if args.status_only:
        proc = run_boot("status")
        if proc.stdout:
            print(proc.stdout, end="")
        if proc.stderr:
            print(proc.stderr, end="", file=sys.stderr)
        return proc.returncode

    return run_playwright_smoke(skip_boot=args.skip_boot)


if __name__ == "__main__":
    raise SystemExit(main())
