#!/bin/bash
# Create the cursor-worker user and install the ForceCommand handoff.
#
# Run once, as an admin, from the operator account:
#   sudo ./scripts/setup-cursor-worker.sh
# The sudo user's home is written to denied_home in the installed config.
#
# This does not enable mac_sandbox_auto_fix and does not copy any secret.
# After it finishes, as cursor-worker:
#   - put the OpenClaw public key in ~/.ssh/authorized_keys (mode 600)
#   - put a GitHub deploy key at ~/.ssh/id_ed25519 (mode 600)
#   - put the Cursor API key in ~/.config/orion-handoff/cursor-api-key (mode 600)
#   - put a GitHub token in ~/.config/orion-handoff/gh-token (mode 600)
#   - install cursor-agent under this user's home, not under the operator home
#   - set cursor_agent_bin and enabled: true in ~/orion-handoff/mac_sandbox.yaml
# On the Orion node, set the same enabled flag in the overlay mac_sandbox.yaml
# (ssh_host, identity_file) and features.mac_sandbox_auto_fix: true.

set -euo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run with sudo so the cursor-worker user can be created." >&2
  exit 1
fi

USER_NAME="cursor-worker"
HOME_DIR="/Users/${USER_NAME}"
STACK_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
INSTALL_DIR="${HOME_DIR}/orion-handoff"
OPERATOR="${SUDO_USER:-}"
if [[ -z "$OPERATOR" || "$OPERATOR" == "root" ]]; then
  echo "Run with sudo from the operator account so denied_home can be set." >&2
  exit 1
fi
OPERATOR_HOME="$(dscl . -read "/Users/${OPERATOR}" NFSHomeDirectory 2>/dev/null | awk '{print $2}')"
if [[ -z "$OPERATOR_HOME" || "$OPERATOR_HOME" != /Users/* ]]; then
  echo "Could not resolve the operator home for ${OPERATOR}." >&2
  exit 1
fi

if ! id "$USER_NAME" >/dev/null 2>&1; then
  sysadminctl -addUser "$USER_NAME" \
    -fullName "Orion handoff" \
    -home "$HOME_DIR" \
    -shell /bin/bash
fi

# Not an admin. The home directory is not readable by the production user.
dseditgroup -o edit -d "$USER_NAME" -t user admin 2>/dev/null || true
chmod 700 "$HOME_DIR"

install -d -o "$USER_NAME" -g staff -m 755 "$INSTALL_DIR"
install -d -o "$USER_NAME" -g staff -m 700 "${HOME_DIR}/.ssh"
install -d -o "$USER_NAME" -g staff -m 700 "${HOME_DIR}/.config"
install -d -o "$USER_NAME" -g staff -m 700 "${HOME_DIR}/.config/orion-handoff"
install -d -o "$USER_NAME" -g staff -m 755 "${HOME_DIR}/clones"

install -o "$USER_NAME" -g staff -m 755 \
  "${STACK_ROOT}/scripts/orion-mac-handoff" \
  "${INSTALL_DIR}/orion-mac-handoff"
install -o "$USER_NAME" -g staff -m 644 \
  "${STACK_ROOT}/codeflow/sandbox_policy.py" \
  "${INSTALL_DIR}/sandbox_policy.py"
install -o "$USER_NAME" -g staff -m 644 \
  "${STACK_ROOT}/config/cursor-worker.sb" \
  "${INSTALL_DIR}/cursor-worker.sb"

if [[ ! -f "${INSTALL_DIR}/mac_sandbox.yaml" ]]; then
  sed "s#/Users/YOU#${OPERATOR_HOME}#g" \
    "${STACK_ROOT}/config/mac_sandbox.example.yaml" \
    > "${INSTALL_DIR}/mac_sandbox.yaml"
  chown "$USER_NAME":staff "${INSTALL_DIR}/mac_sandbox.yaml"
  chmod 644 "${INSTALL_DIR}/mac_sandbox.yaml"
fi

SNIPPET="/etc/ssh/sshd_config.d/cursor-worker.conf"
cat > "$SNIPPET" <<'EOF'
# Orion handoff. This key cannot get a shell.
Match User cursor-worker
    ForceCommand /Users/cursor-worker/orion-handoff/orion-mac-handoff
    PermitTTY no
    AllowTcpForwarding no
    AllowAgentForwarding no
    X11Forwarding no
    PermitTunnel no
    PasswordAuthentication no
EOF
chmod 644 "$SNIPPET"

echo "Installed ${INSTALL_DIR}."
echo "sshd already includes /etc/ssh/sshd_config.d/*. Reload sshd after you add the OpenClaw key."
echo "mac_sandbox.yaml is installed with enabled: false. Turn it on only after the key files exist."
