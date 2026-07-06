#!/usr/bin/env bash
# Install a post-merge git hook across all REPOS/* checkouts that
# incrementally reindexes each repo's RAG collections after `git pull`.
#
# Usage:
#   install-rag-reindex-hook.sh              # install into every REPOS/*/.git
#   install-rag-reindex-hook.sh --uninstall  # remove (only if hook matches our template)
#   install-rag-reindex-hook.sh --status     # show install state per repo
#
set -euo pipefail

STACK_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SOURCE_HOOK="${STACK_ROOT}/scripts/git-hooks/post-merge"
WORKSPACE="${HOME}/.openclaw/workspace"
REPOS_ROOT="${WORKSPACE}/REPOS"
HOOK_NAME="post-merge"

if [[ ! -f "${SOURCE_HOOK}" ]]; then
  echo "ERROR: missing source hook at ${SOURCE_HOOK}" >&2
  exit 1
fi

_repos() {
  [[ -d "${REPOS_ROOT}" ]] || return 0
  for dir in "${REPOS_ROOT}"/*/; do
    [[ -d "${dir}.git" ]] || continue
    echo "${dir%/}"
  done
}

_matches_source() {
  local target="$1"
  [[ -f "${target}" ]] || return 1
  cmp -s "${SOURCE_HOOK}" "${target}"
}

cmd_install() {
  local count=0 skipped=0
  while IFS= read -r repo; do
    [[ -n "${repo}" ]] || continue
    local hook_path="${repo}/.git/hooks/${HOOK_NAME}"
    if [[ -f "${hook_path}" ]] && ! _matches_source "${hook_path}"; then
      echo "SKIP  $(basename "${repo}"): existing ${HOOK_NAME} hook differs from template (not overwriting)"
      skipped=$((skipped + 1))
      continue
    fi
    mkdir -p "${repo}/.git/hooks"
    cp "${SOURCE_HOOK}" "${hook_path}"
    chmod +x "${hook_path}"
    echo "OK    $(basename "${repo}"): installed ${HOOK_NAME} hook"
    count=$((count + 1))
  done < <(_repos)
  echo ""
  echo "Installed: ${count}, skipped (custom hook present): ${skipped}"
}

cmd_uninstall() {
  local count=0
  while IFS= read -r repo; do
    [[ -n "${repo}" ]] || continue
    local hook_path="${repo}/.git/hooks/${HOOK_NAME}"
    if _matches_source "${hook_path}"; then
      rm -f "${hook_path}"
      echo "OK    $(basename "${repo}"): removed ${HOOK_NAME} hook"
      count=$((count + 1))
    fi
  done < <(_repos)
  echo ""
  echo "Removed: ${count}"
}

cmd_status() {
  while IFS= read -r repo; do
    [[ -n "${repo}" ]] || continue
    local hook_path="${repo}/.git/hooks/${HOOK_NAME}"
    local name
    name="$(basename "${repo}")"
    if [[ ! -f "${hook_path}" ]]; then
      echo "MISSING ${name}"
    elif _matches_source "${hook_path}"; then
      echo "OK      ${name}"
    else
      echo "STALE   ${name} (hook present but differs from template)"
    fi
  done < <(_repos)
}

case "${1:-}" in
  --uninstall) cmd_uninstall ;;
  --status) cmd_status ;;
  --help|-h)
    sed -n '2,10p' "$0"
    ;;
  *) cmd_install ;;
esac
