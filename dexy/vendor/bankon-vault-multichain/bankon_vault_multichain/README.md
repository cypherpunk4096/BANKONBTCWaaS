# bankon_vault — multi-chain Tomb quorum

Encrypted operator vault (Tomb/dm-crypt/LUKS) whose key is governed by an N-of-M
owner quorum, anchored immutably at ONE deterministic address across EVM chains
and mirrored to Algorand for the parsec/x402 payment side.

Read in this order:
1. `USAGE.md` — technical model, use cases, end-to-end runbook, threat table
2. `MULTICHAIN.md` — design rationale (primary + timelock, CREATE2, Algorand caveat)
3. `BANKON_VAULT_Tomb_Integration_Guide.md` — the full Tomb inclusion guide
   (all commands, compatible UIs, hooks, security standard, zuluCrypt fork blueprint)

Quick start: `forge test -vvv` then follow USAGE.md §5.
License: GPL-3.0-or-later. Tomb by dyne.org (https://github.com/dyne/Tomb).
