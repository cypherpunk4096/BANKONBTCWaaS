#!/usr/bin/env bash
#
# test-anchor-regtest.sh — OP_RETURN canonical anchor roundtrip on an isolated regtest
# node. Proves: anchorHash writes SHA-256(data) to an OP_RETURN output and broadcasts;
# verifyAnchor retrieves the tx (txindex=1) and confirms the payload matches the data,
# and REJECTS tampered data. Exercises anchor.mjs end-to-end. No mainnet, no real funds.
#
set -uo pipefail
export PATH="/home/luvai/bitcoin-31.0/bin:$PATH"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DD="${1:-/tmp/claude-1000/-home-luvai/1e73dad7-096a-4221-ba3b-83a8f6bff7e9/scratchpad/regtest-anchor}"
RCLI="bitcoin-cli -regtest -datadir=$DD"
fail(){ echo "FAIL: $*"; cleanup; exit 1; }
cleanup(){ bitcoin-cli -regtest -datadir="$DD" stop >/dev/null 2>&1 || true; }
trap cleanup EXIT

rm -rf "$DD"; mkdir -p "$DD"
cat > "$DD/bitcoin.conf" <<EOF
regtest=1
server=1
txindex=1
fallbackfee=0.0002
[regtest]
rpcport=18556
EOF

echo "1) start regtest node (txindex=1 — verifyAnchor needs getrawtransaction)"
bitcoind -regtest -datadir="$DD" -daemon >/dev/null 2>&1
for i in $(seq 1 30); do $RCLI getblockchaininfo >/dev/null 2>&1 && break; sleep 1; done
$RCLI getblockchaininfo >/dev/null 2>&1 || fail "regtest node did not start"

echo "2) anchor wallet + 101 blocks (spendable coinbase to pay the anchor fee)"
$RCLI createwallet bankon_anchor >/dev/null
AADDR=$($RCLI -rpcwallet=bankon_anchor getnewaddress)
$RCLI generatetoaddress 101 "$AADDR" >/dev/null
echo "   anchor wallet balance: $($RCLI -rpcwallet=bankon_anchor getbalance) BTC"

# point anchor.mjs (via rpc.mjs) at the regtest node
export BITCOIN_RPC_URL="http://127.0.0.1:18556"
export BITCOIN_COOKIE="$DD/regtest/.cookie"
export BANKON_ANCHOR_WALLET="bankon_anchor"

DATA="BANKON anchor proof — block-timestamped at $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "3) anchor data via anchor.mjs: \"$DATA\""
OUT=$(node "$HERE/test-anchor-helper.mjs" anchor "$DATA") || fail "anchorHash failed: $OUT"
TXID=$(echo "$OUT" | python3 -c "import sys,json;print(json.load(sys.stdin)['txid'])")
HASH=$(echo "$OUT" | python3 -c "import sys,json;print(json.load(sys.stdin)['hash'])")
echo "   txid=$TXID"
echo "   OP_RETURN hash=$HASH"

echo "4) confirm the OP_RETURN output is on-chain (raw decode)"
$RCLI generatetoaddress 1 "$AADDR" >/dev/null
ASM=$($RCLI getrawtransaction "$TXID" true | python3 -c "
import sys,json
tx=json.load(sys.stdin)
for v in tx['vout']:
    a=v['scriptPubKey'].get('asm','')
    if a.startswith('OP_RETURN'): print(a); break
")
echo "   on-chain script: $ASM"
echo "$ASM" | grep -q "$HASH" || fail "OP_RETURN payload not found on-chain"

echo "5) verifyAnchor with the CORRECT data → must match"
V1=$(node "$HERE/test-anchor-helper.mjs" verify "$TXID" "$DATA") || fail "verify call failed"
M1=$(echo "$V1" | python3 -c "import sys,json;print(json.load(sys.stdin)['match'])")
echo "   match=$M1  conf=$(echo "$V1" | python3 -c "import sys,json;print(json.load(sys.stdin)['confirmations'])")"
[ "$M1" = "True" ] || fail "verify of correct data did not match: $V1"

echo "6) verifyAnchor with TAMPERED data → must NOT match"
V2=$(node "$HERE/test-anchor-helper.mjs" verify "$TXID" "${DATA} (tampered)") || fail "verify call failed"
M2=$(echo "$V2" | python3 -c "import sys,json;print(json.load(sys.stdin)['match'])")
echo "   match=$M2 (expected False)"
[ "$M2" = "False" ] || fail "tampered data unexpectedly matched: $V2"

echo
echo "✓ ANCHOR PASS — OP_RETURN canonical anchor verified: anchor → on-chain → verify(true) → tamper→verify(false)"
