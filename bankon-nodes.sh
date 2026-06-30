#!/usr/bin/env bash
#
# bankon-nodes.sh — BANKON multi-node manager.
#
# Runs TWO Bitcoin Core instances side by side:
#   • full    — existing archival node (txindex), datadir ~/.bitcoin
#               P2P 8333 / RPC 8332. "FULLchain as a Service" (explorer/lookup).
#   • pruned  — new lean node for WaaS, prune=2048 (2 GB), NO txindex.
#               P2P 8334 / RPC 8342. Security is identical (full validation).
#
# The pruned datadir lives on the external drive (only filesystem with room).
#
# Usage:
#   ./bankon-nodes.sh init-pruned         # create the pruned node's config
#   ./bankon-nodes.sh start  pruned|full
#   ./bankon-nodes.sh stop   pruned|full
#   ./bankon-nodes.sh status              # both nodes side by side
#
set -uo pipefail
export PATH="/home/luvai/bitcoin-31.0/bin:$PATH"

FULL_DATADIR="${BANKON_FULL_DATADIR:-$HOME/.bitcoin}"
PRUNED_DATADIR="${BANKON_PRUNED_DATADIR:-/media/luvai/2c0ab2e0-1e83-4721-bde5-57afe1bcd4e2/home/tank/.bitcoin-pruned}"
PRUNE_MIB="${BANKON_PRUNE_MIB:-2048}"
# --- resource policy: the pruned WaaS node must PLAY NICE with the user's archival Core ---
PRUNED_DBCACHE="${BANKON_PRUNED_DBCACHE:-300}"   # pruned UTXO cache (MiB) — small; shares RAM with the full node
PRUNED_PAR="${BANKON_PRUNED_PAR:-1}"             # script-verify threads — 1 = leave the cores for the full node
PRUNED_MAXCONN="${BANKON_PRUNED_MAXCONN:-20}"    # modest peer set
PRUNED_NICE="${BANKON_PRUNED_NICE:-15}"          # CPU priority (higher = nicer/lower) — full node wins
PRUNED_IONICE="${BANKON_PRUNED_IONICE:-3}"       # I/O class 3 = idle — full node's disk I/O wins
# --- full archival node dbcache (the user's Core) — a BANKON-managed variable, see `set-dbcache` ---
FULL_DBCACHE="${BANKON_BTC_DBCACHE:-2000}"       # MiB; 7.6GB RAM here → keep ≤4000 with the pruned node off
PRUNED_P2P=8334
PRUNED_RPC=8342

cli_full()   { bitcoin-cli -datadir="$FULL_DATADIR" "$@"; }
cli_pruned() { bitcoin-cli -datadir="$PRUNED_DATADIR" -rpcport=$PRUNED_RPC "$@"; }

write_pruned_conf() {
  mkdir -p "$PRUNED_DATADIR"
  cat > "$PRUNED_DATADIR/bitcoin.conf" <<EOF
# BANKON pruned node — lean WaaS backend. Full validation; prune old blocks.
# Security is identical to an archival node (see PRUNING.md).
prune=$PRUNE_MIB
server=1
daemon=1
listen=1

# Distinct ports so it coexists with the full archival node.
port=$PRUNED_P2P
rpcport=$PRUNED_RPC
rpcbind=127.0.0.1
rpcallowip=127.0.0.1

# Play-nice resource limits — yield CPU/RAM/peers to the full archival node.
dbcache=$PRUNED_DBCACHE
par=$PRUNED_PAR
maxconnections=$PRUNED_MAXCONN
maxmempool=100

# Seed peers (hostnames, not stale IPs).
dns=1
addnode=seed.bitcoin.sipa.be
addnode=dnsseed.bluematt.me
addnode=seed.bitcoin.sprovoost.nl
addnode=dnsseed.emzy.de
EOF
  echo "Wrote $PRUNED_DATADIR/bitcoin.conf (prune=$PRUNE_MIB, RPC $PRUNED_RPC, P2P $PRUNED_P2P)"
}

node_line() { # label datadir cli...
  local label="$1"; shift; local dd="$1"; shift
  if "$@" getblockchaininfo >/dev/null 2>&1; then
    local info; info="$("$@" getblockchaininfo 2>/dev/null)"
    local b h p pr conns
    b=$(echo "$info"  | grep -oE '"blocks": *[0-9]+' | grep -oE '[0-9]+')
    h=$(echo "$info"  | grep -oE '"headers": *[0-9]+' | grep -oE '[0-9]+')
    p=$(echo "$info"  | grep -oE '"verificationprogress": *[0-9.]+' | grep -oE '[0-9.]+')
    pr=$(echo "$info" | grep -oE '"pruned": *(true|false)' | grep -oE 'true|false')
    conns=$("$@" getconnectioncount 2>/dev/null)
    local pct; pct=$(awk -v v="${p:-0}" 'BEGIN{printf "%.2f", v*100}')
    printf "  %-7s RUNNING  blocks=%-8s headers=%-8s %s%%  peers=%-3s pruned=%s\n" "$label" "${b:-?}" "${h:-?}" "$pct" "${conns:-?}" "${pr:-?}"
  elif "$@" getconnectioncount >/dev/null 2>&1; then
    printf "  %-7s WARMING UP (RPC up, chain loading)\n" "$label"
  else
    printf "  %-7s stopped / not responding\n" "$label"
  fi
  local sz; sz=$(du -sh "$dd/blocks" "$dd/chainstate" 2>/dev/null | awk '{s=s" "$1"/"$2} END{print s}')
  [ -n "$sz" ] && printf "          disk:%s\n" "$sz"
}

