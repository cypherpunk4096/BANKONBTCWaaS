# Indexes & the UTXO snapshot

Bitcoin Core keeps optional **indexes** that speed up lookups the base node can't answer quickly,
and it can export a **UTXO snapshot** that lets *other* nodes bootstrap fast. BANKON surfaces both:
the **Indexes tab** shows each index's sync quality and on-disk size, and an **Export UTXO snapshot**
control produces the one index-derived artifact that is portable between nodes.

- Index dirs live under `~/.bitcoin/indexes/` (each is a LevelDB store).
- This host runs **`txindex=1`** (full archival). `getindexinfo` reports sync state; the Console adds
  disk size and a plain-English "what it enables" per index.
- Backend: `GET /api/indexinfo` (state + sizes) and `POST /api/index/export-utxo` (snapshot). See
  [server.md](server.md) and [console.md](console.md).

---

## 1 · The indexes

| Index | Enables | Config | Cost |
|-------|---------|--------|------|
| **txindex** | look up **any** transaction by txid (`getrawtransaction`) — not just wallet txs | `txindex=1` | ~66 GB (grows with the chain) |
| **coinstatsindex** | instant UTXO-set stats (`gettxoutsetinfo`): supply, UTXO count, muhash — no full scan | `coinstatsindex=1` | a few GB |
| **basic block filter index** | BIP157/158 compact block filters — light clients sync privately | `blockfilterindex=1` | a few GB |

Notes:
- **`txindex` is mutually exclusive with pruning** (`prune=N`). A pruned node discards old blocks, so
  it can't answer arbitrary txid lookups. See [../PRUNING.md](../PRUNING.md).
- Indexes **build in the background** during and after IBD and then track the tip. BANKON shows the
  frontier live from `debug.log` even while `getindexinfo` is RPC-choked.
- BANKON's WaaS only needs the node for watch-only wallet operations; `txindex` is what powers
  arbitrary transaction/explorer lookups on the full node.

### In BANKON
- **Indexes tab** (web `:8090` and Qt): per index → height · behind · % indexed · rate · ETA ·
  **size on disk** · status, a "crunching" activity indicator that animates only while indexing, and
  a tooltip describing what each index enables. The header shows total index disk.
- **API:** `GET /api/indexinfo` → `{ indexes:{ <name>:{ synced, best_block_height } }, sizes:{ <name>: bytes } }`.
  Sizes come from a background `du` (never blocks the request).
- **txindex deep-dive** (Indexes tab): drill into any single transaction — the direct payoff of
  `txindex`. It resolves the tx (`getrawtransaction … true`) and its prevouts to compute a **real fee
  and fee-rate**, and shows highlighted headline figures (outputs, fee, fee rate, vsize/weight, in→out
  count, confirmations) over full input/output tables (each output's address + value, coinbase
  flagged). Only a node with `txindex` can resolve an arbitrary txid — a pruned or no-index node can't.
  - **Recent-transaction browser:** an up/down **count** control (1–100) lists the latest N
    transactions (newest first, walked back from the tip); the **latest transaction is prefilled** in
    the query field, and clicking any history row deep-dives it. The list refreshes when a new block
    lands. Or paste any txid directly.
  - **Normal ↔ Scientific toggle:** Normal shows the essentials; **Scientific** reveals raw fields
    (version, locktime, size/vsize/weight), derived measures (**SegWit**, **RBF/BIP125**, witness
    item count, resolved inputs value, block height + median time), per-input **value** and
    **sequence** columns, output **script type**, a decoded **coinbase message** (see
    [coinbase.md](coinbase.md)), and the **raw transaction hex**.

---

## 2 · The UTXO snapshot (the important one)

### What it is
A **UTXO snapshot** is a serialized copy of the entire unspent-transaction-output set at a specific
block height. It is **not** one of the LevelDB indexes above — it's a single portable file. Its
purpose is **assumeUTXO**: a *new* node can load the snapshot and become usable in minutes instead
of waiting days for a full Initial Block Download, then quietly validate the whole chain down to
genesis in the background.

