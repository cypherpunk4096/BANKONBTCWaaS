#!/bin/sh
# node-setup.sh (cryptoDebian) — provision the persistent Bitcoin-Core + multi-crypto NODE on a
# bankonDebian base (Debian/Ubuntu). Dedicated user, txindex/loopback-RPC conf, systemd service.
# On a live-persistent image this survives via the persistence partition; on a sys-install it's durable.
# Idempotent.
#
#   COINS="bitcoin" sudo sh node-setup.sh      # default: bitcoin. Bitcoin Core uses BANKON's verified
#                                              # install-core when the repo is present (no distro pkg needed).
set -eu
COINS="${COINS:-bitcoin}"
BTC_HOME="${BTC_HOME:-/var/lib/bitcoind}"
BANKON_DIR="${BANKON_DIR:-$HOME/bankon-tools}"
say()  { printf '\033[38;5;208m▸ %s\033[0m\n' "$*"; }
warn() { printf 'WARN: %s\n' "$*" >&2; }
command -v apt-get >/dev/null || { echo "cryptoDebian node runs on Debian/Ubuntu (bankonDebian base)"; exit 1; }
SUDO=""; [ "$(id -u)" != 0 ] && SUDO=sudo

pkgfor() { case "$1" in
  monero)  echo monero ;;
  litecoin) echo litecoin ;;
  *) echo "-" ;;                         # bitcoin handled specially (verified install-core)
esac; }

say "cryptoDebian node — coins: $COINS"

# Bitcoin Core: prefer BANKON's SHA256SUMS-verified installer; else Debian has no official pkg → guide
install_bitcoin() {
  if command -v bitcoind >/dev/null; then say "  bitcoind present"; return; fi
  if [ -x "$BANKON_DIR/bankon" ]; then
    say "  installing Bitcoin Core via BANKON's verified install-core (SHA256SUMS-checked)"
    "$BANKON_DIR/bankon" install-core
    for b in bitcoind bitcoin-cli; do
      [ -x "$HOME/bitcoin-31.0/bin/$b" ] && $SUDO ln -sf "$HOME/bitcoin-31.0/bin/$b" /usr/local/bin/$b
    done
  else
    warn "no BANKON install-core found — clone github.com/cypherpunk2048/bankon-tools and run ./bankon install-core"
    return 1
  fi
}

for c in $COINS; do
  if [ "$c" = bitcoin ]; then install_bitcoin || true; continue; fi
  p="$(pkgfor "$c")"; [ "$p" = "-" ] && { warn "$c: no Debian package — build from source or skip"; continue; }
  say "install $c ($p)…"; $SUDO env DEBIAN_FRONTEND=noninteractive apt-get install -y "$p" >/dev/null 2>&1 || warn "$c: apt install failed"
done

if echo " $COINS " | grep -q " bitcoin " && command -v bitcoind >/dev/null; then
  say "configure Bitcoin Core → $BTC_HOME (txindex, loopback RPC, dedicated user, systemd)"
  id bitcoin >/dev/null 2>&1 || $SUDO useradd -r -m -d "$BTC_HOME" -s /usr/sbin/nologin bitcoin 2>/dev/null || true
  $SUDO mkdir -p "$BTC_HOME"
  if [ ! -f "$BTC_HOME/bitcoin.conf" ]; then
    printf '%s\n' 'server=1' 'daemon=0' 'txindex=1' 'listen=1' \
      'rpcbind=127.0.0.1' 'rpcallowip=127.0.0.1' 'dbcache=450' \
      '# cryptoDebian: loopback RPC only; front with BANKON Console for read-only access' \
      | $SUDO tee "$BTC_HOME/bitcoin.conf" >/dev/null
  fi
  $SUDO chown -R bitcoin:bitcoin "$BTC_HOME"
  if [ ! -f /etc/systemd/system/bitcoind.service ]; then
    $SUDO tee /etc/systemd/system/bitcoind.service >/dev/null <<EOF
[Unit]
Description=Bitcoin Core daemon (cryptoDebian node)
After=network-online.target
Wants=network-online.target
[Service]
User=bitcoin
Group=bitcoin
Type=simple
ExecStart=$(command -v bitcoind) -datadir=$BTC_HOME -conf=$BTC_HOME/bitcoin.conf
Restart=on-failure
TimeoutStopSec=600
# hardening
NoNewPrivileges=true
ProtectSystem=full
PrivateTmp=true
[Install]
WantedBy=multi-user.target
EOF
    $SUDO systemctl daemon-reload
    $SUDO systemctl enable bitcoind 2>/dev/null || true
    say "  bitcoind systemd service installed + enabled (systemctl start bitcoind)"
  fi
fi

say "node provisioned."
say "  start:  sudo systemctl start bitcoind   ·   status: systemctl status bitcoind"
say "  attach BANKON read-only Console/WaaS to :8332 for the sovereign stack."
