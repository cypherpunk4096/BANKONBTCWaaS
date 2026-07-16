#!/usr/bin/env bash
#
# test-dexy-regtest.sh — DEXY end-to-end on an isolated regtest node. Proves:
# /api/pairs serves chain-native SPINTRADE data from the regtest tip; the CEX→DEX
# projector and venue quotes work offline (DEXY_FIXTURES=1, zero network); a
# quote REFUSES to run without the user's BTC address and verifies a regtest
# destination; the reused HTLC leg accepts BTC into the watch-only swap wallet;
# and the non-custodial guard 400s any private material. No mainnet, no real funds.
#
set -uo pipefail
export PATH="/home/luvai/bitcoin-31.0/bin:$PATH"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEXY_DIR="$(dirname "$HERE")"
DD="${1:-${TMPDIR:-/tmp}/regtest-dexy}"
RCLI="bitcoin-cli -regtest -datadir=$DD"
API="http://127.0.0.1:18091"
SRV_PID=""
fail(){ echo "FAIL: $*"; cleanup; exit 1; }
cleanup(){
  [ -n "$SRV_PID" ] && kill "$SRV_PID" >/dev/null 2>&1 || true
  bitcoin-cli -regtest -datadir="$DD" stop >/dev/null 2>&1 || true
}
trap cleanup EXIT
jget(){ python3 -c "import sys,json;d=json.load(sys.stdin);print($1)"; }

rm -rf "$DD"; mkdir -p "$DD"
cat > "$DD/bitcoin.conf" <<EOF
regtest=1
server=1
fallbackfee=0.0002
[regtest]
rpcport=18557
EOF

echo "1) start isolated regtest node"
bitcoind -regtest -datadir="$DD" -daemon >/dev/null 2>&1
for i in $(seq 1 30); do $RCLI getblockchaininfo >/dev/null 2>&1 && break; sleep 1; done
$RCLI getblockchaininfo >/dev/null 2>&1 || fail "regtest node did not start"

echo "2) funded wallet + destination address (stand-in for the user's WaaS wallet)"
$RCLI createwallet dexy_test >/dev/null
DEST=$($RCLI -rpcwallet=dexy_test getnewaddress)
$RCLI generatetoaddress 101 "$DEST" >/dev/null
echo "   destination (user's own keys): $DEST"

echo "3) start DEXY on :18091 (fixtures on — zero network)"
export BITCOIN_RPC_URL="http://127.0.0.1:18557"
export BITCOIN_COOKIE="$DD/regtest/.cookie"
export BANKON_DEXY_PORT=18091
export DEXY_FIXTURES=1
( cd "$DEXY_DIR" && node server.mjs >"$DD/dexy.log" 2>&1 ) & SRV_PID=$!
for i in $(seq 1 20); do curl -s "$API/api/health" >/dev/null 2>&1 && break; sleep 0.5; done
curl -s "$API/api/health" | jget "d['ok']" | grep -q True || fail "dexy did not start: $(tail -3 "$DD/dexy.log")"

echo "4) /api/pairs — chain-native from the regtest tip (SPINTRADE-compatible)"
P=$(curl -s "$API/api/pairs")
echo "$P" | jget "d['ok']" | grep -q True || fail "/api/pairs not ok: $P"
echo "$P" | jget "d['asOfBlock']" | grep -q 101 || fail "pairs not at regtest tip"
echo "   pairs at block $(echo "$P" | jget "d['asOfBlock']") — no external feed, no fiat"

echo "5) CEX→DEX projection (fixtures)"
PR=$(curl -s "$API/api/dexy/project?moveUsd=1000000")
echo "$PR" | jget "d['ok']" | grep -q True || fail "project failed: $PR"
echo "$PR" | jget "len(d['projection']['schedule'])>0" | grep -q True || fail "empty schedule"
echo "   schedule: $(echo "$PR" | jget "len(d['projection']['schedule'])") tranche(s) across $(echo "$PR" | jget "d['projection']['days']") day(s)"

echo "6) quote REFUSES without the user's address; verifies a real destination"
CODE=$(curl -s -o /dev/null -w '%{http_code}' "$API/api/dexy/quote?chain=ETH&symbol=USDC&decimals=6&amount=5000000000")
[ "$CODE" = 400 ] || fail "quote without btcAddress must 400 (got $CODE)"
Q=$(curl -s "$API/api/dexy/quote?chain=ETH&symbol=USDC&contract=0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48&decimals=6&amount=5000000000&btcAddress=$DEST")
echo "$Q" | jget "d['destination']['valid']" | grep -q True || fail "regtest destination should validate: $Q"
echo "$Q" | jget "len(d['quotes'])>=1" | grep -q True || fail "no venue quotes: $Q"
echo "   destination valid, $(echo "$Q" | jget "len(d['quotes'])") venue quote(s) → user's address"

echo "7) custody verify endpoint"
CV=$(curl -s "$API/api/dexy/custody/verify?address=$DEST")
echo "$CV" | jget "d['valid']" | grep -q True || fail "custody verify failed: $CV"

echo "8) HTLC leg (reused swap.mjs) — accept BTC into the watch-only swap wallet"
PK1=$($RCLI -rpcwallet=dexy_test getaddressinfo "$($RCLI -rpcwallet=dexy_test getnewaddress)" | jget "d['pubkey']")
PK2=$($RCLI -rpcwallet=dexy_test getaddressinfo "$($RCLI -rpcwallet=dexy_test getnewaddress)" | jget "d['pubkey']")
HASH=$(python3 -c "import hashlib;print(hashlib.sha256(bytes.fromhex('11'*32)).hexdigest())")
H=$(curl -s -X POST "$API/api/swap/htlc/new" -H 'content-type: application/json' \
  -d "{\"claimPubkey\":\"$PK1\",\"refundPubkey\":\"$PK2\",\"hashHex\":\"$HASH\",\"locktime\":200}")
echo "$H" | jget "d['ok']" | grep -q True || fail "htlc/new failed: $H"
HADDR=$(echo "$H" | jget "d['address']")
$RCLI -rpcwallet=dexy_test sendtoaddress "$HADDR" 0.5 >/dev/null || fail "funding send failed"
$RCLI -rpcwallet=dexy_test generatetoaddress 1 "$DEST" >/dev/null
F=$(curl -s "$API/api/swap/htlc/funding?address=$HADDR")
echo "$F" | jget "d['funded']" | grep -q True || fail "swap not funded: $F"
echo "   HTLC $HADDR funded with $(echo "$F" | jget "d['amountBtc']") BTC (watch-only)"

echo "9) non-custodial guard — private material is REFUSED"
CODE=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$API/api/dexy/plan" \
  -H 'content-type: application/json' -d '{"privkey":"KxFC1jm..."}')
[ "$CODE" = 400 ] || fail "privkey body must 400 (got $CODE)"

echo
echo "PASS — DEXY e2e on isolated regtest: chain-native pairs, projection, sovereign quote gate, HTLC accept, non-custodial guard."
