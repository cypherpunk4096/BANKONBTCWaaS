#!/usr/bin/env bash
#
# bankon-diag.sh — BANKON scientific diagnostics for Bitcoin Core (v31)
#
# Produces a rigorous, quantified health report of the node, the indexes, and
# block metrics. Every figure is reported with units and a defined methodology;
# nothing is estimated where an exact RPC value exists. Measurements are
# timestamped and IBD caveats are stated explicitly.
#
# Tiers:
#   (default)  FAST  — all read-only, O(1) RPCs. Safe during IBD.
#   --deep           — adds heavy integrity ops: verifychain + gettxoutsetinfo.
#                      Locks cs_main for minutes; AVOID during IBD on slow disks.
#
# Usage:
#   ./bankon-diag.sh            # fast scientific report
#   ./bankon-diag.sh --deep     # + integrity & UTXO-set audit (heavy)
#   ./bankon-diag.sh --json     # machine-readable (fast tier)
#
set -uo pipefail
export PATH="/home/luvai/bitcoin-31.0/bin:$PATH"
CLI="bitcoin-cli"
TO() { timeout "${1}" "${@:2}"; }   # bounded RPC so the report never hangs
RPC() { TO 15 $CLI "$@" 2>/dev/null; }

DEEP=0; JSON=0
for a in "$@"; do
  case "$a" in --deep) DEEP=1;; --json) JSON=1;; esac
done

command -v jq >/dev/null 2>&1 && HAVE_JQ=1 || HAVE_JQ=0
g() { # extract a numeric/string field from JSON via jq if present, else grep
  local json="$1" key="$2"
  if [ "$HAVE_JQ" = 1 ]; then echo "$json" | jq -r ".$key // empty"; else
    echo "$json" | grep -oE "\"$key\"[: ]+[^,}]+" | head -1 | sed -E 's/.*[: ]+//; s/"//g'; fi
}

NOW="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
CHAININFO="$(RPC getblockchaininfo)"
[ -z "$CHAININFO" ] && { echo "FATAL: node RPC unresponsive (node busy validating, or down). Retry shortly."; exit 1; }

blocks=$(g "$CHAININFO" blocks);       headers=$(g "$CHAININFO" headers)
vp=$(g "$CHAININFO" verificationprogress); ibd=$(g "$CHAININFO" initialblockdownload)
chainwork=$(g "$CHAININFO" chainwork); sizedisk=$(g "$CHAININFO" size_on_disk)
diff=$(g "$CHAININFO" difficulty);     pruned=$(g "$CHAININFO" pruned)
mediantime=$(g "$CHAININFO" mediantime); besthash=$(g "$CHAININFO" bestblockhash)

# ---- derived, with explicit formulas -------------------------------------
hdr_gap=$(( ${headers:-0} - ${blocks:-0} ))                 # blocks downloaded-but-unvalidated frontier
pct=$(awk -v v="${vp:-0}" 'BEGIN{printf "%.6f", v*100}')    # verification %, 6 d.p.
tip_age_s=$(( $(date -u +%s) - ${mediantime:-0} ))          # wall-clock lag of validated tip
tip_age_h=$(awk -v s="$tip_age_s" 'BEGIN{printf "%.1f", s/3600}')
tip_age_y=$(awk -v s="$tip_age_s" 'BEGIN{printf "%.2f", s/31557600}')
disk_gib=$(awk -v b="${sizedisk:-0}" 'BEGIN{printf "%.1f", b/1073741824}')

echo "======================================================================"
echo " BANKON SCIENTIFIC DIAGNOSTIC  —  measured $NOW"
echo "======================================================================"
echo
echo "[A] CHAIN STATE (exact RPC values)"
echo "  chain ................. $(g "$CHAININFO" chain)"
echo "  validated height ..... ${blocks}  (best=${besthash:0:16}…)"
echo "  header height ........ ${headers}"
echo "  unvalidated frontier . ${hdr_gap} blocks   [= headers − blocks]"
echo "  verification ......... ${pct} %            [verificationprogress×100, 6 d.p.]"
echo "  IBD active ........... ${ibd}"
echo "  tip wall-clock lag ... ${tip_age_h} h  ≈ ${tip_age_y} yr   [now − mediantime]"
echo "  chainwork ............ ${chainwork}"
echo "  difficulty ........... ${diff}"
echo "  size on disk ......... ${disk_gib} GiB   pruned=${pruned}"
echo

echo "[B] INDEX QUALITY  (lag = tip − index.best_block_height; quality = synced ∧ lag=0)"
IDX="$(RPC getindexinfo)"
if [ -z "$IDX" ] || [ "$IDX" = "{}" ]; then
  echo "  (no indexes reported — getindexinfo empty)"
