# API Reference

All endpoints are JSON over HTTP on `127.0.0.1`. Responses use `{ "ok": true, ... }` or
`{ "ok": false, "error": "..." }`. Auth is **off by default**; set `BANKON_API_TOKEN` to
require `Authorization: Bearer <token>` on `/api/*` (see [security.md](security.md)).

## WaaS API — `http://127.0.0.1:8088`

### POST `/api/wallet` — register a watch-only single-sig wallet
Body (PUBLIC only — secrets are rejected):
```json
{ "type":"native-segwit", "fingerprint":"0c06170e", "xpub":"xpub…",
  "external":"wpkh([…]xpub…/0/*)", "internal":"wpkh([…]xpub…/1/*)",
  "label":"optional", "owner":"optional" }
```
→ `{ ok, wallet, walletId, watchOnly:true, firstAddress, record }`

### POST `/api/wallet/multisig` — register an N-of-M multisig
```json
{ "threshold":2, "keys":[
    {"xpub":"…","fingerprint":"…","path":"48'/0'/0'/2'"},
    {"xpub":"…","fingerprint":"…","path":"48'/0'/0'/2'"} ],
  "label":"optional", "owner":"optional" }
```
→ builds `wsh(sortedmulti(threshold, …))`, imports watch-only, returns `{ wallet, threshold, cosigners, firstAddress }`.

### GET `/api/wallets?owner=` — list registry (public metadata)
### GET `/api/wallets/:id` — one wallet's metadata
### GET `/api/wallet/:name/balance` — `getbalances`
### GET `/api/wallet/:name/receive` — fresh receive address
### GET `/api/wallet/:name/payment-request?amount=&label=&message=[&address=]` — BIP21 payment request
→ `{ address, uri, bip21, request }`. A shareable `bitcoin:` URI (amount in BTC + label + message) — the payment-request layer Bitcoin Core doesn't provide. Uses a fresh address unless `address=` is given (explicit address needs no node → air-gap-safe). See [waas-beyond-core.md](waas-beyond-core.md).
### GET `/api/wallet/:name/history?n=20` — recent transactions
### GET `/api/wallet/:name/verify?txid=…&minConf=6` — VERIFIED payment check
Verify a **received** payment against your own node. Separates **node validation** (pruned or
archival — both fully validate; `chainAccuracyPct` = verified fraction of the chain) from the
**payment verdict** (`PENDING` → `CONFIRMING` → **`VERIFIED`** at ≥6 conf). Returns a proof-grade
`record` (status, amount, block hash/height/time, **raw block tx**) to store in the ICE `.history`
for minting / further proof. → `{ ok, status, verified, confirmations, payment, nodeValidation, chainAccuracyPct, record }`
### GET `/api/wallet/:name/qr?amount=&label=&format=svg|txt|uri` — receive from QR
Scannable QR of the BIP21 payment request (via `qrencode`). `svg` (default, `image/svg+xml`) ·
`txt` (ANSI, for terminals) · `uri` (the `bitcoin:` string). Any wallet scans it to pay.
### Atomic swap (Bitcoin leg) — HTLC from Core primitives, non-custodial
- **POST `/api/swap/htlc/new`** `{claimPubkey, refundPubkey, hashHex|preimageHex, locktime}` →
  `{witnessScript, address, descriptor, watching}`. Builds an HTLC (hashlock + CLTV refund), derives
  the P2WSH funding address via `decodescript`, and imports it **watch-only** so the node can accept BTC.
- **GET `/api/swap/htlc/funding?address=`** → cheap `listunspent` check: `{funded, amountBtc, utxos, tipHeight}`.
- **POST `/api/swap/htlc/preimage`** `{preimageHex}` → `{hashHex}`. Full flow: [proposals/atomic-swap-accept-btc.md](proposals/atomic-swap-accept-btc.md).
### GET `/api/fees` — fee tiers
→ `{ ok, fees:{ fast, medium, slow, minRelay }, unit:"sat/vB", note }` (falls back client-side during IBD)

### POST `/api/wallet/:name/send` — build an UNSIGNED PSBT
```json
{ "to":"bc1q…", "amountBtc":0.001, "feeRate":8 }
```
→ `{ ok, psbt, fee, note }`. **The server never signs.**

### POST `/api/broadcast` — broadcast a signed tx
```json
{ "hex":"0200000000…" }
```
→ `{ ok, txid }`

### GET `/api/health` — chain/sync snapshot

## Console API — `http://127.0.0.1:8090`

> Complete technical + usage reference (RPC engine, circuit breaker, caching, IBD resilience,
> every endpoint, security, config): **[server.md](server.md)**.

### POST `/api/rpc` — run one whitelisted READ-ONLY RPC
```json
{ "node":"full", "method":"getblockstats", "params":[800000], "wallet":null }
```
→ `{ ok, result }`. Non-whitelisted methods → HTTP 403. Spends/stop/config-writes are never exposed.

### GET `/api/catalog` — the categorized read-only RPC catalog (44 methods)
### GET `/api/overview?node=full` — batched node snapshot for the dashboard
### GET `/api/nodes` — available node targets (`full`, `pruned`)

### rageRPC policy — see [ragebtc.md](ragebtc.md)
- **GET `/api/settings`** — live RPC policy + circuit-breaker state: `{ rpc:{ rage, maxInflight, effInflight, tier, steps, inflight, waiting, circuitOpen, distressRemainingMs }, cache, blocks, node }`.
- **POST `/api/settings`** — runtime-tune: `{ "rage":true }` | `{ "maxInflight":8 }` (>256 ⇒ RAGE) | `{ "distressMs":3000, "timeoutMs":45000 }`. Requires node control.