### Why the raw indexes can't be shared, but a snapshot can
The raw `txindex` maps each txid to a **byte offset inside *this* node's `blk*.dat` files**. Block
files are written in the order each node happens to receive blocks, so those offsets are meaningless
on any other machine — copying the index alone would corrupt it. (You could clone an entire datadir,
but that's cloning a node, not sharing an index.) A **UTXO snapshot**, by contrast, is defined purely
by consensus state at a height — it is deterministic and **portable to any node**.

### Producing one — `dumptxoutset`
```bash
# BANKON writes this for you (see below), or run it directly:
bitcoin-cli -rpcclienttimeout=0 dumptxoutset "/path/utxo-snapshot.dat" latest
```
- Use **`latest`** to snapshot the current tip **without** rolling back — this keeps **network
  activity online**. (Omitting `latest`, or using `rollback`, temporarily rewinds the node and
  **suspends networking** while it works — avoid unless you specifically need a historical snapshot.)
- It writes **~5–11 GB** and takes **several minutes**; use no RPC timeout (`-rpcclienttimeout=0`).
- Result fields: `coins_written`, `base_hash`, `base_height`, `txoutset_hash`, `nchaintx`, `path`.

### Consuming one — `loadtxoutset` (assumeUTXO)
On the receiving node:
```bash
bitcoin-cli loadtxoutset "/path/utxo-snapshot.dat"
```
The node comes up usable at the snapshot height and **background-validates to genesis**. While that
runs, `getblockchaininfo` reports `verificationprogress` 1.0 / `initialblockdownload` false, and a
`backgroundvalidation` object tracks the down-to-genesis pass (v31). The snapshot height must be one
the node's chainparams recognize. Full upstream detail: [upstream/assumeutxo.md](upstream/assumeutxo.md).

> **Trust note.** assumeUTXO gets you *usable* fast, but the snapshot is *assumed* valid until the
> background validation completes and confirms it against the actual chain. Treat a snapshot from a
> third party as unverified until your node has validated down to genesis; prefer snapshots you
> produced yourself, and check `base_hash`/`txoutset_hash` against a source you trust.

---

## 3 · Exporting a snapshot from BANKON

**Indexes tab → "Export UTXO snapshot".** It runs `dumptxoutset … latest` and writes to
`~/bankon-tools/exports/bankon-utxo-snapshot.dat` (override with `BANKON_EXPORT_DIR`).

- **Endpoint:** `POST /api/index/export-utxo` → `{ ok, path, elapsedSec, result:{ base_height,
  base_hash, txoutset_hash, coins_written } }`.
- **Gated:** requires `BANKON_NODE_CONTROL≠0` (on by default on localhost). It is not part of the
  read-only RPC surface — it's an explicit, single-flight node action.
- **Non-suspending:** uses `latest`, so the node stays online while it writes.
- **Heavy:** ~5–11 GB and minutes; the UI shows progress and, on completion, the base height, coin
  count, UTXO hash, and the exact `loadtxoutset` command to run on another node.

After it finishes, hand the file to another node and load it:
```bash
bitcoin-cli loadtxoutset "/…/bankon-utxo-snapshot.dat"
```

### Related fast-bootstrap RPCs in this build (v31)
- `loadtxoutset` — load a snapshot (assumeUTXO).
- `dumptxoutset` — write a snapshot (`latest` / `rollback`).
- `importmempool` — reload a previously saved `mempool.dat`.
- `getdescriptoractivity` — wallet/address activity over a block range from descriptors.

---

## 4 · Operations & disk

- **Check state:** `bitcoin-cli getindexinfo` · Indexes tab · `GET /api/indexinfo`.
- **Disk:** `du -sh ~/.bitcoin/indexes/*`. On this host the datadir lives on an external drive that
  has run near-full — indexes and snapshots are the big consumers, so watch free space before an
  export (the snapshot lands on the internal disk under `~/bankon-tools/exports/` by default).
- **Rebuild an index:** stop the node and restart with the index enabled; it back-fills from the
  block data. Rebuilding chainstate: `bitcoind -reindex-chainstate`.

---

*See also: [console.md](console.md) · [server.md](server.md) · [bitcoin-core-rpc.md](bitcoin-core-rpc.md) · [upstream/assumeutxo.md](upstream/assumeutxo.md) · [../PRUNING.md](../PRUNING.md).*
