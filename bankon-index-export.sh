#!/usr/bin/env bash
# bankon-index-export.sh — BANKON DaaS (Duplication as a Service), a sub-component
# of WaaS. Exports Bitcoin Core's INDEX as a portable, verifiable bundle so another
# node can import it and skip rebuilding — saving a long verify.
#
# Why: the chain is in the ~1TB zone and still growing; rebuilding the txindex on a
# fresh node is slow. "Duplicating" the already-built index (this machine acting as a
# UI + external blockchain-reference device) hands a peer a ready index to verify
# against their own blocks. First DaaS target = indexes; future = chainstate/blocks.
#
# Exports ~/.bitcoin/indexes/{txindex,coinstatsindex,blockfilter} + a manifest
# (height, best-block hash, Core version, sizes, sha256 of the archive). LevelDB must
# be consistent → the node should be STOPPED.
set -euo pipefail

BIN="${BANKON_BTC_BIN:-$HOME/bitcoin-31.0/bin}"
DATADIR="${BANKON_BTC_DATADIR:-$HOME/.bitcoin}"
OUT="$HOME"
DO_STOP=0
FORCE=0
GRANTEE=""      # who this duplication is granted to — duplication is a PRIVILEGE, not open
COMP="zstd"; command -v zstd >/dev/null 2>&1 || COMP="gzip"

usage() {
  cat <<EOF
bankon-index-export.sh — package the Bitcoin Core index for sharing

Usage: ./bankon-index-export.sh [options]
  --datadir DIR   Bitcoin data dir      (default: $DATADIR)
  --out DIR       Output directory      (default: $OUT)
  --stop          Stop bitcoind first (safe), then export
  --force         Export even if the node is running (LevelDB may be inconsistent)
  --gzip          Use gzip instead of zstd
  --grantee NAME  Record who this duplication is granted to (duplication is a privilege)
  -h, --help      This help

Import on another node (while ITS bitcoind is stopped):
  tar --zstd -xf bankon-index-<h>-<hash>.tar.zst -C <their-datadir>
  # then start bitcoind — it validates the index against its own blocks.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --datadir) DATADIR="$2"; shift 2;;
    --out)     OUT="$2"; shift 2;;
    --stop)    DO_STOP=1; shift;;
    --force)   FORCE=1; shift;;
    --gzip)    COMP="gzip"; shift;;
    --grantee) GRANTEE="$2"; shift 2;;
    -h|--help) usage; exit 0;;
    *) echo "unknown option: $1" >&2; usage; exit 1;;
  esac
done

CLI="$BIN/bitcoin-cli -datadir=$DATADIR"
IDX="$DATADIR/indexes"
[[ -d "$IDX" ]] || { echo "✗ no index dir at $IDX (is txindex=1 set and built?)" >&2; exit 1; }

# node running?
running=0
if $CLI getblockchaininfo >/dev/null 2>&1; then running=1; fi

HEIGHT="?"; BESTHASH="unknown"; COREVER="?"
if [[ "$running" -eq 1 ]]; then
  HEIGHT=$($CLI getblockcount 2>/dev/null || echo "?")
  BESTHASH=$($CLI getbestblockhash 2>/dev/null || echo unknown)
  COREVER=$("$BIN/bitcoind" --version 2>/dev/null | head -1 | grep -oE 'v[0-9.]+' || echo "?")
  if [[ "$DO_STOP" -eq 1 ]]; then
    echo "⏻ stopping bitcoind for a consistent export…"; $CLI stop >/dev/null 2>&1 || true
    for _ in $(seq 1 60); do $CLI getblockchaininfo >/dev/null 2>&1 || break; sleep 1; done
    running=0
  elif [[ "$FORCE" -ne 1 ]]; then
    echo "✗ bitcoind is RUNNING — the index (LevelDB) may be inconsistent if copied live."
    echo "  Re-run with --stop (recommended) or --force to override."
    exit 2
  else
    echo "⚠ exporting while the node runs (--force): the recipient should re-verify."
  fi
else
  COREVER=$("$BIN/bitcoind" --version 2>/dev/null | head -1 | grep -oE 'v[0-9.]+' || echo "?")
fi

# which index types are present
mapfile -t TYPES < <(cd "$IDX" && for d in txindex coinstatsindex blockfilter; do [[ -d "$d" ]] && echo "$d"; done)
[[ ${#TYPES[@]} -gt 0 ]] || { echo "✗ no known index subdirs under $IDX" >&2; exit 1; }

hash8="${BESTHASH:0:8}"; [[ -n "$hash8" ]] || hash8="nohash"
stamp="bankon-index-${HEIGHT}-${hash8}"
ext="tar.zst"; TARC=(--zstd); [[ "$COMP" == "gzip" ]] && { ext="tar.gz"; TARC=(-z); }
archive="$OUT/${stamp}.${ext}"
manifest="$OUT/${stamp}.json"

echo "▶ exporting index"
echo "  datadir : $DATADIR"
echo "  height  : $HEIGHT   best: $BESTHASH"
echo "  version : $COREVER"
echo "  indexes : ${TYPES[*]}"
echo "  archive : $archive  (compressor: $COMP)"

# tar the indexes/ subtree (keeps the relative 'indexes/...' path for easy import)
tar "${TARC[@]}" -C "$DATADIR" -cf "$archive" "$(basename "$IDX")/${TYPES[0]}" \
  $(for t in "${TYPES[@]:1}"; do echo "indexes/$t"; done)

SHA=$(sha256sum "$archive" | awk '{print $1}')
BYTES=$(stat -c%s "$archive")

cat > "$manifest" <<EOF
{
  "service": "BANKON DaaS — Duplication as a Service (index), under BaaS/WaaS",
  "value": "skips rebuilding ~1TB-scale index: saves the hardware + electrical + time cost of recreating it",
  "access": "privileged — duplication is a granted privilege, not an open endpoint",
  "granted_to": "${GRANTEE:-unspecified}",
  "created": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "core_version": "$COREVER",
  "height": "$HEIGHT",
  "best_block_hash": "$BESTHASH",
  "indexes": [$(printf '"%s",' "${TYPES[@]}" | sed 's/,$//')],
  "archive": "$(basename "$archive")",
  "archive_bytes": $BYTES,
  "archive_sha256": "$SHA",
  "exported_while_running": $([[ $running -eq 1 ]] && echo true || echo false)
}
EOF

echo
echo "✓ done"
echo "  $archive"
echo "  $manifest   (sha256 $SHA)"
echo
echo "Import on another node (its bitcoind STOPPED):"
echo "  tar $([[ $COMP == gzip ]] && echo -xzf || echo --zstd -xf) $archive -C <their-datadir>"
echo "  # then start bitcoind; it validates the index against its own block data."
