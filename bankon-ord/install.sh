#!/usr/bin/env bash
# bankon-ord installer — the OPTIONAL ordinals module. Installs the `ord` binary with an explicit
# DEPLOY CHOICE (source build vs prebuilt binary), then runs the Python self-check.
#
#   bash bankon-ord/install.sh                     # auto: source build if cargo exists, else binary
#   ORD_SOURCE=source  bash bankon-ord/install.sh  # cargo build of the official crate (recommended)
#   ORD_SOURCE=binary  bash bankon-ord/install.sh  # official prebuilt release (needs glibc >= 2.38!)
#   ORD_SOURCE=fork    bash bankon-ord/install.sh  # cargo build of github.com/bankonvault/ord
#
# Why source is the default when cargo exists: the official prebuilt is linked against a NEWER
# glibc (2.38/2.39) than many stable hosts carry (e.g. Ubuntu 22.04 = 2.35) — it simply won't run
# there. A cargo build links against YOUR glibc and always matches the host.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN="${BANKON_ORD_BIN:-$HOME/.local/bin}"
SRC="${ORD_SOURCE:-auto}"
say() { printf '\033[38;5;208m▸\033[0m %s\n' "$*"; }

glibc_ok() {  # prebuilt ord needs glibc >= 2.38
  v="$(ldd --version 2>/dev/null | head -1 | grep -oE '[0-9]+\.[0-9]+$' || echo 0)"
  [ "$(printf '%s\n2.38\n' "$v" | sort -V | head -1)" = "2.38" ]
}

install_binary() {
  arch="$(uname -m)"; os="$(uname -s | tr A-Z a-z)"
  case "$arch" in x86_64) A=x86_64;; aarch64|arm64) A=aarch64;; *) A="";; esac
  [ -n "$A" ] && [ "$os" = linux ] || { say "no prebuilt for $os/$arch"; return 1; }
  if ! glibc_ok; then
    say "host glibc $(ldd --version | head -1 | grep -oE '[0-9.]+$') < 2.38 — the prebuilt WILL NOT run; use ORD_SOURCE=source"
    return 1
  fi
  say "fetching official ord prebuilt (ordinals/ord, $A-linux) …"
  url="$(curl -fsSL https://api.github.com/repos/ordinals/ord/releases/latest \
         | grep -oE "https://[^\"]*${A}-unknown-linux[^\"]*\.tar\.gz" | head -1 || true)"
  [ -n "$url" ] || return 1
  tmp="$(mktemp -d)"
  curl -fsSL "$url" -o "$tmp/ord.tgz" && tar -xzf "$tmp/ord.tgz" -C "$tmp" || { rm -rf "$tmp"; return 1; }
  # release tarballs nest the binary under ord-<ver>/
  bin="$(find "$tmp" -name ord -type f | head -1)"
  [ -n "$bin" ] && install -m755 "$bin" "$BIN/ord" && "$BIN/ord" --version >/dev/null 2>&1 \
    || { rm -rf "$tmp"; rm -f "$BIN/ord"; return 1; }
  rm -rf "$tmp"
}

install_source() {
  command -v cargo >/dev/null || { say "need cargo (rustup.rs) for a source build"; return 1; }
  if [ "${1:-}" = fork ]; then
    say "building your fork github.com/bankonvault/ord (cargo — takes a while) …"
    cargo install --git https://github.com/bankonvault/ord --locked --root "$HOME/.local"
  else
    say "building the official ord crate from source (cargo — takes a while) …"
    cargo install ord --locked --root "$HOME/.local"
  fi
}

if command -v ord >/dev/null; then
  say "ord already installed: $(ord --version)"
else
  mkdir -p "$BIN"
  case "$SRC" in
    source) install_source ;;
    fork)   install_source fork ;;
    binary) install_binary || { echo "prebuilt install failed — try ORD_SOURCE=source"; exit 1; } ;;
    auto)   if command -v cargo >/dev/null; then install_source || install_binary
            else install_binary || { echo "no cargo and the prebuilt failed — install rustup, then ORD_SOURCE=source"; exit 1; }
            fi ;;
    *) echo "ORD_SOURCE must be source|binary|fork|auto"; exit 1 ;;
  esac
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
