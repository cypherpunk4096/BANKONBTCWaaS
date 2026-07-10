#!/usr/bin/env bash
# bankon-uninstall.sh — complete, clean removal of BANKON (the modular extension).
#
# REMOVES:  every BANKON process, the ~/bankon-tools tree (code, node_modules, logs),
#           BANKON desktop launchers and autostart entries.
# NEVER TOUCHES:  Bitcoin Core, the blockchain, ~/.bitcoin, bitcoin.conf, or any wallet.
#
# Self-deletes safely: stages a copy of itself in /tmp and re-execs it, so deleting
# ~/bankon-tools out from under a running script is not a problem.
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ "${BANKON_UNINSTALL_STAGED:-}" != "1" ]; then
  tmp="$(mktemp /tmp/bankon-uninstall.XXXXXX.sh)"
  cp "$0" "$tmp" && chmod +x "$tmp"
  BANKON_UNINSTALL_STAGED=1 exec "$tmp" "$HERE"
fi
TARGET="${1:?usage: staged copy needs the bankon-tools path}"
case "$TARGET" in
  */bankon-tools) : ;;
  *) echo "refusing: unexpected target '$TARGET' (not a bankon-tools dir)"; exit 1 ;;
esac
echo "Uninstalling BANKON from $TARGET …"

# 1 · stop everything BANKON started (Bitcoin Core keeps running — attach, don't replace)
pkill -f "bankon_qt.py"              2>/dev/null
pkill -f "bankon-waas/server.mjs"    2>/dev/null
pkill -f "bankon-console/server.mjs" 2>/dev/null
pkill -f "bankon-algo/server.mjs"    2>/dev/null
pkill -f "bankon-eth/server.mjs"     2>/dev/null
pkill -f "bankon-launcher.py"        2>/dev/null
sleep 1

# 2 · desktop launchers / autostart entries
rm -f "$HOME/.local/share/applications/"bankon*.desktop \
      "$HOME/.config/autostart/"bankon*.desktop 2>/dev/null
update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true

# 3 · the BANKON tree itself — code, services, node_modules, logs, caches
rm -rf "$TARGET"

echo "✓ BANKON removed completely."
echo "  Bitcoin Core, the blockchain and all wallets were NOT touched."
rm -f "$0"
