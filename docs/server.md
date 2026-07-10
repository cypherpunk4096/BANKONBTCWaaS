# BANKON Console Server — Complete Reference

`bankon-console/server.mjs` is the read-only diagnostics + node-control backend that both the
web Console (`:8090`) and the native Qt app talk to. It serves the dashboard UI, proxies a
**whitelist of read-only Bitcoin Core RPCs**, and derives live node state from `debug.log` so it
keeps working even while the node's RPC is saturated during Initial Block Download (IBD).

> **Design invariant:** no spends, no `stop` via RPC, no config writes are ever exposed through the
> RPC proxy. The only state-changing operations are a small set of explicitly **node-control-gated**
> endpoints (start/stop the daemon, peer management, network toggle, UTXO export). Everything else
> is strictly read-only. See [security.md](security.md) and [POLICY.md](../POLICY.md).

- Stack: **Express** static server + JSON API. No build step. Peer library: `../shared/security.mjs`.
- Node targets: **full archival** (`http://127.0.0.1:8332`) and optional **pruned** (`:8342`),
  selectable per request via `?node=full|pruned` or the `node` body field.
- Auth: **off by default** on localhost. Set `BANKON_API_TOKEN` to require
  `Authorization: Bearer <token>` on `/api/*` (a rate limiter is always active).

---

## 1 · Startup & configuration

```bash
# via the launcher (starts Core if needed, then WaaS + Console):
~/bankon-tools/bankon up
# or directly:
cd ~/bankon-tools/bankon-console && node server.mjs      # listens on :8090
```

The process runs as a bare `node server.mjs` with its working directory as `bankon-console/`
(this matters when finding/stopping it — match by cwd, not just `pkill -f server.mjs`, which also
hits the WaaS server).

### Environment variables

| Var | Default | Purpose |
|-----|---------|---------|
| `BANKON_CONSOLE_PORT` | `8090` | HTTP listen port |
| `BANKON_API_TOKEN` | *(unset)* | if set, requires `Authorization: Bearer` on `/api/*` |
| `BANKON_BTC_BIN` | `~/bitcoin-31.0/bin` | Bitcoin Core binary dir |
| `BANKON_BTC_DATADIR` | `~/.bitcoin` | datadir (a symlink to the external drive on this host) |
| `BANKON_NODE_CONTROL` | on (`≠0`) | gates every state-changing endpoint (start/stop/peers/export) |
| `BANKON_NODE_HAMMER` | off (`≠1`) | continuous block-burst prefetch (RPC-heavy; off during IBD) |
| `BANKON_RPC_MAX_INFLIGHT` | `256` | ceiling for concurrent RPCs (adaptive cap stays under it) |
| `RPC_TIMEOUT_MS` | `45000` | per-RPC timeout |
| `BANKON_RPC_DISTRESS_MS` | `3000` | circuit-breaker cooldown after the node reports overload |
| `BANKON_WARM_MS` | `25000` | interval for the cheap-metric cache warmer |
| `BANKON_RECENT_MAX` / `BANKON_RECENT_BUDGET` | `200` / `60` | recent-blocks retention / max fetched per burst |
| `BANKON_EXPORT_DIR` | `~/bankon-tools/exports` | where UTXO snapshots are written |
| `BANKON_PRUNED_COOKIE` | *(external path)* | cookie for the optional pruned node |
| `RAGE_URL` / `RAGE_TOKEN` / `RAGE_INGEST_PATH` | *(unset)* | rage.pythai.net handoff target (see [ragebtc.md](ragebtc.md)) |

---

## 2 · The RPC engine (rageRPC)

Every proxied RPC flows through one adaptive, self-protecting path so a busy node is never flooded:

- **Whitelist** — only methods in the catalog (`ALLOW`) are callable; anything else → **HTTP 403**.
  The catalog is 44 read-only methods across 6 categories (`GET /api/catalog`).
- **Concurrency cap** — at most `effInflight` RPCs run at once (ceiling `BANKON_RPC_MAX_INFLIGHT`,
  default 256). Excess calls queue (up to 48 waiters, then fail fast with "rpc busy").
- **Adaptive backoff** — when the node returns `work queue depth exceeded` or HTTP ≥ 500, the
  effective cap is **quartered** and a **circuit breaker** opens for `distressMs`; during that
  window callers are served cache instead of hitting the node. A sustained-healthy streak ramps
  the cap back up slowly.
- **rageRPC tiers** — `RAGE off` = gentle (cap 4), `RAGE on` = max throughput (cap 32+); toggle and
  watch the live breaker state via `GET/POST /api/settings`. Full details in [ragebtc.md](ragebtc.md).
- **Result cache** — every successful read is cached; on a miss (timeout / breaker open) the
  last-known value is served **flagged `stale`**, and a background warmer keeps common metrics warm
  by catching the node's lock gaps. This is why the dashboard never goes blank during IBD.

## 3 · IBD resilience — `debug.log`-derived endpoints

When `cs_main` is locked by validation, RPC calls time out. These endpoints read the node's
`debug.log` directly (no RPC, no lock) so the UI stays live throughout the sync:

- `GET /api/synctip` — last `UpdateTip`: height, verification progress, cumulative tx, coin cache.
- `GET /api/coremon` — daemon up/feeding/off, derived from the TCP port + log freshness.
- `GET /api/recentblocks` — recent blocks from `UpdateTip` lines (+ on-demand `nTx` enrichment).
- `GET /api/netactivity` — peer connect/disconnect/fail activity parsed from the log.

---

## 4 · Endpoint reference

Responses are `{ "ok": true, … }` or `{ "ok": false, "error": "…" }`.

