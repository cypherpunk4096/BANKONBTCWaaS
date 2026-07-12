#!/usr/bin/env bash
# bankon-vault installer — the definitive chain-agnostic vault (BTC-first).
#   github.com/cypherpunk2048 standard · GPLv3 · no daemon, no LUKS required, offline-friendly.
#
#   curl -fsSL .../bankon-vault/install.sh | bash        # or:  bash install.sh
#
# Installs the Python deps (cryptography + embit), verifies the crypto + BTC stack, runs the test
# suite, and drops a `bankon-vault` launcher on PATH. Nothing touches your keys or the network.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN="${BANKON_VAULT_BIN:-$HOME/.local/bin}"
PY="${PYTHON:-python3}"

say() { printf '\033[38;5;208m▸\033[0m %s\n' "$*"; }

command -v "$PY" >/dev/null || { echo "python3 required"; exit 1; }
say "python: $($PY --version 2>&1)"

say "1/4  dependencies (cryptography + embit) …"
"$PY" -m pip install --user --quiet --upgrade cryptography embit || {
  echo "pip install failed — on Debian/Ubuntu try:  sudo apt install python3-pip python3-cryptography"; exit 1; }

say "2/4  self-check (AES-256-GCM + HKDF-SHA512 + embit BIP32/PSBT) …"
"$PY" - <<PYCHECK
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from embit import bip32, bip39, psbt, script
import sys; sys.path.insert(0, "$HERE")
from bankon_vault import BankonVault
from bankon_vault.chains.btc import BitcoinAdapter
print("   crypto core + BTC adapter import OK")
PYCHECK

say "3/4  test suite …"
"$PY" "$HERE/tests/test_vault.py"

say "4/4  launcher → $BIN/bankon-vault"
mkdir -p "$BIN"
cat > "$BIN/bankon-vault" <<LAUNCH
#!/usr/bin/env bash
exec "$PY" -c "import sys; sys.path.insert(0, '$HERE'); from bankon_vault.cli import main; main()" "\$@"
LAUNCH
chmod +x "$BIN/bankon-vault"

echo
say "installed. Try:"
echo "     bankon-vault init"
echo "     bankon-vault gen-btc --net regtest"
echo "     bankon-vault address --id btc.seed --net regtest"
case ":$PATH:" in *":$BIN:"*) ;; *) echo "  (add $BIN to PATH:  export PATH=\"$BIN:\$PATH\")";; esac
