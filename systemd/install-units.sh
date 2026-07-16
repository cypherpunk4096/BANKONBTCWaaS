#!/usr/bin/env bash
# install-units.sh — install BANKON user systemd units (no root needed).
# Mirrors the Algorand node's user-systemd pattern.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="$HOME/.config/systemd/user"
mkdir -p "$DEST"
cp -v "$HERE"/bitcoind.service "$HERE"/bankon-waas.service "$HERE"/bankon-console.service "$HERE"/bankon-dexy.service "$DEST/"
systemctl --user daemon-reload
echo
echo "Enable + start (note: bitcoind.service replaces 'bitcoind -daemon'):"
echo "  systemctl --user enable --now bitcoind.service"
echo "  systemctl --user enable --now bankon-waas.service bankon-console.service"
echo "  systemctl --user enable --now bankon-dexy.service   # optional: ⟲ DEXY on :8091"
echo "  loginctl enable-linger $USER     # keep services running after logout"
echo "Status:  systemctl --user status bankon-waas"
echo "Logs:    journalctl --user -u bankon-waas -f"
