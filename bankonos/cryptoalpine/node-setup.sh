#!/bin/sh
# node-setup.sh (cryptoAlpine) — provision the persistent Bitcoin-Core + multi-crypto NODE on a
# bankonAlpine base (Alpine Linux). Dedicated user, txindex/loopback-RPC conf, OpenRC service.
# Persist on a diskless Alpine with `lbu commit` (Alpine's local-backup); on a sys-install it's durable.
# Idempotent.
#
#   COINS="bitcoin" doas sh node-setup.sh      # or: sh node-setup.sh   (default: bitcoin)
set -eu
# 3-level logging (shared lib if reachable; inline fallback keeps the script standalone)
_ld="$(cd "$(dirname "$0")" 2>/dev/null && pwd || echo .)"
for _p in "$_ld/lib/log.sh" "$_ld/../lib/log.sh" "$_ld/../../lib/log.sh"; do [ -r "$_p" ] && { . "$_p"; break; }; done
if ! command -v log_info >/dev/null 2>&1; then
  : "${BANKON_LOG:=1}"
  log_info(){ [ "${BANKON_LOG:-1}" -ge 1 ] && printf "\033[38;5;208m▸ %s\033[0m\n" "$*"; return 0; }
  log_warn(){ printf "WARN: %s\n" "$*" >&2; }
  die(){ printf "ERROR: %s\n" "$*" >&2; exit 1; }
  log_debug(){ [ "${BANKON_LOG:-1}" -ge 2 ] && printf "  · %s\n" "$*" >&2; return 0; }
  log_run(){ [ "${DRY:-0}" = 1 ] && { printf "   [dry-run] %s\n" "$*"; return 0; }; eval "$@"; }
fi
say(){ log_info "$@"; }; warn(){ log_warn "$@"; }; run(){ log_run "$@"; }
COINS="${COINS:-bitcoin}"
BTC_HOME="${BTC_HOME:-/var/lib/bitcoind}"
command -v apk >/dev/null || { echo "cryptoAlpine node runs on Alpine (bankonAlpine base)"; exit 1; }
SUDO=""; [ "$(id -u)" != 0 ] && { command -v doas >/dev/null && SUDO=doas || { command -v sudo >/dev/null && SUDO=sudo; }; }

# Alpine package per coin ('-' = not packaged → note only)
pkgfor() { case "$1" in
  bitcoin) echo bitcoin ;;
  monero)  echo monero ;;
  *) echo "-" ;;
esac; }

say "cryptoAlpine node — coins: $COINS"
$SUDO apk update >/dev/null 2>&1 || true

for c in $COINS; do
  p="$(pkgfor "$c")"
  [ "$p" = "-" ] && { warn "$c: no Alpine package — build from source or skip"; continue; }
  say "install $c ($p)…"
  $SUDO apk add --no-cache "$p" 2>/dev/null || warn "$c: apk add failed (community repo enabled? network for first provision?)"
done

if echo " $COINS " | grep -q " bitcoin "; then
  say "configure Bitcoin Core → $BTC_HOME (txindex, loopback RPC, dedicated user)"
  $SUDO addgroup -S bitcoin 2>/dev/null || true
  $SUDO adduser -S -D -H -h "$BTC_HOME" -s /sbin/nologin -G bitcoin bitcoin 2>/dev/null || true
  $SUDO mkdir -p "$BTC_HOME"
  if [ ! -f "$BTC_HOME/bitcoin.conf" ]; then
    printf '%s\n' 'server=1' 'daemon=0' 'txindex=1' 'listen=1' \
      'rpcbind=127.0.0.1' 'rpcallowip=127.0.0.1' 'dbcache=300' \
      '# cryptoAlpine: loopback RPC only; front with BANKON Console for read-only access' \
      | $SUDO tee "$BTC_HOME/bitcoin.conf" >/dev/null
  fi
  $SUDO chown -R bitcoin:bitcoin "$BTC_HOME"
  # OpenRC service
  if [ ! -f /etc/init.d/bitcoind ]; then
    $SUDO tee /etc/init.d/bitcoind >/dev/null <<EOF
#!/sbin/openrc-run
name="bitcoind"
description="Bitcoin Core daemon (cryptoAlpine node)"
command="/usr/bin/bitcoind"
command_args="-datadir=$BTC_HOME -conf=$BTC_HOME/bitcoin.conf"
command_user="bitcoin:bitcoin"
command_background=true
pidfile="/run/bitcoind.pid"
output_log="$BTC_HOME/debug-svc.log"
error_log="$BTC_HOME/debug-svc.log"
depend() { need net; after firewall; }
EOF
    $SUDO chmod +x /etc/init.d/bitcoind
    $SUDO rc-update add bitcoind default 2>/dev/null || true
    say "  bitcoind OpenRC service installed + enabled (rc-service bitcoind start)"
  fi
fi

say "node provisioned."
say "  persist (diskless Alpine):  doas lbu commit -d      (saves to the boot media)"
say "  start:  doas rc-service bitcoind start"
say "  attach BANKON read-only Console/WaaS to :8332 for the sovereign stack."
