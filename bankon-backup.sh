#!/usr/bin/env bash
#
# bankon-backup.sh — encrypted backup/restore of BANKON's watch-only state.
#
# Backs up the wallet registry (public descriptors + metadata). This is PUBLIC
# data (watch-only), but it's encrypted so a backup can't leak which addresses
# you own. NOTE: user PRIVATE keys/mnemonics live only on clients and are NOT in
# scope here — back those up separately (write the recovery phrase down).
#
#   bankon-backup.sh create [outfile.enc]   encrypt registry → outfile
#   bankon-backup.sh restore <file.enc>      decrypt to stdout
#
# Passphrase from $BANKON_BACKUP_PASS, else prompted. AES-256 + PBKDF2 (openssl).
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REGISTRY="${BANKON_REGISTRY:-$HERE/bankon-waas/registry.json}"
cmd="${1:-create}"

get_pass() { [ -n "${BANKON_BACKUP_PASS:-}" ] && { printf '%s' "$BANKON_BACKUP_PASS"; return; }
  read -rsp "Backup passphrase: " p; echo >&2; printf '%s' "$p"; }

case "$cmd" in
  create)
    out="${2:-$HERE/backups/bankon-registry-$(date -u +%Y%m%d%H%M%S).enc}"
    mkdir -p "$(dirname "$out")"
    [ -f "$REGISTRY" ] || { echo "no registry at $REGISTRY (nothing registered yet)"; exit 1; }
    pass="$(get_pass)"
    openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt \
      -in "$REGISTRY" -out "$out" -pass pass:"$pass" || { echo "encrypt failed"; exit 1; }
    echo "Encrypted backup → $out  ($(wc -c < "$out") bytes)"
    echo "Restore with: $0 restore \"$out\""
    ;;
  restore)
    file="${2:?usage: restore <file.enc>}"
    pass="$(get_pass)"
    openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 \
      -in "$file" -pass pass:"$pass" || { echo "decrypt failed (wrong passphrase?)"; exit 1; }
    ;;
  *) echo "usage: $0 {create [outfile]|restore <file>}"; exit 1 ;;
esac