case "${1:-status}" in
  init-pruned) write_pruned_conf ;;
  start)
    case "${2:-}" in
      pruned) write_pruned_conf   # regenerate so play-nice limits (par/maxconn/dbcache) always apply
              echo "Starting pruned node — nice -n$PRUNED_NICE · ionice -c$PRUNED_IONICE · par=$PRUNED_PAR (yields CPU/disk to the full node)…"
              nice -n "$PRUNED_NICE" ionice -c "$PRUNED_IONICE" bitcoind -datadir="$PRUNED_DATADIR" ;;
      full)   echo "Starting full node (dbcache=$FULL_DBCACHE)…"; bitcoind -datadir="$FULL_DATADIR" ;;
      *) echo "usage: $0 start pruned|full"; exit 1 ;;
    esac ;;
  set-dbcache)   # BANKON-managed dbcache variable → writes it into the node's bitcoin.conf
    val="${2:?usage: $0 set-dbcache <MiB> [full|pruned]}"; which="${3:-full}"
    conf=$([ "$which" = pruned ] && echo "$PRUNED_DATADIR/bitcoin.conf" || echo "$FULL_DATADIR/bitcoin.conf")
    if grep -q '^dbcache=' "$conf" 2>/dev/null; then sed -i "s/^dbcache=.*/dbcache=$val/" "$conf"
    else echo "dbcache=$val" >> "$conf"; fi
    echo "Set dbcache=$val MiB in $conf — restart the $which node to apply." ;;
  optimize)   # auto-tune the conf to THIS hardware (cores + RAM). The BANKON optimized default.
    which="${2:-full}"
    conf=$([ "$which" = pruned ] && echo "$PRUNED_DATADIR/bitcoin.conf" || echo "$FULL_DATADIR/bitcoin.conf")
    cores=$(nproc); ram=$(free -m | awk 'NR==2{print $2}')
    # set key=val in the conf; if appending, guard against a missing trailing newline (no concatenation)
    set_kv() { local k="$1" v="$2"; if grep -q "^$k=" "$conf" 2>/dev/null; then sed -i "s/^$k=.*/$k=$v/" "$conf";
      else [ -s "$conf" ] && [ -n "$(tail -c1 "$conf" 2>/dev/null)" ] && echo >> "$conf"; echo "$k=$v" >> "$conf"; fi; }
    if [ "$which" = pruned ]; then
      write_pruned_conf   # regenerate cleanly with the lean+polite settings (avoids stale/partial conf)
      echo "Optimized pruned for ${cores}c/${ram}MB → dbcache=$PRUNED_DBCACHE, par=$PRUNED_PAR, maxconn=$PRUNED_MAXCONN (lean+polite)"
    else
      # dbcache: honor an explicit BANKON_BTC_DBCACHE override, else compute the largest power-of-2
      # ≤ (RAM − 2GB headroom), capped 8192 — fills RAM, leaves OS room.
      if [ -n "${BANKON_BTC_DBCACHE:-}" ]; then db="$BANKON_BTC_DBCACHE"
      else db=512; head=$((ram - 2048)); for p in 1024 2048 4096 8192; do [ "$p" -le "$head" ] && db=$p; done; fi
      set_kv dbcache "$db"; set_kv par 0; set_kv rpcworkqueue 256; set_kv rpcthreads 16
      echo "Optimized full for ${cores} cores / ${ram}MB RAM → dbcache=$db, par=0 (all cores), rpcworkqueue=256, rpcthreads=16"
      echo "  (restart the node to apply — chainstate is on SSD, so it's CPU-bound; this is the IBD-max config for this box)"
    fi ;;
  stop)
    case "${2:-}" in
      pruned) cli_pruned stop ;;
      full)   cli_full stop ;;
      *) echo "usage: $0 stop pruned|full"; exit 1 ;;
    esac ;;
  status)
    echo "================ BANKON multi-node status ================"
    node_line full   "$FULL_DATADIR"   cli_full
    node_line pruned "$PRUNED_DATADIR" cli_pruned
    echo "=========================================================="
    echo "WaaS → pruned RPC: export BITCOIN_RPC_URL=http://127.0.0.1:$PRUNED_RPC BITCOIN_COOKIE=$PRUNED_DATADIR/.cookie" ;;
  *) echo "usage: $0 {init-pruned|start pruned|full|stop pruned|full|status|set-dbcache <MiB> [full|pruned]|optimize [full|pruned]}"; exit 1 ;;
esac