else
  if [ "$HAVE_JQ" = 1 ]; then
    echo "$IDX" | jq -r --argjson tip "${blocks:-0}" '
      to_entries[] | . as $e |
      ($tip - $e.value.best_block_height) as $lag |
      "  \($e.key): synced=\($e.value.synced)  best_height=\($e.value.best_block_height)  lag=\($lag) blk  quality=\(if ($e.value.synced==true and $lag==0) then "PASS" else "DEGRADED" end)"'
  else
    echo "$IDX"
  fi
  echo "  note: txindex is REQUIRED for BANKON tx lookups by txid; DEGRADED during IBD is expected."
fi
echo

echo "[C] BLOCK METRICS"
CTS="$(RPC getchaintxstats 2016)"     # ~2-week window
if [ -n "$CTS" ]; then
  wtx=$(g "$CTS" window_tx_count); wint=$(g "$CTS" window_interval); txtot=$(g "$CTS" txcount)
  tps=$(awk -v t="${wtx:-0}" -v i="${wint:-1}" 'BEGIN{if(i>0)printf "%.4f", t/i; else print "n/a"}')
  bint=$(awk -v i="${wint:-0}" -v n="${wtx:-0}" 'BEGIN{print "n/a"}')
  avg_blk=$(awk -v i="${wint:-0}" 'BEGIN{if(i>0)printf "%.1f", i/2016/60; else print "n/a"}')
  echo "  cumulative tx count .. ${txtot}"
  echo "  window (2016 blk) tx . ${wtx}   over ${wint}s"
  echo "  throughput ........... ${tps} tx/s        [window_tx_count / window_interval]"
  echo "  mean block interval .. ${avg_blk} min/blk  [window_interval / 2016 / 60]"
fi
# Per-block stats on the validated tip (exact, from getblockstats)
if [ -n "$blocks" ]; then
  BS="$(RPC getblockstats "$blocks")"
  if [ -n "$BS" ]; then
    echo "  --- tip block #$blocks ---"
    echo "    txs .............. $(g "$BS" txs)"
    echo "    weight ........... $(g "$BS" total_weight) WU  (limit 4,000,000)"
    echo "    size ............. $(g "$BS" total_size) B"
    echo "    feerate (sat/vB) . min=$(g "$BS" minfeerate) med=$(g "$BS" medianfeerate) max=$(g "$BS" maxfeerate)"
    echo "    total fees ....... $(g "$BS" totalfee) sat   subsidy=$(g "$BS" subsidy) sat"
    echo "    inputs/outputs ... $(g "$BS" ins)/$(g "$BS" outs)   utxo_increase=$(g "$BS" utxo_increase)"
  fi
fi
echo

echo "[D] MEMPOOL"
MP="$(RPC getmempoolinfo)"
if [ -n "$MP" ]; then
  echo "  txs .................. $(g "$MP" size)"
  echo "  virtual bytes ........ $(g "$MP" bytes)"
  echo "  memory usage ......... $(awk -v b="$(g "$MP" usage)" 'BEGIN{printf "%.1f", b/1048576}') MiB"
  echo "  min relay feerate .... $(g "$MP" mempoolminfee) BTC/kvB"
fi
echo

echo "[E] NETWORK"
NET="$(RPC getnetworkinfo)"
conns=$(g "$NET" connections)
echo "  connections .......... ${conns:-?}   $([ "${conns:-0}" = "0" ] && echo '<-- WARN: 0 peers = no new blocks inbound')"
echo "  protocol version ..... $(g "$NET" protocolversion)   subver=$(g "$NET" subversion)"
echo

if [ "$DEEP" = 1 ]; then
  echo "[F] INTEGRITY AUDIT (heavy — exact)"
  echo "  verifychain(level=3, nblocks=144) ..."
  VC="$(TO 600 $CLI verifychain 3 144 2>/dev/null)"
  echo "    result: ${VC:-<timed out / inconclusive>}   [true = last 144 blocks consistent]"
  echo "  gettxoutsetinfo (UTXO set hash + supply) ..."
  UTXO="$(TO 600 $CLI gettxoutsetinfo 2>/dev/null)"
  if [ -n "$UTXO" ]; then
    echo "    height ........... $(g "$UTXO" height)"
    echo "    utxo count ....... $(g "$UTXO" txouts)"
    echo "    total amount ..... $(g "$UTXO" total_amount) BTC"
    echo "    set hash ......... $(g "$UTXO" hash_serialized_3)"
  else
    echo "    <timed out — expected during IBD on slow storage>"
  fi
  echo
fi

echo "======================================================================"
echo " Methodology: all values from Bitcoin Core JSON-RPC at the timestamp"
echo " above. Derived quantities show their formula in [brackets]. During IBD"
echo " (IBD active = ${ibd}) block-metric and index figures describe the"
echo " VALIDATED tip, not the network tip; they converge as sync completes."
echo "======================================================================"
