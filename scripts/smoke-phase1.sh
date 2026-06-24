#!/usr/bin/env bash
# Phase 1 smoke tests (no LLM / no git push).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="$ROOT/.venv/bin/python"
cd "$ROOT"

echo "[1] test_runner pass/fail/skip"
"$PY" -c "
from pathlib import Path
from codeflow.test_runner import run_tests
path = Path('$ROOT/../REPOS/CIS-NET-AUTOMATION')
assert run_tests('CIS-NET-AUTOMATION', path)['passed']
assert not run_tests('CIS-NET-AUTOMATION', path, test_cmd_override='false')['passed']
assert run_tests('NO-SUCH-REPO', path)['passed']
"

echo "[2] graph compile + test_run_node"
"$PY" -c "
from codeflow.graph import build_graph
from codeflow.nodes import test_run_node
g = build_graph()
assert 'test_run' in g.get_graph().nodes
state = {
    'repo': 'CIS-NET-AUTOMATION',
    'repo_path': '$ROOT/../REPOS/CIS-NET-AUTOMATION',
    'changed_files': ['phase3.py'],
    'syntax_results': 'phase3.py: OK',
}
assert test_run_node(state)['test_passed'] is True
"

echo "[3] patch_apply unit tests"
"$PY" "$ROOT/codeflow/test_patch_apply.py"

echo "[4] orion-fix CLI help"
"$ROOT/bin/orion-fix" --help >/dev/null

echo "[5] orion-incident remediate subcommand"
"$ROOT/bin/orion-incident" remediate --help >/dev/null

echo "Phase 1 smoke tests passed."
