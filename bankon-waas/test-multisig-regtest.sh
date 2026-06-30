#!/usr/bin/env bash
# test-multisig-regtest.sh — register & fund a 2-of-3 watch-only multisig on regtest.
# Proves BANKON's multisig descriptor build + watch-only import + receive works.
set -uo pipefail
export PATH="/home/luvai/bitcoin-31.0/bin:$PATH"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DD="${1:-/tmp/claude-1000/-home-luvai/1e73dad7-096a-4221-ba3b-83a8f6bff7e9/scratchpad/regtest-ms}"
RCLI="bitcoin-cli -regtest -datadir=$DD"
fail(){ echo "FAIL: $*"; bitcoin-cli -regtest -datadir="$DD" stop >/dev/null 2>&1 || true; exit 1; }
trap 'bitcoin-cli -regtest -datadir="$DD" stop >/dev/null 2>&1 || true' EXIT
j(){ python3 -c "import sys,json;print(json.load(sys.stdin)$1)"; }

rm -rf "$DD"; mkdir -p "$DD"
printf 'regtest=1\nserver=1\nfallbackfee=0.0002\n[regtest]\nrpcport=18556\n' > "$DD/bitcoin.conf"
echo "1) regtest node + miner"
bitcoind -regtest -datadir="$DD" -daemon >/dev/null 2>&1
for i in $(seq 1 30); do $RCLI getblockchaininfo >/dev/null 2>&1 && break; sleep 1; done
$RCLI createwallet miner >/dev/null; MADDR=$($RCLI -rpcwallet=miner getnewaddress)
$RCLI generatetoaddress 101 "$MADDR" >/dev/null

echo "2) generate 3 cosigners (client-side)"
K1=$(node "$HERE/e2e-helper.mjs" keygen-regtest-ms); K2=$(node "$HERE/e2e-helper.mjs" keygen-regtest-ms); K3=$(node "$HERE/e2e-helper.mjs" keygen-regtest-ms)
desc_branch(){ local b="$1"; for K in "$K1" "$K2" "$K3"; do
    printf '[%s/%s]%s/%s/*,' "$(echo "$K"|j "['fingerprint']")" "$(echo "$K"|j "['path']")" "$(echo "$K"|j "['xpub']")" "$b"; done | sed 's/,$//'; }
EXT="wsh(sortedmulti(2,$(desc_branch 0)))"
INT="wsh(sortedmulti(2,$(desc_branch 1)))"

echo "3) register 2-of-3 watch-only multisig"
EXTC=$($RCLI getdescriptorinfo "$EXT" | j "['descriptor']") || fail "external descriptor invalid"
INTC=$($RCLI getdescriptorinfo "$INT" | j "['descriptor']") || fail "internal descriptor invalid"
$RCLI createwallet bankon_ms true true "" false true true >/dev/null
$RCLI -rpcwallet=bankon_ms importdescriptors "[{\"desc\":\"$EXTC\",\"timestamp\":\"now\",\"active\":true,\"internal\":false,\"range\":[0,10]},{\"desc\":\"$INTC\",\"timestamp\":\"now\",\"active\":true,\"internal\":true,\"range\":[0,10]}]" >/dev/null
ADDR=$($RCLI -rpcwallet=bankon_ms getnewaddress)
echo "   2-of-3 receive address: $ADDR"
case "$ADDR" in bcrt1q*) ;; *) fail "expected P2WSH bcrt1q address, got $ADDR";; esac

echo "4) fund it + confirm balance"
$RCLI -rpcwallet=miner sendtoaddress "$ADDR" 2.5 >/dev/null
$RCLI generatetoaddress 1 "$MADDR" >/dev/null
BAL=$($RCLI -rpcwallet=bankon_ms getbalance)
[ "$BAL" = "2.50000000" ] || fail "expected 2.5 BTC, got $BAL"
echo "   multisig balance: $BAL BTC"
echo
echo "✓ MULTISIG PASS — 2-of-3 watch-only multisig built, imported, received funds"
echo "  (spending: each of 2 cosigners signs the PSBT via the offline client, then combinepsbt → broadcast)"
