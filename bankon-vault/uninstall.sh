#!/usr/bin/env bash
# bankon-vault uninstaller — leaves NO trace. Securely erases the vault (N-pass shred), removes the
# launcher, and (optionally) the module source. Nothing survives: no keys, salt, state, or binaries.
#
#   bash bankon-vault/uninstall.sh                 # shred the default vault + remove launcher (confirms)
#   VAULT_PATH=~/.bankon-vault PASSES=7 bash bankon-vault/uninstall.sh
#   bash bankon-vault/uninstall.sh --purge-source  # also delete this module directory
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VAULT_PATH="${VAULT_PATH:-$HOME/.bankon-vault}"
PASSES="${PASSES:-7}"
BIN="${BANKON_VAULT_BIN:-$HOME/.local/bin}"
say() { printf '\033[38;5;208m▸\033[0m %s\n' "$*"; }

say "This will SECURELY ERASE the vault at $VAULT_PATH ($PASSES-pass shred) and remove the launcher."
if [ "${1:-}" != "--yes" ] && [ "${AUTO_YES:-}" != "1" ]; then
  read -r -p "type ERASE to confirm: " ans
  [ "$ans" = "ERASE" ] || { echo "aborted"; exit 1; }
fi

# 1) shred the vault directory (use the module's own traceless destroy for zeroize + shred)
if [ -e "$VAULT_PATH/.salt" ]; then
  python3 -c "import sys; sys.path.insert(0,'$HERE'); from bankon_vault.core import BankonVault; \
    print(BankonVault('$VAULT_PATH').destroy(shred_passes=$PASSES))"
else
  say "no vault at $VAULT_PATH (nothing to shred)"
fi

# 2) shred any stray policy/tomb state next to it, and the launcher
for f in "$VAULT_PATH".tomb "$VAULT_PATH".tomb.key; do
  [ -e "$f" ] && { command -v shred >/dev/null && shred -u -z -n "$PASSES" "$f" || rm -f "$f"; say "removed $f"; }
done
[ -e "$BIN/bankon-vault" ] && { rm -f "$BIN/bankon-vault"; say "removed launcher $BIN/bankon-vault"; }

# 3) remove __pycache__ + optionally the whole module source
find "$HERE" -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
if [ "${1:-}" = "--purge-source" ] || [ "${2:-}" = "--purge-source" ]; then
  say "purging module source $HERE"
  tmp="$(mktemp)"; cp "$0" "$tmp"; exec bash "$tmp" -c "rm -rf '$HERE'; echo '▸ bankon-vault fully removed — no trace.'" 2>/dev/null || rm -rf "$HERE"
fi
say "bankon-vault uninstalled — no keys, salt, or state remain."
