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
### GET `/api/wallet/:name/history?n=20` — recent transactions
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
- **GET `/api/node/peerspeed`** · **GET `/api/node/fastnodes`** · **POST `/api/node/connect-fast`** — rank by speed / connect fastest.

### RAGE handoff
- **GET `/api/rage/status`** — `{ target, tokenSet }`.
- **POST `/api/rage/handoff`** — `{ count }` → gathers node addresses → POSTs to pgvectorscale; `{ ok, gathered, source, stale, target, push }`.

### GET `/api/netactivity?n=50` — log-based connection activity (powers the Network IBD fallback ring)

## Client-side modules (not HTTP — run in the browser/Node)
- `keygen.mjs` — `generateWallet(type, strength)` → mnemonic + descriptors (PRIVATE stays local)
- `sign.mjs` — `signPsbt(mnemonic, type, psbtBase64)` → `{ signedTxHex, inputsSigned }`
