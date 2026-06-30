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
  hitting the node — this *drains* Core's queue and stops the flood at the source.

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

## 3. RAGE handoff — node intelligence → pgvectorscale (`bankon.pythai.net/ragest`)

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

## 4. Where it lives
- **rageRPC controller** — Console **RPC Console** tab (toggle + tier selector + live breaker
  readout) and the Qt **RPC Console** tab.
- **Server** — `bankon-console/server.mjs` (`RPC`/`RAGE_PROFILES`, `rpc()`, `/api/settings`,
  `/api/rage/*`).
- **Reference ingest handler** — `bankon-waas/ragest-server.mjs`.
- **Config** — `bankon.env` (`BANKON_RAGE`, `RAGE_URL`, `RAGE_INGEST_PATH`, `RAGE_TOKEN`, ceilings).

## Invariants
- **Resilience first** — the adaptive floor (2) + breaker mean even RAGE never takes the node down;
  it self-throttles and serves cache.
- **Honest degradation** — handoff and views fall back (addrman → peers → cache) and *say so*
  (`stale`, `source_rpc`, `circuitOpen`) rather than faking data.
- **Non-custodial / read-only** — RAGE only ever moves *public* network metadata; no keys, no spends.
