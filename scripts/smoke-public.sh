#!/usr/bin/env bash
# Public smoke tests — runs on a fresh clone with no overlay, no MySQL, no REPOS.
# These verify that the framework code imports and basic CLI wiring works.
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
PY="$ROOT/.venv/bin/python"
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
    echo "FAIL: $name"
    FAIL=$((FAIL + 1))
  fi
  echo
}

check "feature config loads" "$PY" -c "
from common.config import load_config, feature_enabled
c = load_config()
assert 'features' in c
print('features:', list(c['features'].keys()))
"

check "overlay_root resolves (None or path)" "$PY" -c "
from common.paths import overlay_root, stack_root, data_dir, rag_artifacts_dir
print('stack_root:', stack_root())
print('overlay_root:', overlay_root())
print('data_dir(incidents):', data_dir('incidents'))
print('rag_artifacts_dir:', rag_artifacts_dir())
"

check "RAG retrieve imports" "$PY" -c "
from rag.retrieve import retrieve, collections_for_intent
assert 'docs' in collections_for_intent('general', 'readme setup')
print('retrieve OK')
"

check "codeflow graph builds" "$PY" -c "
from codeflow.graph import build_graph
g = build_graph()
print('graph nodes:', list(g.get_graph().nodes))
"

check "safety gate imports" "$PY" -c "
from codeflow.safety_gate import check_diff
print('safety_gate OK')
"

check "incidents settings import" "$PY" -c "
from incidents.settings import openclaw_token, ensure_data_dir
print('incidents/settings OK')
"

check "watchdog settings import" "$PY" -c "
from watchdog.settings import ensure_data_dir, repos_root
print('watchdog/settings OK')
"

check "redaction module" "$PY" -c "
from rag.redaction import redact, should_skip_file
assert '[REDACTED_STRIPE_WEBHOOK]' in redact('whsec_testABCDEFGHIJ1234567890')
assert should_skip_file('.env')
assert not should_skip_file('.env.example')
print('redaction OK')
"

check "orion-fix CLI help" "$ROOT/bin/orion-fix" --help

check "orion-incident remediate help" "$ROOT/bin/orion-incident" remediate --help
check "orion-watchdog remediate help" "$ROOT/bin/orion-watchdog" remediate --help

check "audit-before-publish exits 0" bash "$ROOT/scripts/audit-before-publish.sh"

echo "Passed: $PASS  Failed: $FAIL"
exit $(( FAIL > 0 ? 1 : 0 ))
