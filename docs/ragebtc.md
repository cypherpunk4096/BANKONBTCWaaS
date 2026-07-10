# RAGEbtc — rageRPC + the Retrieval-Augmented Generative Engine (Bitcoin)

**RAGEbtc** is the Bitcoin-chain RAGE: two complementary pieces on the same data path. (The name is
chain-scoped — the EVM/Algorand twins get their own.)

1. **rageRPC** — an *accelerated, self-protecting* RPC controller that pushes a healthy Bitcoin
   Core as hard as it safely can (max IBD/diagnostics throughput) while never flooding the work
   queue. It keeps the Console/Qt views live during a heavy initial block download (IBD). **rageRPC**
   is the controller's name throughout the UI and the code.
2. **RAGE handoff** — *Retrieval-Augmented Generative Engine.* A web2→web3 bridge that gathers the
   node's view of the network (addrman / peers) and hands it off to **pgvectorscale**, the seed of
   BANKON's node-intelligence dataset (and, downstream, the Arweave permaweb bridge).

> One-line: *rageRPC feeds the views; the RAGE engine feeds the dataset.*

**Project references:** RAGE service — <https://rage.pythai.net> · source —
<https://github.com/gaterage>. (The Console's configured ingest target defaults to
`bankon.pythai.net/ragest`, overridable in `bankon.env`.)

---

## 1. rageRPC — the accelerated RPC controller

A running node under IBD holds `cs_main` while it validates, so naïve polling piles up until Core
returns **"Work queue depth exceeded"** and the dashboard goes blank. rageRPC solves this with a
**concurrency limiter + adaptive cap + circuit breaker**, so you can run at max throughput *and* stay
resilient.

### Throttle tiers (powers of two)
The controller (RPC Console → rageRPC panel, or `POST /api/settings`) selects an in-flight ceiling
from a power-of-two ladder, plus an uncapped RAGE step:

| Tier | In-flight ceiling | Role |
|------|-------------------|------|
| **1 · 2 · 4** | 1–4 | **resilient floor** — always safe, even mid-validation |
| **8** | 8 | **standard** |
| 16 · 32 · 64 · 128 | 16–128 | faster, as the node frees up |
| **256** | 256 | **max** (the BANKON default) |
| **RAGE** | uncapped (`RAGE_CEIL`, default 100 000) | no upper limit — hardware-bound |

Steps: `[1, 2, 4, 8, 16, 32, 64, 128, 256]` + RAGE. Default tier is **256**; RAGE is opt-in via the
controller (any value > 256 flips `rage:true`).

### Why max is safe — three layers of protection
- **Concurrency limiter** — never more than `effCap()` calls in flight; excess queues (max 48
  waiters, then fast-fails with "rpc busy" so a held thread is freed rather than blocking).
- **Adaptive cap** (`_effInflight`) — on distress it **quarters** the live cap (sticky), floor **2**;
  when sustained-healthy it **ramps +1 every 25 OK calls** back toward the ceiling. So even **RAGE
  degrades to a floor of 2 during IBD** and only reaches the high ceiling when the node is genuinely
  healthy. Resilience is maintained "from 1 and 2 and 4."
