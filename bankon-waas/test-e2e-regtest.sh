#!/usr/bin/env bash
#
# test-e2e-regtest.sh — full non-custodial WaaS loop on an isolated regtest node.
# Proves: keygen → watch-only register → fund → build PSBT → CLIENT-SIGN → broadcast
# → confirm, end to end, with real (regtest) coins. Independent of the mainnet node.
#
set -uo pipefail
export PATH="/home/luvai/bitcoin-31.0/bin:$PATH"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DD="${1:-/tmp/claude-1000/-home-luvai/1e73dad7-096a-4221-ba3b-83a8f6bff7e9/scratchpad/regtest}"
RCLI="bitcoin-cli -regtest -datadir=$DD"
fail(){ echo "FAIL: $*"; cleanup; exit 1; }
cleanup(){ bitcoin-cli -regtest -datadir="$DD" stop >/dev/null 2>&1 || true; }
trap cleanup EXIT

rm -rf "$DD"; mkdir -p "$DD"
cat > "$DD/bitcoin.conf" <<EOF
regtest=1
server=1
fallbackfee=0.0002
[regtest]
rpcport=18555
EOF

echo "1) start regtest node"
bitcoind -regtest -datadir="$DD" -daemon >/dev/null 2>&1
for i in $(seq 1 30); do $RCLI getblockchaininfo >/dev/null 2>&1 && break; sleep 1; done
$RCLI getblockchaininfo >/dev/null 2>&1 || fail "regtest node did not start"

echo "2) miner wallet + 101 blocks (spendable coinbase)"
$RCLI createwallet miner >/dev/null
MADDR=$($RCLI -rpcwallet=miner getnewaddress)
$RCLI generatetoaddress 101 "$MADDR" >/dev/null
echo "   miner balance: $($RCLI -rpcwallet=miner getbalance) BTC"

echo "3) BANKON keygen (client-side)"
KG=$(node "$HERE/e2e-helper.mjs" keygen-regtest native-segwit)
MNEMONIC=$(echo "$KG" | python3 -c "import sys,json;print(json.load(sys.stdin)['mnemonic'])")
EXT=$(echo "$KG" | python3 -c "import sys,json;print(json.load(sys.stdin)['external'])")
INT=$(echo "$KG" | python3 -c "import sys,json;print(json.load(sys.stdin)['internal'])")
echo "   minted wallet (mnemonic kept client-side only)"

echo "4) register watch-only on the node"
$RCLI createwallet bankon_e2e true true "" false true true >/dev/null
EXTC=$($RCLI getdescriptorinfo "$EXT" | python3 -c "import sys,json;print(json.load(sys.stdin)['descriptor'])")
INTC=$($RCLI getdescriptorinfo "$INT" | python3 -c "import sys,json;print(json.load(sys.stdin)['descriptor'])")
$RCLI -rpcwallet=bankon_e2e importdescriptors "[{\"desc\":\"$EXTC\",\"timestamp\":\"now\",\"active\":true,\"internal\":false,\"range\":[0,20]},{\"desc\":\"$INTC\",\"timestamp\":\"now\",\"active\":true,\"internal\":true,\"range\":[0,20]}]" >/dev/null
WADDR=$($RCLI -rpcwallet=bankon_e2e getnewaddress)
echo "   watch-only receive addr: $WADDR"
[ "$($RCLI -rpcwallet=bankon_e2e getwalletinfo | python3 -c 'import sys,json;print(json.load(sys.stdin)["private_keys_enabled"])')" = "False" ] || fail "wallet is not watch-only!"

echo "5) fund it: miner sends 1 BTC, mine 1 block"
$RCLI -rpcwallet=miner sendtoaddress "$WADDR" 1.0 >/dev/null
$RCLI generatetoaddress 1 "$MADDR" >/dev/null
BAL=$($RCLI -rpcwallet=bankon_e2e getbalance)
echo "   bankon_e2e balance: $BAL BTC"
[ "$BAL" = "1.00000000" ] || fail "expected 1.0 BTC, got $BAL"

echo "6) build UNSIGNED PSBT (send 0.4 BTC back to miner, fee_rate 10 sat/vB)"
DEST=$($RCLI -rpcwallet=miner getnewaddress)
PSBT=$($RCLI -rpcwallet=bankon_e2e walletcreatefundedpsbt "[]" "[{\"$DEST\":0.4}]" 0 "{\"fee_rate\":10}" | python3 -c "import sys,json;print(json.load(sys.stdin)['psbt'])")
echo "   unsigned PSBT built (${#PSBT} chars)"

echo "7) CLIENT-SIGN locally with the mnemonic (key never touched the node)"
SIGNED=$(python3 -c "import json,sys;print(json.dumps({'mnemonic':sys.argv[1],'type':'native-segwit','psbt':sys.argv[2]}))" "$MNEMONIC" "$PSBT" | node "$HERE/e2e-helper.mjs" sign)
echo "   signed tx hex (${#SIGNED} chars)"

echo "8) broadcast + confirm"
TXID=$($RCLI sendrawtransaction "$SIGNED") || fail "broadcast rejected"
$RCLI generatetoaddress 1 "$MADDR" >/dev/null
CONF=$($RCLI -rpcwallet=bankon_e2e gettransaction "$TXID" | python3 -c "import sys,json;print(json.load(sys.stdin)['confirmations'])")
echo "   txid $TXID confirmed ($CONF conf)"
[ "$CONF" -ge 1 ] || fail "tx not confirmed"

NEWBAL=$($RCLI -rpcwallet=bankon_e2e getbalance)
echo "   bankon_e2e balance after send: $NEWBAL BTC (was 1.0, sent 0.4 + fee)"
awk -v b="$NEWBAL" 'BEGIN{exit !(b>0.59 && b<0.6)}' || fail "post-send balance unexpected: $NEWBAL"

echo
echo "✓ E2E PASS — non-custodial loop verified: keygen → watch-only → fund → PSBT → client-sign → broadcast → confirm"
