#!/usr/bin/env bash
#
# test-prune-regtest.sh — prove pruning is LOSSLESS for validation accuracy, across prune
# sizes, FROM NODE CREATION. Creates independent regtest nodes each born at a different
# prune= size (and one archival), mines the same chain into each, then compares the ONE
# measure that captures validation accuracy: the UTXO-set hash (gettxoutsetinfo).
#
# The measure — WHY the UTXO hash is the right yardstick:
#   A Bitcoin node's "accuracy" is the correctness of its UTXO set — the exact set of
#   spendable coins after fully validating every block. gettxoutsetinfo returns a
#   cryptographic hash (muhash / hash_serialized_3) over that whole set. If two nodes report
#   the SAME hash at the same height, their validation results are byte-for-byte identical —
#   provably. Pruning discards old *block files* but never the chainstate (the UTXO set), and
#   every block is fully verified before its file is discarded. So a 1 GB-pruned node and an
#   archival node MUST produce the same UTXO hash. This test asserts exactly that.
#
# Usage:  ./test-prune-regtest.sh            # runs all tiers, prints PASS/FAIL
#         BLOCKS=500 ./test-prune-regtest.sh # deeper chain
set -uo pipefail
export PATH="${BANKON_BTC_BIN:-$HOME/bitcoin-31.0/bin}:$PATH"
command -v bitcoind >/dev/null || { echo "bitcoind not found on PATH"; exit 1; }

BLOCKS="${BLOCKS:-300}"
TIERS=("minimal:550" "onegb:1024" "default:2048" "generous:10000" "archival:0")   # name:pruneMiB (0=off+txindex)
ROOT="$(mktemp -d /tmp/bankon-prune-test.XXXXXX)"
PORT=19700; RPCPORT=19800
declare -A UTXO TIP HEIGHT PRUNED
FAIL=0

cleanup() {
  for t in "${TIERS[@]}"; do
    local dd="$ROOT/${t%%:*}"
    bitcoin-cli -regtest -datadir="$dd" stop >/dev/null 2>&1 || true
  done
  sleep 1; rm -rf "$ROOT"
}
trap cleanup EXIT

wait_rpc() { local dd="$1"; for _ in $(seq 1 40); do
  bitcoin-cli -regtest -datadir="$dd" getblockchaininfo >/dev/null 2>&1 && return 0; sleep 0.25; done; return 1; }

echo "=================================================================="
echo " BANKON prune accuracy test — $BLOCKS regtest blocks per node, from creation"
echo " measure: UTXO-set hash (gettxoutsetinfo) — identical hash ⇒ identical validation"
echo "=================================================================="

# ONE shared chain: the archival node mines it and listens; every pruned node — born at its own
# prune size — CONNECTS and validates the identical chain. Same chain in, UTXO hash out is the proof.
ARCH_P2P=$PORT
i=0
for t in "${TIERS[@]}"; do
  name="${t%%:*}"; prune="${t##*:}"; dd="$ROOT/$name"; mkdir -p "$dd"
  {
    echo "regtest=1"; echo "server=1"; echo "daemon=1"; echo "fallbackfee=0.0001"
    echo "[regtest]"; echo "rpcport=$((RPCPORT+i))"
    if [ "$name" = "archival" ]; then
      echo "port=$ARCH_P2P"; echo "listen=1"; echo "txindex=1"       # the source of the chain
    else
      echo "listen=0"; echo "connect=127.0.0.1:$ARCH_P2P"; echo "prune=$prune"   # validate archival's chain
    fi
  } > "$dd/bitcoin.conf"
  bitcoind -regtest -datadir="$dd" >/dev/null 2>&1
  wait_rpc "$dd" || { echo " ✗ $name: node did not start"; FAIL=1; }
  i=$((i+1))
done

# archival mines the shared chain; the pruned nodes sync it over P2P
adir="$ROOT/archival"
bitcoin-cli -regtest -datadir="$adir" -named createwallet wallet_name=t descriptors=true >/dev/null 2>&1
addr=$(bitcoin-cli -regtest -datadir="$adir" -rpcwallet=t getnewaddress "" bech32m)
bitcoin-cli -regtest -datadir="$adir" -rpcwallet=t generatetoaddress "$BLOCKS" "$addr" >/dev/null

measure() { local name="$1" dd="$ROOT/$1"
  local ci; ci=$(bitcoin-cli -regtest -datadir="$dd" getblockchaininfo 2>/dev/null)
  HEIGHT[$name]=$(echo "$ci" | grep -oE '"blocks": *[0-9]+' | grep -oE '[0-9]+')
  PRUNED[$name]=$(echo "$ci" | grep -oE '"pruned": *(true|false)' | grep -oE '(true|false)')
  TIP[$name]=$(bitcoin-cli -regtest -datadir="$dd" getbestblockhash 2>/dev/null)
  UTXO[$name]=$(bitcoin-cli -regtest -datadir="$dd" gettxoutsetinfo muhash 2>/dev/null | grep -oE '"muhash": *"[0-9a-f]+"' | grep -oE '[0-9a-f]{64}')
}
# wait for every pruned node to reach the archival tip, then measure
for t in "${TIERS[@]}"; do
  name="${t%%:*}"; prune="${t##*:}"; dd="$ROOT/$name"
  for _ in $(seq 1 60); do
    h=$(bitcoin-cli -regtest -datadir="$dd" getblockcount 2>/dev/null || echo 0)
    [ "${h:-0}" -ge "$BLOCKS" ] && break; sleep 0.5
  done
  measure "$name"
  printf " %-9s prune=%-6s height=%s pruned=%s\n            tip=%s\n            utxo(muhash)=%s\n" \
    "$name" "$prune" "${HEIGHT[$name]:-?}" "${PRUNED[$name]:-?}" "${TIP[$name]:-?}" "${UTXO[$name]:0:32}…"
done

echo "------------------------------------------------------------------"
# reference = archival node
ref="archival"; rtip="${TIP[$ref]:-}"; rutxo="${UTXO[$ref]:-}"
[ -z "$rtip" ] && { echo " ✗ archival reference node failed — cannot compare"; exit 1; }
for t in "${TIERS[@]}"; do
  name="${t%%:*}"; prune="${t##*:}"
  [ "$name" = "$ref" ] && continue
  if [ "${TIP[$name]}" = "$rtip" ] && [ -n "${UTXO[$name]}" ] && [ "${UTXO[$name]}" = "$rutxo" ]; then
    echo " ✓ $name (prune=$prune): tip AND UTXO hash MATCH archival — validation identical, zero accuracy loss"
  else
    echo " ✗ $name (prune=$prune): DIVERGED  tip=${TIP[$name]}  utxo=${UTXO[$name]:0:16}… (expected $rutxo)"; FAIL=1
  fi
done
# the pruned tiers must actually report pruned=true (born pruned)
for t in minimal:550 onegb:1024 default:2048 generous:10000; do
  name="${t%%:*}"; [ "${PRUNED[$name]}" = "true" ] || { echo " ✗ $name did not report pruned=true"; FAIL=1; }
done

echo "=================================================================="
if [ "$FAIL" = 0 ]; then
  echo " PASS — every prune size (1 GB minimal … 10 GB generous) validated to the SAME"
  echo "        UTXO set as the archival node. Pruning changes storage, not accuracy."
else
  echo " FAIL — see divergences above"
fi
exit $FAIL
