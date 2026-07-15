#!/usr/bin/env bash
# ₿ANKON ₿TC WaaS — simple foreground launcher.
# Runs in THIS terminal so all logs stream here. Ctrl-C to stop.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

# ── sane defaults (override with flags, or pre-set the env var) ──
: "${BANKON_BTC_BIN:=$HOME/bitcoin-31.0/bin}"
: "${BANKON_BTC_DATADIR:=$HOME/.bitcoin}"
: "${BANKON_WAAS_URL:=http://127.0.0.1:8088}"
: "${BANKON_PEER_TARGET:=12}"
GPU=0

usage() {
  cat <<EOF
₿ANKON ₿TC WaaS — launcher

Usage: ./bankon.sh [options] [-- extra Qt args]
  --btc-bin DIR     ₿itcoin Core bin dir   (default: $BANKON_BTC_BIN)
  --datadir DIR     ₿itcoin data dir        (default: $BANKON_BTC_DATADIR)
  --waas-url URL    WaaS backend URL         (default: $BANKON_WAAS_URL)
  --peers N         Peer target              (default: $BANKON_PEER_TARGET)
  --gpu             Use GPU OpenGL (default: software rendering — safer on this host)
  -h, --help        Show this help

Logs stream to this terminal. Ctrl-C to stop.
EOF
}

ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --btc-bin)  BANKON_BTC_BIN="$2"; shift 2;;
    --datadir)  BANKON_BTC_DATADIR="$2"; shift 2;;
    --waas-url) BANKON_WAAS_URL="$2"; shift 2;;
    --peers)    BANKON_PEER_TARGET="$2"; shift 2;;
    --gpu)      GPU=1; shift;;
    -h|--help)  usage; exit 0;;
    --)         shift; ARGS+=("$@"); break;;
    *)          ARGS+=("$1"); shift;;          # pass anything else through to Qt
  esac
done

export BANKON_BTC_BIN BANKON_BTC_DATADIR BANKON_WAAS_URL BANKON_PEER_TARGET
# Software rendering by default — the node host black-screens under GL.
# (Per the Qt globe guidance: set ONLY QT_OPENGL=software; other flags can hang.)
[[ "$GPU" -eq 0 ]] && export QT_OPENGL=software

echo "▶ ₿ANKON ₿TC WaaS"
echo "  btc-bin : $BANKON_BTC_BIN"
echo "  datadir : $BANKON_BTC_DATADIR"
echo "  waas    : $BANKON_WAAS_URL"
echo "  peers   : $BANKON_PEER_TARGET"
echo "  render  : $([[ $GPU -eq 1 ]] && echo GPU || echo software)"
[[ -x "$BANKON_BTC_BIN/bitcoind" ]] || echo "  ⚠ bitcoind not found at $BANKON_BTC_BIN — pass --btc-bin DIR"
echo "  logs → this terminal · Ctrl-C to stop"
echo

# -u = unbuffered, so logs appear live in the terminal.
exec python3 -u bankon_qt.py ${ARGS[@]+"${ARGS[@]}"}
