# Architecture

## Principle: attach, never replace
BANKON layers on top of a **running Bitcoin Core**. It never custodies keys and never
modifies the node's consensus role — it consumes the node over JSON-RPC (cookie auth)
and exposes WaaS + diagnostics.

## Non-custodial data flow
```
  CLIENT (browser / offline file)            BANKON API + Bitcoin Core node
  ───────────────────────────────            ──────────────────────────────
  entropy → BIP39 mnemonic (PRIVATE)
  mnemonic → BIP32 xprv (PRIVATE)
  derive account xpub + descriptor ───────▶  POST /api/wallet  (PUBLIC descriptor only)
                                             createwallet disable_private_keys=true
                                             importdescriptors (watch-only)
  ◀────────────── unsigned PSBT ───────────  POST /api/wallet/:n/send (walletcreatefundedpsbt)
  sign locally with xprv (@scure)
  finalized tx hex ───────────────────────▶  POST /api/broadcast (sendrawtransaction)
```
The mnemonic and xprv **never leave the client**. The server defensively rejects any
request body containing `mnemonic|xprv|seed|privkey|wif|passphrase`.

## Components
| Component | Tech | Port | Role |
|-----------|------|------|------|
| WaaS API + UI | Express + vanilla ESM + `@scure/*` | 8088 | keygen (browser), watch-only register, PSBT build, broadcast, registry, fees |
| Console | Express + vanilla ESM | 8090 | read-only diagnostics; whitelisted RPC proxy |
| Qt UI | PySide6 | — | native desktop diagnostics (software-rendered) |
| Full node | Bitcoin Core v31 | RPC 8332 / P2P 8333 | archival (txindex) — explorer/lookup |
| Pruned node | Bitcoin Core v31 | RPC 8342 / P2P 8334 | lean WaaS backend (planned; see operations.md) |

## Multi-node model
`prune` and `txindex` are mutually exclusive, so BANKON runs **two** instances:
- **Full/archival** — keeps the whole chain + `txindex` for arbitrary tx lookup.
- **Pruned** — `prune=2048`, full validation, lean (~14 GB) WaaS backend.

Pruning does **not** weaken security — a pruned node fully validates every block, then
discards old ones. See [PRUNING.md](../PRUNING.md).

## Key derivation (BTC Standard)
| Type | BIP | Path | Script |
|------|-----|------|--------|
| Native SegWit | 84 | `m/84'/0'/0'` | `wpkh` (bc1q…) |
| Taproot | 86 | `m/86'/0'/0'` | `tr` (bc1p…) |
| Legacy | 44 | `m/44'/0'/0'` | `pkh` (1…) |
| Multisig | 48 | `m/48'/0'/0'/2'` | `wsh(sortedmulti(N,…))` |

Signing is network-version-independent: `sign.mjs` derives the same private keys whether
the descriptor used mainnet (`xpub`) or testnet/regtest (`tpub`) extended keys.
