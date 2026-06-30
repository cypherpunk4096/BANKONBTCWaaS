#!/usr/bin/env bash
# bankon-eth.sh — launch the BANKON.ETH WaaS (EVM twin of bankon-waas), STANDALONE.
#
# Third twin, in tandem with the BTC + ALGO stacks — own port, own folder:
#   BANKON BTC  WaaS :8088   ALGO :4444   ETH/EVM :4448   ← this
# Non-custodial: keys minted in the browser (ethers.js); the node tracks watch-only addresses only.
# EVM-generic — point ETH_RPC_URL at any EVM chain.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DIR="$HERE/bankon-eth"
[ -f "$HERE/bankon.env" ] && { set -a; . "$HERE/bankon.env"; set +a; }
export BANKON_ETH_PORT="${BANKON_ETH_PORT:-4448}"

command -v node >/dev/null || { echo "node.js required"; exit 1; }
cd "$DIR"
[ -d node_modules ] || { echo "installing deps (one-time)…"; npm install --no-audit --no-fund || exit 1; }
echo "BANKON.ETH WaaS → http://127.0.0.1:$BANKON_ETH_PORT   (EVM node: ${ETH_RPC_URL:-http://127.0.0.1:8545})"
exec node server.mjs