- **Circuit breaker** (`_distressUntil`) — when Core returns ≥500 or "work queue depth exceeded", the
  breaker **opens for `distressMs`** (cooldown). While open, callers are served from cache instead of
  hitting the node — this *drains* Core's queue and stops the flood at the source. The breaker uses
  **exponential backoff + jitter** (repeated trips escalate the cooldown up to 60 s, staggered so the
  herd doesn't re-trip in lockstep) and heals the escalation as health persists. When the window
  expires the next calls are **half-open probes** — a success recovers, another failure re-trips.
- **Timeout-aware backoff** — a *timeout* (slow node, not a 500) is soft distress: the cap is trimmed
  ~25 % and the OK-streak reset, so the engine eases off a struggling node without the full cut.
- **Request coalescing** — concurrent identical cache-miss calls share **one** node call (single-flight),
  collapsing the dashboard's many-widgets-same-method bursts.
- **Faster AIMD recovery** — the cap grows +1 per `RECOVER_EVERY` (8) sustained OKs (was 25), so a
  transient trip no longer depresses throughput for thousands of calls, while staying AIMD-stable.
- **Observability** — `/api/settings` reports live `stats:{ calls, errors, trips, timeouts,
  coalesced, servedStale }`, `tripCount`, and `coalescing` alongside the breaker/cap state.

### Node-side headroom
rageRPC pairs with a node tuned to absorb bursts (written by `bankon optimize` / `bankon up`):
```
rpcworkqueue=256
rpcthreads=16
```
(applies on the node's next restart). Together: a deep queue to absorb bursts + a client that backs
off the instant the queue is genuinely overwhelmed.

### Profiles
| Profile | maxInflight | distressMs | When |
|---------|-------------|------------|------|
| **on** (rage) | 32 baseline → up to ceiling | 3000 | max throughput (default posture) |
| **off** (gentle) | 4 | 12000 | quiet background polling |

`bankon.env`: `BANKON_RAGE=1` (default) selects the accelerated posture; `0` selects gentle.

---

## 2. Findings from settings — `GET /api/settings`

The Console exposes the **complete live policy + breaker state** so the controller can display exactly
what the engine is doing right now. `settingsSnapshot()` returns:

```jsonc
{
  "rpc": {
    "rage": false,            // RAGE (uncapped) engaged?
    "maxInflight": 256,       // configured ceiling (the selected tier)
    "effInflight": 256,       // ADAPTIVE live cap — drops to ≥2 under distress, ramps back up
    "tier": 256,              // "RAGE" when uncapped, else the numeric ceiling
    "steps": [1,2,4,8,16,32,64,128,256],
    "timeoutMs": 45000,       // per-call timeout
    "distressMs": 3000,       // circuit-breaker cooldown
    "inflight": 0,            // calls in flight right now
    "waiting": 0,             // queued waiters
    "circuitOpen": false,     // breaker tripped?
    "distressRemainingMs": 0  // ms left on the current cooldown
  },
  "cache":  { "bgRefreshMinMs": …, "warmCheapMs": 25000, "chainTimeoutMs": … },
  "blocks": { "recentMax": …, "recentBudget": … },
  "node":   { "control": true, "hammer": false, "datadir": "…/.bitcoin" }
}
```

**What the fields tell you at a glance:**
- `effInflight < maxInflight` → the adaptive cap has **backed off**; the node is/was under load.
- `circuitOpen:true` with `distressRemainingMs > 0` → the breaker is **draining Core's queue**; views
  are being served from cache (this is the system working, not failing).
- `inflight`/`waiting` → real-time saturation; sustained `waiting` means you're at the ceiling.
- `tier:"RAGE"` → uncapped ceiling engaged (adaptive floor still protects you).

### Tuning at runtime — `POST /api/settings`
```bash
# engage RAGE (uncapped)
curl -s -X POST :8090/api/settings -d '{"rage":true}'  -H content-type:application/json
# pick an exact tier (1..256; >256 flips RAGE)
curl -s -X POST :8090/api/settings -d '{"maxInflight":8}' -H content-type:application/json
# adjust breaker cooldown / per-call timeout
curl -s -X POST :8090/api/settings -d '{"distressMs":3000,"timeoutMs":45000}' -H content-type:application/json
```
A manual change **resets the adaptive cap** to the new ceiling and clears the OK-streak. Requires node
control (`BANKON_NODE_CONTROL≠0`).

Env overrides (no API needed): `BANKON_RPC_MAX_INFLIGHT`, `BANKON_RPC_DISTRESS_MS`, `RPC_TIMEOUT_MS`,
`BANKON_RAGE_CEIL`.

---

## 3. Fast-peer preference — maximize block-download speed

rageRPC keeps the *views* fast; **fast-peer preference** keeps *block download* fast by biasing the
node's peer set toward the fastest block-serving peers. It's an **on/off toggle** (off by default).

### Peer scoring (block-download-relevant)
`_score(peer)` ranks peers by what makes them good at serving blocks quickly, not just raw bytes:
low **minping** latency, a **tip ahead of us**, **compact-block high-bandwidth** relay
(`bip152_hb_from`), a **block-serving connection type** (outbound-full-relay / block-relay-only), and
**blocks currently in flight** from the peer. Higher = faster + more useful. Powers
`GET /api/node/peerspeed` (ranked list) and `GET /api/node/fastnodes` (the persisted fast set).

### The rolling connector — `POST /api/node/fastpref { on }`
When **on**, a background loop (default every 90 s) continuously measures live peers on a **rolling
index** and keeps the fastest. All actions are net-layer (direct `bitcoin-cli
addnode`/`disconnectnode`/`onetry`, so they work during IBD when the RPC breaker is open):

1. **Measure & keep the fastest** — re-score connected peers each tick and persist the fast ones into
   the rolling index (`fast-nodes.json`), stamped with a last-seen; the index is trimmed to the
   freshest `keep` (default 200), so it stays "the fastest, recently measured."
2. **Top up** — `addnode add` the best saved fast nodes we aren't connected to (sticky across restarts).
3. **Explore from all** — sample fresh candidates from the **entire addrman** (`getnodeaddresses 0`)
   and dial a batch (`addnode … onetry`, default 8/tick) to *measure* them; the fast ones enter the
   index next tick, the rest fall away. This is the rolling probe that keeps discovering faster peers
   across the whole known network rather than only re-ranking the ones you already have.
4. **Gentle prune** — drop the **single** slowest **non-favourite outbound** peer, and only when
   above a peer-count **floor** (default 12) and the peer is clearly slow (minping > 1 s or negative
   score) and **not currently serving blocks**. Favourites and inbound peers are never touched.
5. **Security rotation** (`rotate`, default on) — **no matter how many nodes are configured**, once the
   set is all-fast the loop still churns **one** node every tick: it drops a *random* non-favourite
   outbound peer and dials the **next-fastest** index node not currently connected. So the peer set is
   never a fixed, fully-known group — an extra layer of resistance against a coordinated fast-peer /
   **eclipse** attempt. `last.rotated` reports it.

**Network-scale readout (actual).** The loop reports `knownTotal` — the **actual** number of nodes
**our** addrman knows (tens of thousands; e.g. ~68 k here, a superset that includes stale/unreachable
addresses, far more than the ~24 k *reachable* set). No hardcoded reference — it's read live. `last`
reports each tick's actions: `{ peers, promoted, toppedUp, explored, dropped, rotated, indexSize, knownTotal }`.

### Local node census — the full all-nodes list, stored & DB-ready
`_refreshCensus()` keeps a **stored, updatable local copy of the entire addrman** (`node-census.json`,
auto-refreshed every 30 min and on the explore tick). It's the local canonical copy that feeds **every
BANKON database option**: pushed to **pgvectorscale** via the RAGE handoff (§4) and collected into
**Postgres** by the WaaS node-collector.
```bash
curl -s :8090/api/nodes/census                 # { count, updatedAt, nets:{ipv4,ipv6,tor,…} }
curl -s :8090/api/nodes/census?full=1          # + the full nodes[] list
curl -s -X POST :8090/api/nodes/census/refresh # force an update now
```

```bash
curl -s :8090/api/node/fastpref                                   # state: { on, intervalSec, floor, running, last }
curl -s -X POST :8090/api/node/fastpref -d '{"on":true}'  -H content-type:application/json   # engage
curl -s -X POST :8090/api/node/fastpref -d '{"on":false}' -H content-type:application/json   # disengage
# tune cadence / floor:
curl -s -X POST :8090/api/node/fastpref -d '{"intervalSec":60,"floor":16}' -H content-type:application/json
```
`last` reports the previous tick's actions: `{ peers, promoted, toppedUp, dropped }`.

### Where the toggle lives
- **Qt → Network tab:** the **⚡ prefer fastest** checkbox (beside the peer-target selector and
  ⚡ Grow now). The checkbox reflects the server's persisted state on open.
- **Manual dial** stays available: **⚡ Grow now** / `POST /api/node/connect-fast` dials the fastest
  saved peers (or DNS-seed bootstrap) toward the target immediately — a one-shot version of the loop.
- **Node-side headroom for parallel block fetch:** more outbound connections = more parallel block
  download. `addnode` peers are additional manual slots that serve blocks; for enterprise peer counts,
  size `-maxconnections` up and open inbound `:8333`. Faster IBD also benefits from a large `dbcache`
  (set by `bankon optimize`) and the default `assumevalid` checkpoint.

Requires node control (`BANKON_NODE_CONTROL≠0`). Off by default — turn it on only when you want the
node actively managing its peer set for speed.

---

## 4. RAGE handoff — node intelligence → pgvectorscale (`bankon.pythai.net/ragest`)

The **Retrieval-Augmented Generative Engine** turns the node's own view of the p2p network into a
dataset. `POST /api/rage/handoff` gathers live node addresses and pushes them to the ingest endpoint.

- **Source**: `getnodeaddresses` (addrman snapshot, breaker/adaptive-protected, cache-aware). If
  addrman is choked, it **falls back to `getpeerinfo`** (connected peers, warm cache) — so a handoff
  still works during IBD. Degrades honestly if there's no data at all.
- **Target**: `RAGE_URL + RAGE_INGEST_PATH` → default `https://bankon.pythai.net/ragest`.
- **Auth**: `Authorization: Bearer <RAGE_TOKEN>` when set.
- **Payload**: `{ source:"bankon", kind:"btc-nodes", source_rpc, stale, count, nodes:[{address,port,services,time,network}] }`.

```bash
curl -s :8090/api/rage/status                                  # { target, tokenSet }
curl -s -X POST :8090/api/rage/handoff -d '{"count":500}' -H content-type:application/json
# → { ok, gathered, source, stale, target, push:{ ok, status, response } }
```

### Config (`bankon.env`)
```ini
RAGE_URL=https://bankon.pythai.net
RAGE_INGEST_PATH=/ragest
RAGE_TOKEN=               # bearer token for the ingest endpoint (never commit a real one)
```

### Downstream
The ingest endpoint loads the nodes into **pgvectorscale** (ANN over node geo/version/uptime — see
[../bankon-waas/db/README.md](../bankon-waas/db/README.md)). From there the **permaweb bridge**
(`bankon-waas/permaweb-bridge/`, `bankon permaweb`) can snapshot pgvectorscale → **Arweave** as signed
ANS-104 data-items — the web2→web3 permaweb path. The RAGE engine and its dataset live at
**[rage.pythai.net](https://rage.pythai.net)** (source: **[github.com/gaterage](https://github.com/gaterage)**).

---

## 5. Peer-target control (powers of two)

The Qt **Network tab** peer-target control accepts **any number**, but its **▲/▼ arrows jump in
powers of two** (8 → 16 → 32 → 64 …; down halves). Type `12` if you want exactly 12; step up and it
snaps to 16, down to 8. The target is a connection **floor**, not a cap — exceeding it is healthy.
`auto-grow` dials toward it with `connect-fast`; **⚡ prefer fastest** (§3) keeps the set fast once there.

## 5b. Chain exporter — the whole blockchain → pgvectorscale

RAGE handoff ships the *network view*; the **chain exporter** ships the **chain itself** — every
block + transaction + input + output + address — into a pgvectorscale DB searchable by **SQL and
vector similarity**. It extracts via `getblock` verbosity 3 (exact fees + input addresses, no extra
RPC) through the rageRPC `rpc()` engine, enforces a per-block accuracy invariant, writes with a
**COPY** path (staging + idempotent upsert), and advances a **height checkpoint** only on COMMIT →
fully **resumable**. Dry-run when no `DATABASE_URL`.

```bash
export DATABASE_URL='postgresql://…'                                   # output-location control (unset ⇒ dry-run)
psql "$DATABASE_URL" -f bankon-waas/db/schema-chain.sql
curl -s -X POST :8090/api/chain/export -d '{"resume":true}' -H content-type:application/json   # fire-and-poll
curl -s :8090/api/chain/export/status        # { running, height, tip, pct, rateBlkS, etaSec, blocksDone, txDone }
curl -s -X POST :8090/api/chain/export/stop  # pause; checkpoint persists → restart continues
curl -s ':8090/api/chain/export/verify?from=&to='   # DB tx-counts vs getblockstats
```
Scale honesty: full chain ≈ **1.1B txs / 1–2 TB / days-to-weeks** — prove it on a bounded window
first. Tables/embeddings/queries + the full workflow are in the **[RAGE skill](../../.claude/commands/rage.md)**
(`rage`). Files: `bankon-console/chain-exporter.mjs`, `bankon-waas/db/schema-chain.sql`.

## 6. Where it lives
- **rageRPC controller** — Console **RPC Console** tab (toggle + tier selector + live breaker
  readout) and the Qt **RPC Console** tab.
- **Fast-peer preference** — Qt **Network tab** (⚡ prefer fastest) + `POST /api/node/fastpref`.
- **Server** — `bankon-console/server.mjs` (`RPC`/`RAGE_PROFILES`, `rpc()`, `_score()`, `/api/settings`,
  `/api/node/peerspeed|fastnodes|connect-fast|fastpref`, `/api/rage/*`).
- **Chain exporter** — `bankon-console/chain-exporter.mjs` + `/api/chain/export*` endpoints;
  schema `bankon-waas/db/schema-chain.sql`; full docs in the **`rage` skill**.
- **Reference ingest handler** — `bankon-waas/ragest-server.mjs`.
- **Config** — `bankon.env` (`BANKON_RAGE`, `RAGE_URL`, `RAGE_INGEST_PATH`, `RAGE_TOKEN`, ceilings) ·
  persisted state files: `fast-nodes.json`, `favourite-nodes.json`, `fastpref.json`.

## Invariants
- **Resilience first** — the adaptive floor (2) + breaker mean even RAGE never takes the node down;
  it self-throttles and serves cache.
- **Honest degradation** — handoff and views fall back (addrman → peers → cache) and *say so*
  (`stale`, `source_rpc`, `circuitOpen`) rather than faking data.
- **Non-custodial / read-only** — RAGE only ever moves *public* network metadata; no keys, no spends.
