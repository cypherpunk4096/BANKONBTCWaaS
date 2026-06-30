#!/usr/bin/env bash
# bankon-algo.sh — launch the BANKON ALGO WaaS (Algorand twin of bankon-waas), STANDALONE.
#
# Twin of bankon-waas (Bitcoin). Runs in tandem with the BTC stack — different ports, own folder:
#   BANKON BTC  WaaS  :8088   (Bitcoin Core :8332)
#   BANKON ALGO WaaS  :4444   (Algorand algod :8080)        ← this
# Non-custodial: keys are minted in the browser (algosdk); the node tracks watch-only addresses only.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DIR="$HERE/bankon-algo"
# shared operator config (optional): ALGOD_URL / ALGOD_TOKEN / BANKON_ALGO_PORT
[ -f "$HERE/bankon.env" ] && { set -a; . "$HERE/bankon.env"; set +a; }
export BANKON_ALGO_PORT="${BANKON_ALGO_PORT:-4444}"

command -v node >/dev/null || { echo "node.js required"; exit 1; }
cd "$DIR"
[ -d node_modules ] || { echo "installing deps (one-time)…"; npm install --no-audit --no-fund || exit 1; }
echo "BANKON ALGO WaaS → http://127.0.0.1:$BANKON_ALGO_PORT   (algod: ${ALGOD_URL:-auto from ~/.algorand})"
exec node server.mjs