### RPC proxy & catalog
| Method / path | Purpose |
|---|---|
| `POST /api/rpc` | run one whitelisted read-only RPC: `{ node, method, params, wallet }` → `{ ok, result, stale?, asOf? }`. Non-whitelisted → 403. Cached served instantly, refreshed in background. |
| `GET /api/catalog` | the 44-method categorized read-only catalog (with descriptions + example params). |
| `GET /api/nodes` | available node targets (`full`, `pruned`). |
| `GET /api/overview?node=full` | batched dashboard snapshot (fewer round-trips). |
| `GET /api/health` | fast chain/sync snapshot for the header status pill. |

### Blocks & the BTC.oracle
| Method / path | Purpose |
|---|---|
| `GET /api/recentblocks?n=90` | recent blocks (height, hash, time, **nTx**). nTx is enriched on demand from `getblockheader` — bounded to 24/request, circuit-safe, cached in `NTX` so repeats are instant. Returns `enriched` count + `source`. |
| `GET /api/oracle` | "clock on a Bitcoin block": genesis time, height, all-time & windowed avg block time, protocol target, recommended poll interval. |
| `GET /api/synctip` | live tip from `debug.log` (see §3). |
| `GET /api/coremon` | daemon liveness/feeding state (see §3). |

### Network
| Method / path | Purpose |
|---|---|
| `GET /api/netactivity?n=200` | enriched connection log: per event `kind`, `peer`, `conntype` (role), `transport` (v1/v2), `ver`, `blocks`, `net` (ipv4/ipv6/tor/i2p/cjdns), `addr`, `subver`, `reason`. Current peers from `getpeerinfo` are merged in as fully-detailed `connected` rows; a session-long peer-id→address cache backfills later disconnect lines. Returns `tally`, `nets`, `conntypes`, `transports`, `local`. |
| `GET /api/nethealth` | **average peer ping** (`getpeerinfo.pingtime`, avg/min/max ms) + **network usage** (`getnettotals` totals + live in/out **rate** derived between samples). Powers the oracle's ping + network cards. |

### Node control *(all require `BANKON_NODE_CONTROL≠0`)*
| Method / path | Purpose |
|---|---|
| `POST /api/node/start` · `POST /api/node/stop` | launch `bitcoind -daemon` / graceful `bitcoin-cli stop`. |
| `POST /api/node/netactive` | `setnetworkactive` on/off — the AIRGAP switch (`{ on: bool }`, strict-coerced). |
| `POST /api/node/addnode` | `{ addr, command: "add"|"onetry"|"remove" }` — the node chooser. |
| `POST /api/node/promote` · `POST /api/node/boot` | favourite+`addnode` / `disconnectnode`. |
| `GET /api/node/addednodes` · `/favourites` · `/peerspeed` · `/fastnodes` · `POST /connect-fast` | added-node list, favourites, speed ranking, connect-fastest. |
| `GET /api/node/log?lines=N` | tail of `debug.log` (boot/sync stream). |

### Indexes
| Method / path | Purpose |
|---|---|
| `GET /api/indexinfo` | `getindexinfo` (per-index synced/height) **plus `sizes`** — on-disk bytes per index dir (`txindex`, `coinstatsindex`, `basic block filter index`), refreshed by a background `du` (never blocks the request). |
| `POST /api/index/export-utxo` | **export a UTXO snapshot** for other nodes. Runs `dumptxoutset … latest` (no rollback → network stays online) to `BANKON_EXPORT_DIR`; returns `{ path, elapsedSec, result:{ base_height, base_hash, txoutset_hash, coins_written } }`. Heavy (~5–11 GB, minutes), single-flight, node-control-gated. **Note:** the raw indexes are *not* portable — a UTXO snapshot is the only index-derived artifact another node can load (via `loadtxoutset`, assumeUTXO). |

### Monitor, settings, system, rage
| Method / path | Purpose |
|---|---|
| `GET/POST /api/monitor` | list / run whitelisted read-only terminal commands (bitcoin-cli + `debug.log`/system), each a fixed command key (no user input interpolated). |
| `GET/POST /api/settings` | live rageRPC policy + circuit-breaker state / runtime tuning (node-control-gated). See [ragebtc.md](ragebtc.md). |
| `GET /api/system` | host CPU %, load, temperature, memory (local reads, no node RPC). |
| `GET /api/filesystem` | datadir disk usage. |
| `GET /api/rage/status` · `POST /api/rage/handoff` | rage.pythai.net node-address handoff (see [ragebtc.md](ragebtc.md)). |

---

## 5 · Security model

- **Read-only by default** — the RPC proxy whitelists 44 read methods; spends, `stop`, and
  config writes are unreachable (403). State-changing endpoints are a separate, `BANKON_NODE_CONTROL`
  gated set and never interpolate user input into shell/RPC (booleans are strict-coerced, command
  keys are fixed, addresses validated).
- **Localhost + auth** — binds localhost; a rate limiter is always on; `BANKON_API_TOKEN` adds
  bearer auth. Cookie auth to the node is read from the datadir `.cookie`.
- **Non-custodial** — the Console never touches wallet keys; wallet provisioning is the WaaS's job
  and only ever imports **watch-only** descriptors. See [architecture.md](architecture.md).

## 6 · Extending

To add a metric: add the method to the `CATALOG`/`ALLOW` whitelist (keeping it read-only), then a
render branch in the UI (`public/index.html`'s `RENDER` map for web, or a `RpcWorker` tab for Qt).
Web and Qt are **feature-peers** — keep the read-only contract identical across both.
