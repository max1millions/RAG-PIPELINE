#!/usr/bin/env bash
# Install chat-branch git guardrails and retire the shared orion branch.
#
# Usage:
#   install-git-guard.sh                 # install hooks, point pipeline repos at cursor/*, retire orion
#   install-git-guard.sh --hooks-only    # copy hooks only
#   install-git-guard.sh --status
set -euo pipefail

STACK_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="${STACK_ROOT}/scripts/openclaw-git-guard"
HOOKS="${HOME}/.openclaw/git-hooks"
BIN_GIT="${HOME}/.openclaw/bin/git"
WS="${HOME}/.openclaw/workspace"
PIPELINE_REPOS=(
  CIS-NET-AUTOMATION
  CWR-INTERFACE
  DATABASE-EXPORT
  DATABASE-INSERT
  ISWC-SERVICE
  MUSO-API
  rightstune.com
  SCHEMA
  SQL-SCRIPTS
)

cmd="${1:-install}"

install_hooks() {
  mkdir -p "${HOOKS}"
  cp "${SRC}/pre-push" "${HOOKS}/pre-push"
  cp "${SRC}/pull-allowed.sh" "${HOOKS}/pull-allowed.sh"
  chmod +x "${HOOKS}/pre-push" "${HOOKS}/pull-allowed.sh"
  if [[ -f "${SRC}/git" ]]; then
    mkdir -p "$(dirname "${BIN_GIT}")"
    cp "${SRC}/git" "${BIN_GIT}"
    chmod +x "${BIN_GIT}"
    echo "OK    git wrapper installed at ${BIN_GIT}"
  fi
  echo "OK    hooks installed in ${HOOKS}"
}

configure_repo() {
  local name="$1"
  local dir="${WS}/REPOS/${name}"
  if [[ ! -d "${dir}/.git" && ! -f "${dir}/.git" ]]; then
    echo "SKIP  ${name}: not a git checkout"
    return
  fi
  git -C "${dir}" config core.hooksPath "${HOOKS}"
  git -C "${dir}" config hooks.allowed-push-branch cursor
  echo "OK    ${name}: chat branches (cursor/*)"
}

retire_orion() {
  local name="$1"
  local dir="${WS}/REPOS/${name}"
  [[ -d "${dir}/.git" || -f "${dir}/.git" ]] || return 0
  if ! git -C "${dir}" show-ref --verify --quiet refs/remotes/origin/orion \
    && ! git -C "${dir}" show-ref --verify --quiet refs/heads/orion; then
    echo "OK    ${name}: no orion branch"
    return 0
  fi
  git -C "${dir}" fetch origin main orion --quiet || true
  local ahead="0"
  if git -C "${dir}" show-ref --verify --quiet refs/remotes/origin/orion; then
    ahead="$(git -C "${dir}" rev-list --count origin/main..origin/orion 2>/dev/null || echo unknown)"
  fi
  if [[ "${ahead}" != "0" ]]; then
    echo "KEEP  ${name}: origin/orion has ${ahead} commit(s) not in main — not deleting"
    return 0
  fi
  local current
  current="$(git -C "${dir}" branch --show-current || true)"
  if [[ "${current}" == "orion" ]]; then
    git -C "${dir}" checkout main
  fi
  if git -C "${dir}" show-ref --verify --quiet refs/remotes/origin/orion; then
    git -C "${dir}" push origin --delete orion
    echo "DEL   ${name}: removed origin/orion"
  fi
  if git -C "${dir}" show-ref --verify --quiet refs/heads/orion; then
    git -C "${dir}" branch -D orion
    echo "DEL   ${name}: removed local orion"
  fi
}

cmd_status() {
  echo "hooks: ${HOOKS}/pre-push"
  for name in "${PIPELINE_REPOS[@]}"; do
    local dir="${WS}/REPOS/${name}"
    [[ -d "${dir}/.git" || -f "${dir}/.git" ]] || continue
    local mode current
    mode="$(git -C "${dir}" config --get hooks.allowed-push-branch || true)"
    current="$(git -C "${dir}" branch --show-current || true)"
    echo "${name}: push=${mode:-unset} checked-out=${current}"
  done
}

case "${cmd}" in
  --status)
    cmd_status
    ;;
  --hooks-only)
    install_hooks
    ;;
  install|"")
    # Delete orion before the new pre-push hook rejects that ref.
    for name in "${PIPELINE_REPOS[@]}"; do
      retire_orion "${name}"
    done
    install_hooks
    for name in "${PIPELINE_REPOS[@]}"; do
      configure_repo "${name}"
    done
    ;;
  *)
    echo "usage: install-git-guard.sh [--hooks-only|--status]" >&2
    exit 2
    ;;
esac
