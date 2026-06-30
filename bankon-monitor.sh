#!/usr/bin/env bash
#
# bankon-monitor.sh — health watchdog for the BANKON stack. Run on a timer.
# Checks node reachability, peer count, disk headroom, sync progress (stall
# detection), and that the WaaS + Console services answer. Logs + alerts.
#
# Exit 0 = healthy, 1 = warning(s). Wire to systemd timer (see systemd/) or
# the /schedule skill for periodic cloud runs.
#
set -uo pipefail
export PATH="/home/luvai/bitcoin-31.0/bin:$PATH"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG="${BANKON_MONITOR_LOG:-$HERE/.monitor.log}"
STATE="${BANKON_MONITOR_STATE:-$HERE/.monitor.state}"
MIN_PEERS="${BANKON_MIN_PEERS:-3}"
MIN_DISK_GB="${BANKON_MIN_DISK_GB:-5}"
WARN=0
ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
say(){ echo "$ts $*" | tee -a "$LOG"; }
warn(){ WARN=1; say "WARN: $*"; }

# Node
if info="$(timeout 60 bitcoin-cli getblockchaininfo 2>/dev/null)"; then
  blocks=$(echo "$info" | grep -oE '"blocks": *[0-9]+' | grep -oE '[0-9]+')
  conns=$(timeout 15 bitcoin-cli getconnectioncount 2>/dev/null || echo 0)
  [ "${conns:-0}" -lt "$MIN_PEERS" ] && warn "low peers: $conns (< $MIN_PEERS)"
  # stall detection: compare with previous height
  prev=$(cat "$STATE" 2>/dev/null || echo 0)
  if [ "${blocks:-0}" -le "${prev:-0}" ] && [ "${prev:-0}" -gt 0 ]; then
    warn "sync stalled — height ${blocks} unchanged since last check (was ${prev})"
  fi
  echo "${blocks:-0}" > "$STATE"
  say "node OK: block ${blocks}, peers ${conns}"
else
  warn "node not reachable on RPC (may be mid-validation; re-checks next run)"
fi

# Disk (datadir filesystem)
avail_gb=$(df -BG --output=avail "$HOME/.bitcoin" 2>/dev/null | tail -1 | tr -dc '0-9')
[ -n "$avail_gb" ] && [ "$avail_gb" -lt "$MIN_DISK_GB" ] && warn "low disk: ${avail_gb} GB free (< ${MIN_DISK_GB})"

# Services
for svc in "WaaS:8088" "Console:8090"; do
  name="${svc%%:*}"; port="${svc##*:}"
  curl -s -o /dev/null --max-time 5 "http://127.0.0.1:$port/" || warn "$name not responding on :$port"
done

[ "$WARN" -eq 0 ] && say "all healthy" || say "completed with warnings"
exit "$WARN"
