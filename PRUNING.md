# BANKON — Blockchain size vs. security assessment

## Does pruning reduce security? No.
A pruned Bitcoin Core node **fully validates the entire chain** (every block, every
signature) during initial sync. It then discards old, already-verified block files.
Trustless validation is **identical** to an archival node. Pruning trades away
*serviceability and historical lookup*, not *security*:

| Capability | Full / archival | Pruned |
|---|---|---|
| Validate whole chain yourself (trustless) | ✅ | ✅ (same) |
| Send / receive / track wallets going forward | ✅ | ✅ |
| Build & broadcast transactions (PSBT) | ✅ | ✅ |
| Reorg safety at the tip | ✅ | ✅ (keeps recent undo data) |
| Look up ANY historical tx by txid (`txindex`) | ✅ | ❌ (incompatible) |
| Rescan a wallet's PAST history from genesis | ✅ | ❌ (only retained range) |
| Serve historical blocks to peers | ✅ | ❌ |

**Two hard rules:**
1. `prune` is **mutually exclusive with `txindex`** — Core won't start with both.
2. The prune value sizes **block storage only**. The UTXO `chainstate` (~12 GB at full
   sync) is always kept. Total disk ≈ chainstate + prune target.

## Tiers (set `prune=<MiB>`; minimum enforced by Core is 550)

| Tier | `prune=` | Block data | ≈ Total on disk* | Security | Best for |
|---|---|---|---|---|---|
| **Minimum / Lean** | `550` | ~0.55 GB | ~12.5 GB | Full validation; handles ~2-day reorgs | Smallest possible transacting node |
| **Default / Balanced** | `2048` | 2 GB | ~14 GB | Full validation; comfortable reorg + recent history | **BANKON default** |
| **Generous pruned** | `10000` | 10 GB | ~22 GB | Full validation; deeper local history | More rescan headroom |
| **Full / Archival** | *(off)* + `txindex=1` | whole chain | ~720+ GB | Full validation + full lookup/serving | Explorer / analytics / arbitrary txid lookup |

\* total = prune target + ~12 GB UTXO chainstate (grows slowly over time).

## Reversibility
- Full → pruned: allowed in place. Core prunes old blocks down to target on next start.
  `txindex` must be turned off; its data dir (`indexes/txindex`) can then be deleted.
- Pruned → full: **requires full re-download + `-reindex`** (the old blocks are gone).
  This is the one irreversible direction — choose deliberately.

## Recommendation for BANKON WaaS
The WaaS imports watch-only descriptors with `timestamp:"now"` and only ever needs the
chain going forward, so **Default (2 GB) pruned is the right fit for a transaction
service** and reclaims ~800 GB. Keep **Full/archival** only if BANKON also wants
explorer-grade arbitrary-txid lookup. Switch with `bankon-node-mode.sh`.
