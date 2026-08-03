# BANKON Vault — Multi-Chain Quorum (design addendum)

This extends `BANKON_VAULT_Tomb_Integration_Guide.md` §5. It answers one question:
**where does the quorum contract live?** Answer: **the same contract at the same address on
every chain**, all pointing at one identical commitment.

## The decision, in plain terms

Your Tomb key never goes on-chain. What goes on-chain is a **commitment** — `sha256(tomb.key)` —
plus a ledger of owner approvals. We deploy that `VaultQuorum` contract to many chains, but we make
it land at the **identical address** on each one (CREATE2 with identical constructor args).

Why identical address + identical commitment matters — this is the **integrity** you flagged as the
requirement:

1. **Immutable.** No admin key, no upgrade. The commitment is fixed at construction. Nobody — not
   even you — can swap it later.
2. **Cross-verifiable.** The same address on every chain must report the same commitment. If any
   chain disagrees, that's a tamper alarm. `bankon_vault/multichain.py` reads all of them and
   **fails closed** on divergence.
3. **Locally checked.** Before a reconstituted key is ever used, it's re-hashed and compared to the
   anchored commitment (guide §5, `quorum.py`).

So the vault has integrity at three layers: on-chain immutability, cross-chain agreement, and local
hash verification.

## What "multi-chain" actually buys

Be clear-eyed: a key-custody quorum isn't a yield product — it doesn't earn. What multi-chain buys is
**resilience and verifiability**, which is what makes it worth doing:

- **Censorship/liveness resilience** — if one chain is down or censoring, owners can still act.
- **Redundant tamper-evidence** — the commitment is publicly anchored in N places at once.
- **Ecosystem coherence** — the anchor sits at one address alongside your ERC-8004 registry, so
  AgenticPlace / mindX tooling references a single deterministic address everywhere.

## Authority model (why not just "any chain unlocks")

Letting *any* chain's quorum authorize would mean an attacker only needs to subvert approvers on the
**weakest** chain. So instead:

- One **`primaryChainId`** is authoritative — quorum there unlocks immediately.
- Every other chain is a **commitment anchor** plus a **break-glass fallback**: reaching quorum there
  arms a timelock (default 7 days) before it authorizes, giving watchers time to react if the primary
  is compromised or dead. This is baked into `unlocked()`.

Because `primaryChainId` is a constructor arg identical on every chain, the address stays deterministic
while the contract still knows, at runtime, whether it *is* the primary (`block.chainid`).

## Run it

```bash
# 1. commit to your key
export VAULT_COMMITMENT=0x$(sha256sum /mnt/usb/operator.tomb.key | cut -d' ' -f1)
export VAULT_THRESHOLD=3
export VAULT_PRIMARY_CHAIN=1
export VAULT_OWNERS=0xAlice,0xBob,0xCarol,0xDave,0xEve

# 2. test locally
forge test -vvv

# 3. deploy everywhere at one address (chains.json from allchain.html)
cp deploy/chains.example.json deploy/chains.json   # edit RPCs
bash deploy/deploy-all.sh deploy/chains.json

# 4. prove integrity across all chains (fails closed on any divergence)
VAULT_ADDRESS=<printed_address> VAULT_KEY=/mnt/usb/operator.tomb.key \
  python -m bankon_vault.multichain deploy/chains.json
```

`foundry.toml`:
```toml
[profile.default]
src = "contracts"
test = "test"
script = "script"
solc = "0.8.24"
optimizer = true
optimizer_runs = 200
```

## Algorand / parsec x402 caveat (honest note)

CREATE2 and "same EVM address everywhere" are **EVM-only**. Your x402 rail runs on **Algorand** via
`parsec` / `parsec-wallet`, which is not EVM and has no CREATE2. Keep the authoritative quorum on one
**EVM** chain, and mirror the *same commitment* into an Algorand app (Algopy) as a second anchor for
the payment side. Same integrity property (identical `sha256(tomb.key)`), different deployment
mechanics — you get cross-ecosystem tamper-evidence without pretending Algorand is EVM.

The vault key still never leaves the host; both ecosystems only ever see the commitment and approvals.
