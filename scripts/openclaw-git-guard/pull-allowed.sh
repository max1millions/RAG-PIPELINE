#!/bin/sh
# Shared pull-branch allowlist for OpenClaw git guardrails.
# Source from ~/.openclaw/bin/git (set GIT_BIN, optionally GIT_CWD) or git-hooks.

_PULL_GIT="${GIT_BIN:-git}"

_pull_git() {
  if [ -n "${GIT_CWD:-}" ]; then
    "$_PULL_GIT" -C "$GIT_CWD" "$@"
  else
    "$_PULL_GIT" "$@"
  fi
}

pull_allowed() {
  branch="$1"
  case "$branch" in
    main) return 0 ;;
    cursor/*)
      echo "$branch" | grep -Eq '^cursor/[A-Za-z0-9._/-]+$' || return 1
      return 0
      ;;
    orion)
      # Legacy shared branch. Allowed only while the ref still exists.
      _pull_git show-ref --verify --quiet refs/heads/orion && return 0
      _pull_git show-ref --verify --quiet refs/remotes/origin/orion && return 0
      return 1
      ;;
    *) return 1 ;;
  esac
}

pull_allowed_message() {
  printf '%s' "origin/main, origin/cursor/*"
}
