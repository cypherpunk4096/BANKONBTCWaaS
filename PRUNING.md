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
| **Minimum (floor)** | `550` | ~0.55 GB | ~12.5 GB | Full validation; handles ~2-day reorgs | Core's hard minimum |
| **Minimal** | `1024` | 1 GB | ~13 GB | Full validation; ~4-day reorg headroom | **Recommended smallest — "1 GB minimal"** |
| **Default / Balanced** | `2048` | 2 GB | ~14 GB | Full validation; comfortable reorg + recent history | **BANKON default** |
| **Generous pruned** | `10000` | 10 GB | ~22 GB | Full validation; deeper local history | More rescan headroom |
| **Full / Archival** | *(off)* + `txindex=1` | whole chain | ~720+ GB | Full validation + full lookup/serving | Explorer / analytics / arbitrary txid lookup |

\* total = prune target + ~12 GB UTXO chainstate (grows slowly over time).

## The accuracy measure — proven, not asserted

**Does a smaller prune (1 GB) validate less accurately than 10 GB or a full archival node? No —
and it is measurable.** A node's accuracy *is* the correctness of its **UTXO set** (the exact set
of spendable coins after validating every block). `gettxoutsetinfo muhash` returns a cryptographic
hash over that whole set. **Two nodes at the same height with the same UTXO hash have byte-for-byte
identical validation results.** Because pruning discards only old *block files* — never the
chainstate, and only after each block is fully verified — a 1 GB-pruned node and an archival node
compute the **same hash**.

`bankon-waas/test-prune-regtest.sh` proves this from node **creation**: it spins up nodes born at
`550 · 1024 · 2048 · 10000` and an archival node, feeds them one shared chain, and asserts every
pruned node's UTXO `muhash` **equals** the archival node's. Run it:

```bash
bankon-waas/test-prune-regtest.sh            # PASS = all prune sizes → identical UTXO hash
BLOCKS=500 bankon-waas/test-prune-regtest.sh # deeper chain
```

**pruned-node validation vs a VERIFIED payment — two different things:**
- **Node validation** (above) is chain-wide: the node fully validated *the whole chain*; pruning
  doesn't change that. This is what the UTXO-hash test measures.
- **A VERIFIED payment** is per-transaction: *this* received tx is confirmed in a block. A pruned
  node confirms payments identically to an archival one — it has the block that pays you and the
  UTXO it creates. See the WaaS receive/verify flow ([api.md](docs/api.md)); a VERIFIED payment
  records its block + txid to the ICE `.history` for later minting / proof.

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
