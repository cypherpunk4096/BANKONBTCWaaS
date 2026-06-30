#!/usr/bin/env bash
#
# bankon-node-mode.sh — set the Bitcoin Core blockchain-size mode for BANKON.
#
# Controls prune size (security is UNCHANGED by pruning — see PRUNING.md).
# Dry-run by default: prints the plan and disk impact. Pass --apply to edit
# bitcoin.conf (a timestamped backup is always made first).
#
# Tiers:
#   min       prune=550    (~0.55 GB blocks, ~12.5 GB total)  smallest transacting node
#   default   prune=2048   (2 GB blocks, ~14 GB total)        BANKON default
#   generous  prune=10000  (10 GB blocks, ~22 GB total)       deeper history
#   full      prune off + txindex=1 (~720+ GB)                explorer / arbitrary txid lookup
#
# Usage:
#   ./bankon-node-mode.sh default            # show plan (dry-run)
#   ./bankon-node-mode.sh default --apply     # apply + tell you how to restart
#   ./bankon-node-mode.sh full --apply
#
set -uo pipefail
export PATH="/home/luvai/bitcoin-31.0/bin:$PATH"
CONF="${BITCOIN_CONF:-$HOME/.bitcoin/bitcoin.conf}"
DATADIR="${BITCOIN_DATADIR:-$HOME/.bitcoin}"

MODE="${1:-}"; APPLY=0; [ "${2:-}" = "--apply" ] && APPLY=1
case "$MODE" in
  min)      PRUNE=550;   TXINDEX=0 ;;
  default)  PRUNE=2048;  TXINDEX=0 ;;
  generous) PRUNE=10000; TXINDEX=0 ;;
  full)     PRUNE=0;     TXINDEX=1 ;;
  *) echo "usage: $0 <min|default|generous|full> [--apply]"; exit 1 ;;
esac

# Current disk picture
blk=$(du -sb "$DATADIR/blocks" 2>/dev/null | cut -f1); blkg=$(awk -v b="${blk:-0}" 'BEGIN{printf "%.1f", b/1e9}')
cs=$(du -sb "$DATADIR/chainstate" 2>/dev/null | cut -f1); csg=$(awk -v b="${cs:-0}" 'BEGIN{printf "%.1f", b/1e9}')
ix=$(du -sb "$DATADIR/indexes" 2>/dev/null | cut -f1);    ixg=$(awk -v b="${ix:-0}" 'BEGIN{printf "%.1f", b/1e9}')

echo "=================================================================="
echo " BANKON node mode: $MODE   (apply=$([ $APPLY = 1 ] && echo yes || echo 'no — dry run'))"
echo "=================================================================="
echo " Current on disk: blocks=${blkg}GB  chainstate=${csg}GB  indexes(txindex)=${ixg}GB"
echo
if [ "$MODE" = "full" ]; then
  echo " Target: FULL / archival  —  txindex=1, pruning OFF"
  echo "   • Enables arbitrary tx-by-txid lookup and serving historical blocks."
  echo "   • Needs ~720+ GB. If the node was previously PRUNED, this requires a"
  echo "     full re-download with -reindex (old blocks are gone)."
else
  est=$(awk -v p="$PRUNE" -v c="${cs:-12000000000}" 'BEGIN{printf "%.1f", (p*1048576 + c)/1e9}')
  reclaim=$(awk -v b="${blk:-0}" -v p="$PRUNE" -v i="${ix:-0}" 'BEGIN{r=(b - p*1048576 + i)/1e9; if(r<0)r=0; printf "%.0f", r}')
  echo " Target: PRUNED  —  prune=$PRUNE MiB  (txindex OFF; required by Core)"
  echo "   • Full validation preserved — security UNCHANGED."
  echo "   • Total disk after sync ≈ ${est} GB (prune target + chainstate)."
  echo "   • Reclaims roughly ~${reclaim} GB from current blocks+txindex."
  echo "   • Loses: arbitrary txid lookup, old-wallet rescan, serving peers."
  echo "   • WaaS transactions (watch-only, timestamp=now, PSBT) still work fully."
  echo "   • IRREVERSIBLE direction: going back to full later needs full redownload."
fi
echo
echo " bitcoin.conf changes:"
if [ "$TXINDEX" = 1 ]; then echo "   txindex = 1 ; remove any prune="; else echo "   prune = $PRUNE ; txindex = 0"; fi
echo

if [ "$APPLY" != 1 ]; then
  echo " (dry run) re-run with --apply to write these changes."
  exit 0
fi

# ---- apply -------------------------------------------------------------------
if pgrep -x bitcoind >/dev/null || pgrep -x bitcoin-qt >/dev/null; then
  echo " NOTE: node is running. Config takes effect on next restart."
fi
cp -v "$CONF" "$CONF.bak.$(date -u +%Y%m%d%H%M%S)"

set_kv() { local k="$1" v="$2"
  if grep -qE "^[# ]*${k}=" "$CONF"; then sed -i -E "s|^[# ]*${k}=.*|${k}=${v}|" "$CONF"
  else printf '%s=%s\n' "$k" "$v" >> "$CONF"; fi; }
del_kv() { sed -i -E "/^[# ]*$1=.*/d" "$CONF"; }

if [ "$MODE" = "full" ]; then
  del_kv prune
  set_kv txindex 1
else
  set_kv txindex 0
  set_kv prune "$PRUNE"
fi

echo
echo " Updated. Effective lines:"
grep -E "^(prune|txindex)=" "$CONF" | sed 's/^/   /'
echo
echo " Next: restart the node to apply —"
echo "   bitcoin-cli stop   # wait for clean shutdown"
echo "   bitcoind           # daemon=1 is in the conf"
[ "$MODE" != "full" ] && [ "${ix:-0}" -gt 0 ] 2>/dev/null && \
  echo "   (optional, after stop) rm -rf \"$DATADIR/indexes/txindex\"   # reclaim ${ixg}GB of now-unused txindex"
echo "=================================================================="
