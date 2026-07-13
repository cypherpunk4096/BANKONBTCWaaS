#!/usr/bin/env bash
# bankon-ord installer — the OPTIONAL ordinals module. Installs the `ord` binary (prefers your own
# fork bankonvault/ord, falls back to the official ordinals/ord), then runs the Python self-check.
#
#   bash bankon-ord/install.sh                    # auto: prebuilt official → cargo fork → cargo crate
#   ORD_SOURCE=fork   bash bankon-ord/install.sh  # force-build github.com/bankonvault/ord
#   ORD_SOURCE=official bash bankon-ord/install.sh # force official prebuilt release
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN="${BANKON_ORD_BIN:-$HOME/.local/bin}"
SRC="${ORD_SOURCE:-auto}"
say() { printf '\033[38;5;208m▸\033[0m %s\n' "$*"; }

if command -v ord >/dev/null; then
  say "ord already installed: $(ord --version)"
else
  mkdir -p "$BIN"
  installed=""
  # 1) official prebuilt binary (fast, no toolchain) — ordinals/ord releases
  if [ "$SRC" = auto ] || [ "$SRC" = official ]; then
    arch="$(uname -m)"; os="$(uname -s | tr A-Z a-z)"
    case "$arch" in x86_64) A=x86_64;; aarch64|arm64) A=aarch64;; *) A="";; esac
    if [ -n "$A" ] && [ "$os" = linux ]; then
      say "fetching official ord prebuilt (ordinals/ord, $A-linux) …"
      url="$(curl -fsSL https://api.github.com/repos/ordinals/ord/releases/latest \
             | grep -oE "https://[^\"]*${A}-unknown-linux[^\"]*\.tar\.gz" | head -1 || true)"
      if [ -n "$url" ]; then
        tmp="$(mktemp -d)"; curl -fsSL "$url" -o "$tmp/ord.tgz" && tar -xzf "$tmp/ord.tgz" -C "$tmp" \
          && install -m755 "$tmp/ord" "$BIN/ord" && installed=1 && rm -rf "$tmp"
      fi
    fi
  fi
  # 2) build YOUR fork (bankonvault/ord) or the crate, via cargo
  if [ -z "$installed" ]; then
    command -v cargo >/dev/null || { echo "need cargo (rustup.rs) for a source build, or a prebuilt release"; exit 1; }
    if [ "$SRC" = fork ] || [ "$SRC" = auto ]; then
      say "building your fork github.com/bankonvault/ord (cargo) …"
      cargo install --git https://github.com/bankonvault/ord --root "$HOME/.local" && installed=1 || true
    fi
    [ -z "$installed" ] && { say "building official ord crate …"; cargo install ord --root "$HOME/.local"; }
  fi
  say "ord installed: $("$BIN/ord" --version 2>/dev/null || ord --version)"
fi

say "python self-check + tests …"
python3 "$HERE/tests/test_ord.py"

say "preflight (mainnet & testnet):"
python3 -c "import sys; sys.path.insert(0,'$HERE'); from bankon_ord import OrdCli; import json
for n in ('mainnet','testnet'): print(n, '→', json.dumps(OrdCli(n).preflight().get('notes')))"

echo
say "launcher → $BIN/bankon-ord"
cat > "$BIN/bankon-ord" <<LAUNCH
#!/usr/bin/env bash
exec python3 -c "import sys; sys.path.insert(0, '$HERE'); from bankon_ord.cli import main; main()" "\$@"
LAUNCH
chmod +x "$BIN/bankon-ord"
say "done.  try:  bankon-ord preflight --net testnet"
