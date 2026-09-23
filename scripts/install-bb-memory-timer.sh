#!/usr/bin/env bash
# Install the BlueBubbles → Orion memory sync on the OpenClaw host.
#
# Copies notifications/bb_memory.py to ~/.openclaw/scripts/bb-max-memory/,
# points memory search at Gemini (this host authenticates Google, not OpenAI),
# and enables a user systemd timer that runs sync once a minute.
#
# Run on the Orion node as the openclaw user:
#   ./scripts/install-bb-memory-timer.sh
set -euo pipefail

STACK_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$STACK_ROOT/notifications/bb_memory.py"
DEST_DIR="${HOME}/.openclaw/scripts/bb-max-memory"
UNIT_DIR="${HOME}/.config/systemd/user"
OPENCLAW_BIN="${HOME}/.npm-global/bin/openclaw"

if [[ ! -f "$SRC" ]]; then
  echo "missing $SRC" >&2
  exit 1
fi
mkdir -p "$DEST_DIR" "$UNIT_DIR" "${HOME}/.openclaw/logs"
cp "$SRC" "$DEST_DIR/bb_memory.py"
chmod 755 "$DEST_DIR/bb_memory.py"

if [[ -x "$OPENCLAW_BIN" ]]; then
  "$OPENCLAW_BIN" config set agents.defaults.memorySearch.provider '"gemini"' --strict-json
fi

cat > "$UNIT_DIR/orion-bb-memory.service" <<EOF
[Unit]
Description=Ingest BlueBubbles texts to Max into Orion memory
After=network-online.target

[Service]
Type=oneshot
Environment=HOME=${HOME}
Environment=PATH=${HOME}/.npm-global/bin:/usr/bin:/bin
ExecStart=/usr/bin/python3 ${DEST_DIR}/bb_memory.py sync
EOF

cat > "$UNIT_DIR/orion-bb-memory.timer" <<EOF
[Unit]
Description=Run Orion BlueBubbles memory ingest every minute

[Timer]
OnBootSec=45
OnUnitActiveSec=60
AccuracySec=10
Persistent=true

[Install]
WantedBy=timers.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now orion-bb-memory.timer
echo "installed ${DEST_DIR}/bb_memory.py and orion-bb-memory.timer"