### Node actions (require `BANKON_NODE_CONTROL≠0`)
- **POST `/api/node/addnode`** — `{ addr, command:"add"|"onetry"|"remove" }` (node chooser).
- **POST `/api/node/promote`** — `{ addr, subver?, on? }` — favourite + persistent `addnode` (`on:false` un-promotes).
- **POST `/api/node/boot`** — `{ addr }` — `disconnectnode` (idempotent).
- **GET `/api/node/favourites`** — persisted favourites.
- **GET `/api/node/peerspeed`** · **GET `/api/node/fastnodes`** · **POST `/api/node/connect-fast`** — rank peers by block-download score / list saved fast nodes / dial the fastest toward the target.
- **GET/POST `/api/node/fastpref`** — the **fast-peer preference / rolling connector** toggle: `{ on, explore, rotate, intervalSec, floor, exploreBatch, running, last, knownTotal, indexSize }`. When on, a background loop measures live peers on a rolling index, explores fresh candidates from the whole addrman, keeps the fastest, and (security) rotates one node to the next-fastest each tick. See [ragebtc.md §3](ragebtc.md).
- **GET `/api/nodes/census`** (`?full=1`) · **POST `/api/nodes/census/refresh`** — the stored local copy of the **full addrman all-nodes list** (`{ count, updatedAt, nets }`), the canonical source feeding pgvectorscale (RAGE handoff) and Postgres (collector).

### RAGE handoff
- **GET `/api/rage/status`** — `{ target, tokenSet }`.
- **POST `/api/rage/handoff`** — `{ count }` → gathers node addresses → POSTs to pgvectorscale; `{ ok, gathered, source, stale, target, push }`.

### Blocks, oracle & sync (live during IBD — read from `debug.log`)
- **GET `/api/recentblocks?n=90`** — recent blocks `{ height, hash, time, nTx }`. `nTx` is enriched on demand from `getblockheader` (≤24/request, circuit-safe, cached) → returns `enriched` + `source`.
- **GET `/api/oracle`** — genesis time, height, all-time & windowed avg block time, protocol target, recommended poll interval.
- **GET `/api/synctip`** — last `UpdateTip`: height, progress, cumulative tx, coin cache.
- **GET `/api/coremon`** — daemon up/feeding/off (TCP port + log freshness; no RPC).

### Network
- **GET `/api/netactivity?n=200`** — enriched connection activity: per event `kind`, `peer`, `conntype`, `transport` (v1/v2), `ver`, `blocks`, `net`, `addr`, `subver`, `reason`; live peers merged in fully-detailed; plus `tally`, `nets`, `conntypes`, `transports`, `local`. Powers the Net Log tab + the Network IBD fallback ring.
- **GET `/api/nethealth`** — avg/min/max peer ping (`getpeerinfo.pingtime`, ms) + network usage (`getnettotals` totals + live in/out rate). Feeds the oracle's ping + network cards.

### Indexes
- **GET `/api/indexinfo`** — `getindexinfo` per-index state **plus `sizes`** (on-disk bytes per index dir, background `du`).
- **POST `/api/index/export-utxo`** *(node control)* — `dumptxoutset … latest` → a portable UTXO snapshot for other nodes (`loadtxoutset`/assumeUTXO). Returns `{ path, elapsedSec, result:{ base_height, base_hash, txoutset_hash, coins_written } }`. The raw indexes are **not** portable; the snapshot is. Heavy (~5–11 GB), single-flight.
- **POST `/api/blocks/export-recent`** *(node control)* — `{ sizeGb }` copies the most-recent ~N GB of block files (`blk*.dat` + `rev*.dat`) to `exports/recent-blocks/`. Returns `{ dir, files, sizeGb, range, note }`. The **recent-block slice** for rapid pruned bootstrap — **pair with the UTXO snapshot** at the base height (recent blocks can't validate alone). See [../PRUNING.md](../PRUNING.md).
- **GET `/api/exports`** — manifest of every export artifact (`{ name, kind, bytes, mtime, url }`,
  newest first; kinds: `utxo-snapshot` · `block-slice` · `checksum`).
- **GET `/exports/<file>`** — **the export service**: static read-only downloads of the artifacts,
  so *other nodes* can fetch a snapshot/slice over HTTP and bootstrap. UTXO snapshots get a
  `.sha256` sidecar on completion for integrity verification.

### Monitor, system & node lifecycle
- **GET/POST `/api/monitor`** — list / run whitelisted read-only terminal commands (fixed keys, no user input interpolated).
- **GET `/api/system`** — host CPU %, load, temperature, memory.
- **GET `/api/filesystem`** — datadir disk + files: device df, `realPath` (symlink-resolved attach
  point), per-component sizes (blocks/indexes/chainstate/total, background `du`, works with the
  node down), and `?files=1` adds the actual datadir listing (name/size/mtime).
- **POST `/api/node/start`** · **POST `/api/node/stop`** *(node control)* — launch `bitcoind -daemon` / graceful `bitcoin-cli stop`.
- **POST `/api/node/netactive`** *(node control)* — `{ on }` AIRGAP switch (`setnetworkactive`).
- **GET `/api/node/log?lines=N`** — `debug.log` tail (boot/sync stream).

## Client-side modules (not HTTP — run in the browser/Node)
- `keygen.mjs` — `generateWallet(type, strength)` → mnemonic + descriptors (PRIVATE stays local)
- `sign.mjs` — `signPsbt(mnemonic, type, psbtBase64)` → `{ signedTxHex, inputsSigned }`
