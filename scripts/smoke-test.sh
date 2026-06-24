#!/usr/bin/env bash
# Orion stack smoke tests. Some checks expect MySQL/API key to be configured.
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
PASS=0
FAIL=0

check() {
  local name="$1"
  shift
  echo "==> $name"
  if "$@"; then
    echo "OK: $name"
    PASS=$((PASS + 1))
  else
    echo "FAIL: $name (see above)"
    FAIL=$((FAIL + 1))
  fi
  echo
}

check "feature config loads" "$ROOT/.venv/bin/python" -c "
from common.config import load_config, feature_enabled
c = load_config()
assert feature_enabled('rag')
assert feature_enabled('langgraph_multiagent')
print('features:', c['features'])
"

check "RAG retrieve import" "$ROOT/.venv/bin/python" -c "
import sys
sys.path.insert(0, '$ROOT')
from rag.retrieve import retrieve, collections_for_intent
assert 'docs' in collections_for_intent('general', 'readme setup')
print('retrieve OK')
"

check "RAG query" "$ROOT/bin/orion-rag-query" "CWR generator" --repo CWR-INTERFACE --k 1 2>/dev/null || \
  echo "SKIP: RAG query (index not built yet — run orion-rag-index)"

check "orion-db connectivity" "$ROOT/bin/orion-db" "SELECT 1 AS ok" || true

check "plan pipeline tests" "$ROOT/.venv/bin/python" -m codeflow.test_plan_pipeline -q

check "orion-code import" "$ROOT/.venv/bin/python" -c "
import sys
sys.path.insert(0, '$ROOT')
from codeflow.graph import build_graph
g = build_graph()
print('graph nodes:', list(g.get_graph().nodes))
"

check "watchdog dry-run" "$ROOT/.venv/bin/python" -c "
import json, subprocess, sys
proc = subprocess.run(
    ['$ROOT/bin/orion-watchdog', 'run', '--dry-run', '--json'],
    capture_output=True, text=True, check=False,
)
if proc.returncode != 0:
    print(proc.stderr or proc.stdout)
    sys.exit(1)
data = json.loads(proc.stdout)
checks_run = int(data.get('checks_run') or 0)
if checks_run < 1:
    print(f'expected at least 1 check, got {checks_run}')
    sys.exit(1)
if data.get('errors'):
    print('watchdog errors:', data['errors'])
    sys.exit(1)
print(f'watchdog OK: {checks_run} checks, {len(data.get(\"failures\") or [])} failures')
" || true

check "golden tests" "$ROOT/bin/orion-golden-test" --json || true

check "safety gate import" "$ROOT/.venv/bin/python" -c "
import sys
sys.path.insert(0, '$ROOT')
from codeflow.safety_gate import check_diff
print('safety_gate OK')
"

echo "Passed: $PASS  Failed: $FAIL"
exit $(( FAIL > 0 ? 1 : 0 ))
