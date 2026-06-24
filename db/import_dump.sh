#!/usr/bin/env bash
# Load a SQL dump into the local orion database.
#
# Dumps live in your overlay:  $ORION_OVERLAY_ROOT/db/dumps/
#
# From file:
#   ./db/import_dump.sh /path/to/dump.sql
#   ./db/import_dump.sh "$ORION_OVERLAY_ROOT/db/dumps/dump_DATE.sql"
#
# Stream stdin (no local copy — e.g. over SSH or pipe):
#   cat dump.sql | ./db/import_dump.sh --stdin
#   ssh user@production-host 'cat /path/to/dump.sql' | ./db/import_dump.sh -
set -euo pipefail

STACK_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$STACK_ROOT/config/.env"

# Also try overlay .env if ORION_OVERLAY_ROOT is set
if [[ -n "${ORION_OVERLAY_ROOT:-}" && -f "$ORION_OVERLAY_ROOT/config/.env" ]]; then
  ENV_FILE="$ORION_OVERLAY_ROOT/config/.env"
fi

usage() {
  echo "Usage: $0 /path/to/dump.sql | - | --stdin" >&2
  echo "  Dumps are typically stored in: \$ORION_OVERLAY_ROOT/db/dumps/" >&2
  exit 1
}

[[ $# -lt 1 ]] && usage

MODE="file"
DUMP=""
case "$1" in
  -|--stdin)
    MODE="stdin"
    ;;
  *)
    DUMP="$1"
    if [[ ! -f "$DUMP" ]]; then
      echo "ERROR: dump file not found: $DUMP" >&2
      exit 1
    fi
    ;;
esac

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

HOST="${MYSQL_HOST:-127.0.0.1}"
PORT="${MYSQL_PORT:-3306}"
USER="${MYSQL_USER:-orion}"
PASS="${MYSQL_PASSWORD:-}"
DB="${MYSQL_DATABASE:-orion_app}"

mysql_args=(
  -h "$HOST"
  -P "$PORT"
  -u "$USER"
  -p"$PASS"
  "$DB"
)

if [[ "$MODE" == "stdin" ]]; then
  if [[ ! -t 0 ]]; then
    echo "Streaming SQL dump into $DB on $HOST:$PORT as $USER (stdin) ..."
    mysql "${mysql_args[@]}"
    echo "Import complete."
  else
    echo "ERROR: stdin mode requires piped input (e.g. cat dump.sql | ./db/import_dump.sh -)" >&2
    exit 1
  fi
else
  echo "Importing $DUMP into $DB on $HOST:$PORT as $USER ..."
  mysql "${mysql_args[@]}" < "$DUMP"
  echo "Import complete."
fi
