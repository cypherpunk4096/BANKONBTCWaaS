#!/usr/bin/env bash
# deploy/deploy-all.sh — deploy VaultQuorum to every chain in chains.json at ONE address.
#
# The chain list is derived from the canonical registry: agenticplace.pythai.net/allchain.html
# Constructor args are IDENTICAL across chains, so the CREATE2 address is identical everywhere.
set -euo pipefail

CHAINS="${1:-deploy/chains.json}"

: "${VAULT_COMMITMENT:?set to 0x + sha256(tomb.key)}"
: "${VAULT_THRESHOLD:?set to N (required approvals)}"
: "${VAULT_PRIMARY_CHAIN:?set to the authoritative chain id}"
: "${VAULT_OWNERS:?comma-separated approver addresses}"
export VAULT_COMMITMENT VAULT_THRESHOLD VAULT_PRIMARY_CHAIN VAULT_OWNERS
export VAULT_FALLBACK_DELAY="${VAULT_FALLBACK_DELAY:-604800}"   # 7 days

jq -c '.[]' "$CHAINS" | while read -r row; do
  name=$(jq -r '.name' <<<"$row")
  rpc=$(jq -r '.rpc'  <<<"$row")
  echo "──────────── deploying to ${name} ────────────"
  forge script script/DeployVaultQuorum.s.sol:DeployVaultQuorum \
    --rpc-url "$rpc" --broadcast --verify -vvv
done

echo "All chains deployed. Now prove integrity:"
echo "  VAULT_ADDRESS=<addr> VAULT_KEY=/mnt/usb/operator.tomb.key \\"
echo "    python -m bankon_vault.multichain ${CHAINS}"
