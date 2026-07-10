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

## Tradeoffs — what a pruned node gives up
Pruning does **not** weaken validation (every block is fully verified before old ones are
discarded), but a pruned node **cannot**:
- **Serve historical blocks** to peers (it only has recent ones) — it stops being an archival seed.
- **Look up an arbitrary old transaction** — `txindex` is incompatible with `prune` (see
  [docs/indexer.md](docs/indexer.md)); `getrawtransaction` works only for txs still in the kept window.
- **Rescan far back** — importing a descriptor with an old `timestamp` may need blocks it no longer
  has; BANKON avoids this by importing watch-only with `timestamp:"now"`.
- **Go back to full without a full re-download** — the one irreversible direction (above).

What it keeps: full consensus validation, mempool, sending/receiving, watch-only balances, and a
recent-history window — everything the WaaS needs.

## Rapid bootstrap — the recent-block slice + UTXO snapshot
**Is "export the last N GB of the chain to rapidly update a pruned node" a real thing?** *Partly —
and only when paired correctly.* Raw recent block files (`blocks/blk*.dat`, ~128 MB each,
highest-numbered = newest) can be exported, but recent blocks **alone cannot bootstrap a node** — a
node needs the **UTXO set** at the base of the slice to validate forward.

The genuinely-supported fast path is **assumeUTXO**: a **UTXO snapshot** provides the state, and the
**recent block slice** lets the node validate forward from there. Together they are the "rapid point
to update from" for a fresh pruned node:

1. On the source node: **Export UTXO snapshot** (`dumptxoutset … latest`) — see
   [docs/indexer.md](docs/indexer.md).
2. **Export recent block slice** — `POST /api/blocks/export-recent { sizeGb: 2 }` copies the newest
   ~N GB of `blk*.dat` (+ `rev*.dat`) to `~/bankon-tools/exports/recent-blocks/`, reporting the file
   range. (Node-control-gated; single-flight.)
3. On the target: `loadtxoutset` the snapshot, drop the block slice into its `blocks/`. It comes up
   usable at the snapshot height and validates forward, then background-validates to genesis.

For a node that **already has the earlier chain**, the block slice alone speeds catch-up (it validates
from disk instead of re-downloading). For a from-scratch pruned node, always pair it with the snapshot.

## Recommendation for BANKON WaaS
The WaaS imports watch-only descriptors with `timestamp:"now"` and only ever needs the
chain going forward, so **Default (2 GB) pruned is the right fit for a transaction
service** and reclaims ~800 GB. Keep **Full/archival** only if BANKON also wants
explorer-grade arbitrary-txid lookup. Switch with `bankon-node-mode.sh`.
