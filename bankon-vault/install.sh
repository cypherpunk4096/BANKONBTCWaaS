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

say "1/4  dependencies (cryptography + embit) — OS-aware …"
# `cryptography` ships a compiled backend: on musl (Alpine) and OpenBSD a bare `pip install` tries to
# BUILD it (needs a Rust/OpenSSL toolchain). Prefer the SYSTEM package for cryptography everywhere it
# exists, then pip only the pure-Python `embit`. Detect the OS/pkgmgr and act accordingly.
_sudo=""; [ "$(id -u)" != 0 ] && { command -v sudo >/dev/null && _sudo=sudo || { command -v doas >/dev/null && _sudo=doas; }; }
if [ -f /etc/alpine-release ] || command -v apk >/dev/null 2>&1; then
  say "  Alpine (musl) — system py3-cryptography + build deps for any wheels"
  $_sudo apk add --no-cache py3-cryptography py3-pip python3 || true
  "$PY" -m pip install --user --quiet --break-system-packages embit 2>/dev/null \
    || "$PY" -m pip install --user --quiet embit
elif [ "$(uname -s)" = OpenBSD ] || command -v pkg_add >/dev/null 2>&1; then
  say "  OpenBSD — system py3-cryptography (pip build of cryptography is unsupported here)"
  $_sudo pkg_add -I py3-cryptography py3-pip 2>/dev/null || true
  "$PY" -m pip install --user --quiet embit || true
else
  say "  Debian/Ubuntu — prefer system python3-cryptography, pip the rest"
  $_sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y python3-cryptography python3-pip >/dev/null 2>&1 || true
  "$PY" -m pip install --user --quiet --upgrade cryptography embit 2>/dev/null \
    || "$PY" -m pip install --user --quiet embit
fi
# OPTIONAL post-quantum backends (pure Python, no toolchain) — hybrid ML-KEM custody + ML-DSA
# quorum activate when present; everything degrades honestly without them (see SECURITY.md).
"$PY" -m pip install --user --quiet kyber-py dilithium-py 2>/dev/null \
  || "$PY" -m pip install --user --quiet --break-system-packages kyber-py dilithium-py 2>/dev/null \
  || say "  (optional PQC backends not installed — classical custody unaffected)"
# final gate: both must import (cryptography via system OR pip; embit via pip)
"$PY" -c "import cryptography, embit" 2>/dev/null || {
  echo "dependencies missing. Install per-OS:"
  echo "  Alpine : sudo apk add py3-cryptography && pip install --user embit"
  echo "  OpenBSD: doas pkg_add py3-cryptography && pip install --user embit"
  echo "  Debian : sudo apt install python3-cryptography && pip install --user embit"
  exit 1; }

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
