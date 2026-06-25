#!/usr/bin/env bash
# Pre-publish audit: scans the public repo tree for proprietary data.
# Exit 1 if any hit is found; exit 0 if clean.
#
# Pre-publication scan; invoked as ./scripts/audit-before-publish.sh before git add/push.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FAIL=0

# Directories to exclude from search (gitignored or overlay)
EXCLUDE_DIRS=(
  ".git"
  ".venv"
  "node_modules"
  "__pycache__"
  "rag/chroma"
  "rag/bm25_corpus"
)

_build_excludes() {
  local args=()
  for d in "${EXCLUDE_DIRS[@]}"; do
    args+=(--exclude-dir="$d")
  done
  echo "${args[@]}"
}

_grep() {
  local label="$1"
  local pattern="$2"
  shift 2
  local excludes
  read -ra excludes <<< "$(_build_excludes)"
  # shellcheck disable=SC2086
  if grep -rn --include="$@" "${excludes[@]}" -E "$pattern" "$ROOT" 2>/dev/null; then
    echo "FAIL: $label" >&2
    FAIL=1
  else
    echo "OK:   $label"
  fi
}

echo "=== Orion audit-before-publish ==="
echo "Root: $ROOT"
echo ""

# Phone numbers (E.164 format: +1XXXXXXXXXX or raw 10-digit US)
_grep "phone numbers" '\+1[0-9]{10}|[0-9]{10}' "*.yaml" "*.yml" "*.py" "*.sh" "*.md" "*.txt"

# Personal emails (non-placeholder patterns)
_grep "personal/production emails" '[a-zA-Z0-9._%+-]+@(rightstune|example-private|yourcompany)\.(com|io|net)' "*.yaml" "*.yml" "*.py" "*.sh" "*.md"

# Mac/Windows absolute production paths
_grep "Mac production paths (/Users/)" '/Users/[a-zA-Z]' "*.yaml" "*.yml" "*.py" "*.sh" "*.md" "*.txt"

# Anthropic API keys
_grep "Anthropic API keys" 'sk-ant-api0[0-9]-[A-Za-z0-9_-]{10,}' "*.yaml" "*.yml" "*.py" "*.sh" "*.env" "*.json" "*.txt" "*.md"

# Stripe live/webhook secrets
_grep "Stripe live keys" 'sk_live_[A-Za-z0-9]{10,}' "*.yaml" "*.yml" "*.py" "*.sh" "*.env" "*.json" "*.txt" "*.md" "*.php"
_grep "Stripe webhook secrets" 'whsec_[A-Za-z0-9]{10,}' "*.yaml" "*.yml" "*.py" "*.sh" "*.env" "*.json" "*.txt" "*.md" "*.php"

# AWS keys
_grep "AWS access keys" 'AKIA[0-9A-Z]{16}' "*.yaml" "*.yml" "*.py" "*.sh" "*.env" "*.json" "*.txt" "*.md"

# NVM paths
_grep ".nvm paths" '\.nvm/versions/node' "*.yaml" "*.yml" "*.py" "*.sh" "*.md"

# SQL dump files (anything other than provision.sql)
if find "$ROOT/db" -name "*.sql" ! -name "provision.sql" 2>/dev/null | grep -q .; then
  echo "FAIL: SQL dumps in db/ (only provision.sql is allowed)" >&2
  find "$ROOT/db" -name "*.sql" ! -name "provision.sql" >&2
  FAIL=1
else
  echo "OK:   no SQL dumps in db/"
fi

# RAG generated artifact files (should be in overlay, not repo)
for artifact in "$ROOT/rag/index_manifest.json" "$ROOT/rag/bm25_corpus" "$ROOT/rag/chroma"; do
  if [[ -e "$artifact" ]]; then
    echo "FAIL: RAG artifact in repo tree: $artifact" >&2
    FAIL=1
  fi
done
echo "OK:   no RAG artifacts in repo tree"

# data/ runtime JSON that contains real incident/watchdog data
for jsonfile in \
  "$ROOT/data/incidents/active.json" \
  "$ROOT/data/incidents/state.json" \
  "$ROOT/data/watchdog/baselines.json" \
  "$ROOT/data/watchdog/run_history.json"
do
  if [[ -f "$jsonfile" ]]; then
    size=$(wc -c < "$jsonfile" 2>/dev/null || echo 0)
    if [[ "$size" -gt 10 ]]; then
      echo "FAIL: non-empty runtime data file in repo: $jsonfile (${size} bytes)" >&2
      FAIL=1
    fi
  fi
done
echo "OK:   runtime data files are empty or absent"

echo ""
if [[ "$FAIL" -ne 0 ]]; then
  echo "AUDIT FAILED — do not publish until all FAIL items are resolved." >&2
  exit 1
fi
echo "AUDIT PASSED — tree appears clean for OSS publication."
exit 0
