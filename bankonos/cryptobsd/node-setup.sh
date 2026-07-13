#!/bin/sh
# node-setup.sh — provision the cryptoBSD NODE: Bitcoin Core (foundational) + optional other crypto
# daemons, on a PERSISTENT OpenBSD base (FuguIta mode 3, encrypted volume). Idempotent; run via
# cryptobsd.sh node. Persist afterwards with `usbfadm`.
#
#   COINS="bitcoin monero" doas sh node-setup.sh      # pick which daemons (default: bitcoin)
set -eu
COINS="${COINS:-bitcoin}"
say()  { printf '\033[38;5;208m▸ %s\033[0m\n' "$*"; }
warn() { printf 'WARN: %s\n' "$*" >&2; }
SUDO=""; [ "$(id -u)" != 0 ] && SUDO=doas
PKG="$SUDO pkg_add -I"

# OpenBSD package names per coin (extend as ports land). '-' = not packaged → note only.
pkgfor() { case "$1" in
  bitcoin) echo bitcoin ;;
  monero)  echo monero ;;
  litecoin) echo litecoin ;;
  *) echo "-" ;;
esac; }

say "cryptoBSD node — coins: $COINS"
[ -n "${PKG_PATH:-}" ] || export PKG_PATH="https://cdn.openbsd.org/pub/OpenBSD/$(uname -r)/packages/$(uname -m)/"

for c in $COINS; do
  p="$(pkgfor "$c")"
  if [ "$p" = "-" ]; then warn "$c: no OpenBSD package yet — build from source in /usr/ports or skip"; continue; fi
  say "install $c ($p)…"
  $PKG "$p" 2>/dev/null || warn "$c: pkg_add failed (check PKG_PATH / network for the initial provision)"
done

# Bitcoin Core: a hardened, txindex'd config on the persistent volume; run as a dedicated _bitcoin user
if echo " $COINS " | grep -q " bitcoin "; then
  BTC_HOME="/var/bitcoin"
  say "configure Bitcoin Core → $BTC_HOME (txindex, localhost RPC, OpenBSD-friendly)"
  $SUDO mkdir -p "$BTC_HOME"
  id _bitcoin >/dev/null 2>&1 || $SUDO useradd -d "$BTC_HOME" -s /sbin/nologin _bitcoin 2>/dev/null || true
  if [ ! -f "$BTC_HOME/bitcoin.conf" ]; then
    printf '%s\n' \
      'server=1' 'daemon=0' 'txindex=1' 'listen=1' \
      'rpcbind=127.0.0.1' 'rpcallowip=127.0.0.1' \
      'dbcache=300' \
      '# cryptoBSD: keep RPC on loopback; front it with BANKON Console for read-only access' \
      | $SUDO tee "$BTC_HOME/bitcoin.conf" >/dev/null
  fi
  $SUDO chown -R _bitcoin "$BTC_HOME" 2>/dev/null || true
  # rc.d service so the node survives reboots (persisted via usbfadm)
  if command -v rcctl >/dev/null 2>&1 && [ ! -f /etc/rc.d/bitcoind ]; then
    printf '%s\n' '#!/bin/ksh' \
      'daemon="/usr/local/bin/bitcoind"' \
      'daemon_user="_bitcoin"' \
      "daemon_flags=\"-datadir=$BTC_HOME -conf=$BTC_HOME/bitcoin.conf\"" \
      '. /etc/rc.d/rc.subr' 'rc_bg=YES' 'rc_cmd $1' \
      | $SUDO tee /etc/rc.d/bitcoind >/dev/null
    $SUDO chmod +x /etc/rc.d/bitcoind
    $SUDO rcctl enable bitcoind 2>/dev/null || true
    say "  bitcoind rc.d service installed + enabled (rcctl start bitcoind)"
  fi
fi

say "node provisioned. Persist it:  doas usbfadm  → save to the encrypted volume."
say "Attach BANKON (read-only Console / WaaS) to this node's :8332 for the sovereign stack."
