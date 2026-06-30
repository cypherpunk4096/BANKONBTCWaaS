#!/usr/bin/env bash
#
# bankon-wallet.sh — BANKON wallet provisioning layer for Bitcoin Core (v31)
#
# Creates single or bulk descriptor wallets against the running node. Wallets
# created here appear automatically in the Bitcoin-Qt wallet dropdown.
#
# Usage:
#   ./bankon-wallet.sh create <name> [addr_type]
#   ./bankon-wallet.sh bulk <prefix> <count> [start_index] [addr_type]
#   ./bankon-wallet.sh list
#
# Encryption:
#   If a passphrase file exists at $BANKON_PASS_FILE (default ~/.bankon_pass),
#   every wallet is created ENCRYPTED with that passphrase (read from file, so
#   it never lands in shell history or the process list). Leave it absent to
#   create unencrypted wallets (NOT recommended for funds).
#
#   Create it safely with:   umask 077; printf '%s' 'YOUR-PASSPHRASE' > ~/.bankon_pass
#
# Examples:
#   ./bankon-wallet.sh create BANKON_ops
#   ./bankon-wallet.sh bulk BANKON 50            # BANKON_0001 .. BANKON_0050
#   ./bankon-wallet.sh bulk BANKON 50 101 legacy # BANKON_0101 .. start at 101
#
set -euo pipefail

export PATH="/home/luvai/bitcoin-31.0/bin:$PATH"
CLI="bitcoin-cli"

ADDR_TYPE_DEFAULT="bech32m"          # bech32m | bech32 | p2sh-segwit | legacy
PASS_FILE="${BANKON_PASS_FILE:-$HOME/.bankon_pass}"
BACKUP_DIR="${BANKON_BACKUP_DIR:-$HOME/bankon-tools/backups}"
MANIFEST="$BACKUP_DIR/bankon_wallets_manifest.csv"

mkdir -p "$BACKUP_DIR"
[ -f "$MANIFEST" ] || echo "timestamp,wallet,address,addr_type,encrypted,backup_file" > "$MANIFEST"

# Read passphrase from file if present (never via argv).
PASSPHRASE=""
ENCRYPTED="no"
if [ -f "$PASS_FILE" ]; then
  PASSPHRASE="$(cat "$PASS_FILE")"
  [ -n "$PASSPHRASE" ] && ENCRYPTED="yes"
fi

die() { echo "ERROR: $*" >&2; exit 1; }

node_ready() {
  $CLI getblockchaininfo >/dev/null 2>&1 || die "Node RPC not responding. Is bitcoind/bitcoin-qt running?"
}

wallet_exists() {
  $CLI listwallets 2>/dev/null | grep -q "\"$1\"" && return 0
  # also check on-disk (created-but-unloaded)
  $CLI listwalletdir 2>/dev/null | grep -q "\"$1\"" && return 0
  return 1
}

create_one() {
  local name="$1" addr_type="${2:-$ADDR_TYPE_DEFAULT}" stamp addr backup
  stamp="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

  if wallet_exists "$name"; then
    echo "  SKIP  $name (already exists)"; return 0
  fi

  # createwallet "name" disable_priv blank passphrase avoid_reuse descriptors load_on_startup
  # Passing a passphrase here creates the wallet encrypted in one atomic call.
  $CLI createwallet "$name" false false "$PASSPHRASE" false true true >/dev/null \
    || die "createwallet failed for $name"

  # Descriptor wallets derive receive addresses without unlocking (pubkey-only).
  addr="$($CLI -rpcwallet="$name" getnewaddress "BANKON-primary" "$addr_type")" \
    || die "getnewaddress failed for $name"

  # Full encrypted wallet-file backup.
  backup="$BACKUP_DIR/${name}.bak"
  $CLI -rpcwallet="$name" backupwallet "$backup" >/dev/null \
    || echo "  WARN  backup failed for $name"

  echo "$stamp,$name,$addr,$addr_type,$ENCRYPTED,$backup" >> "$MANIFEST"
  echo "  OK    $name  ->  $addr  (enc=$ENCRYPTED)"
}

cmd="${1:-}"; shift || true
case "$cmd" in
  create)
    [ $# -ge 1 ] || die "usage: create <name> [addr_type]"
    node_ready
    create_one "$1" "${2:-$ADDR_TYPE_DEFAULT}"
    echo "Manifest: $MANIFEST"
    ;;
  bulk)
    [ $# -ge 2 ] || die "usage: bulk <prefix> <count> [start_index] [addr_type]"
    prefix="$1"; count="$2"; start="${3:-1}"; addr_type="${4:-$ADDR_TYPE_DEFAULT}"
    [[ "$count" =~ ^[0-9]+$ && "$start" =~ ^[0-9]+$ ]] || die "count/start must be integers"
    node_ready
    echo "Bulk-creating $count wallets: ${prefix}_$(printf '%04d' "$start") .. ${prefix}_$(printf '%04d' $((start+count-1)))  (enc=$ENCRYPTED, type=$addr_type)"
    end=$((start + count - 1))
    for i in $(seq "$start" "$end"); do
      create_one "$(printf '%s_%04d' "$prefix" "$i")" "$addr_type"
    done
    echo "Done. Manifest: $MANIFEST"
    echo "Backups: $BACKUP_DIR"
    ;;
  list)
    node_ready
    echo "Loaded wallets:"; $CLI listwallets
    echo; echo "On-disk wallets:"; $CLI listwalletdir
    ;;
  *)
    grep '^#' "$0" | sed 's/^# \{0,1\}//' | head -40
    exit 1
    ;;
esac
