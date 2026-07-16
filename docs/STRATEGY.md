# BANKON — Strategy

**North-star: ship a usable, non-custodial Bitcoin Wallet-as-a-Service on a single self-hosted
Bitcoin Core — then expand.** Every other thread (multi-chain `allchain`, the pgvectorscale data
product, the 3D globe) sequences *behind* a proven Bitcoin core. *"The wallet you can BANKON."*

Roadmaps list **tasks**; this lists **why and in what order**. Companions:
[ROADMAP](ROADMAP.md) · [TODO](TODO.md) · [QT roadmap](roadmap.md) · [WaaS accuracy](waas-accuracy.md).

---

## 1. Where we are (honest state)

**Proven (works end-to-end):** client-side keygen (BIP39/32), client-side PSBT signing, watch-only
descriptor registration, **regtest e2e send** + **2-of-3 multisig** + **OP_RETURN anchor** (all
regtest-PASS), the non-custodial reject guard, ZMQ push, IBD-resilient diagnostics (cache/log/
concurrency-limited), and node-native network maps.

**Built but gated** (mechanism done; waiting on an external dependency):
- Live **mainnet send** — needs deposited funds + full sync.
- **Pruned WaaS node** (`:8342`) — was blocked on disk; **now unblocked (85 GB free)**.
- **pgvectorscale collector** — needs a Postgres with the `vectorscale` extension.
- Qt **3D globe** (Qt Quick 3D) — needs a GPU host (this box is software-rendered).

**Aspirational** (architecture exists in `docs/qt/`; user supplies the proprietary pieces):
`allchain` multi-chain, EVM/Foundry, Algorand/x402, SATPAY bridge, the PARSEC shell.

**Shipped since (Phase 7):** the 🧊 ICE forensic toolkit (evidence `.history` with
[shred(1)](https://manpages.debian.org/testing/coreutils/shred.1.en.html) secure erase and
memory scrub on exit), ICE transport switches, ⟲ SPINTRADE (chain-native pairs in SAT, opt-in +
consent-gated), and the ordinal minter that follows its own transaction from the local node.
See [ROADMAP §Phase 7](ROADMAP.md) and the one proposal doc
[proposals/btc-pairs-spintrade.md](proposals/btc-pairs-spintrade.md) — details live there, not
duplicated here.

## North-star extension: bankonOS as a shippable product

The endgame is **bankonOS** — a tight OS with **the blockchains as a service**, shipped on
physical media: a **1 TB proto** build and a **2 TB production** build, each pre-provisioned with
Bitcoin Core + the BANKON WaaS module. The 1 TB device is the *binding constraint* (the chain is
the payload); 2 TB is production headroom. This reframes the storage tiers the Qt console already
measures (disk-runway diagnostic) as the product's fit gauge. In this frame the **WaaS is a
standalone dapp** — compatible with, and dockable into, the BANKON ₿TC console (a feature-peer to
the Qt UI, per the read-only/​non-custodial invariants). Provisioning is `bankonos/`
(Alpine/OpenBSD-first, verified installs).

---

## 2. Critical path → "Shipped Bitcoin WaaS"

Four gates, in order. Current node: **~43.6% synced** (height ~662k).

| Gate | Goal | Blocker / dependency | Done when |
|------|------|----------------------|-----------|
| **G1** | **Full sync** (IBD complete) | Slow external HD; avoid restart interruptions (the binary was even deleted once this session) | `verificationprogress ≈ 1.0` |
| **G2** | **Pruned node** (`:8342`) as the lean WaaS backend | ✅ **LAUNCHED** — running on local drive (`~/.bitcoin-pruned`, `listen=0`, syncing) | WaaS cutover to `:8342` once it finishes IBD |
| **G3** | **Funded mainnet send** | a deposited UTXO (user) | one real non-custodial send confirmed |
| **G4** | **Productionize** | — (mostly built) | survives reboot; watchdog green; first-run UX |

**G1 strategy:** let it run uninterrupted; minimize node restarts; keep a SHA256-verified `bitcoind`
binary backup (after the deleted-binary incident); evaluate `assumeutxo` (`loadtxoutset`) for a fast
chainstate bootstrap if sync time is unacceptable.
**G2 strategy:** ✅ **done — the pruned node is launched** (`~/.bitcoin-pruned`, `prune=2048`, `listen=0`
outbound-only since the full node already holds the standard P2P :8333 / RPC :8332; the pruned node
takes P2P :8334 / RPC :8342) and is syncing in parallel with G1.
(`prune` and `txindex` are mutually exclusive → the full archival node keeps `txindex`; the pruned
node is the lean WaaS tx backend.) Remaining: once it finishes IBD, cut the WaaS over with
`BITCOIN_RPC_URL=http://127.0.0.1:8342` + the pruned cookie.
**G3 strategy:** the loop is proven on regtest; on mainnet deposit a small UTXO → build unsigned PSBT
→ client-sign → broadcast → confirm.
**G4 strategy:** systemd units + `bankon-monitor` + encrypted backups already exist; add a clean
first-run experience and a final no-key-logging/auth review.

**Definition of shipped:** on mainnet, a user creates a wallet (keys minted client-side), receives,
and sends **non-custodially** through the UI, served reliably by the pruned node.

---

## 3. Strategic principles (invariants — never traded away)

- **Non-custodial always** — keys/seed live client-side; the server rejects any secret (HTTP 400).
- **Single Core, no external-API dependence** — node-native (`getnodeaddresses`, `debug.log`), so it
  works offline and survives third-party outages (bitnodes.io is down — we don't need it).
- **Resilience-first** — serve cache/log instantly, concurrency-limit RPC, degrade *honestly* during IBD.
- **Clean-house Qt** — no CesiumJS / QtWebEngine / Tauri (those are the separate PARSEC shell).
- **Honest accuracy** — always label proven vs built-gated vs aspirational ([waas-accuracy](waas-accuracy.md)).
- **Hardware-aware** — software-rendered host → QtWidgets + CPU globe; GPU features are explicitly gated.

---

## 4. After ship — long-term phases (each gated on its dependency)

- **Phase II — Node-intelligence product.** Apply `bankon-waas/db/schema.sql` to a live pgvectorscale
  Postgres; run `node-collector.mjs` at scale; expose geo/version/uptime + vector-similarity (ANN)
  analytics. *Gate: a Postgres + `vectorscale`.* (Highest-leverage next — the data layer is already built.)
  The feed is **RAGEbtc** (see [ragebtc.md](ragebtc.md)): the **rageRPC** controller already keeps the
  node readable at max throughput during IBD, and `POST /api/rage/handoff` already gathers addrman/peers
  → the **RAGE** (Retrieval-Augmented Generative Engine) ingest at `bankon.pythai.net/ragest`. So Phase II
  is mostly *standing up the sink* (the engine lives at [rage.pythai.net](https://rage.pythai.net) ·
  [github.com/gaterage](https://github.com/gaterage)) and pointing the existing handoff at it, then ANN
  on top — and downstream the permaweb bridge anchors snapshots to Arweave.
- **Phase III — `allchain` multi-chain.** Parse `allchain.html` → Tier 1/2/3 node-source adapters →
  `GlobeNode`/`GlobeEdge` → cross-chain globe. *Gate: the proprietary registry.*
- **Phase IV — 3D globe + PARSEC split.** Qt Quick 3D behind the swappable `GlobeView` on a GPU host;
  PARSEC (Tauri/Cesium) stays a separate shell with zero shared code. *Gate: GPU host.*
- **Phase V — Settlement rails.** Algorand/x402 (USDC), EVM/Foundry (mainnet-only, no admin keys),
  SATPAY bridge — per the [Master Architect Guide](qt/Qt%206%20Desktop%20Wallet%20with%20Bitcoin%20Core%20Anchor%20for%20BANKON%20Chain-Agnostic%20WaaS_%20Master%20Architect%20Guide.md). *Gate: contracts + facilitators.*

---

## 5. Risks & mitigations

| Risk | Mitigation |
|------|------------|
| IBD time on slow external HD | run uninterrupted; consider `assumeutxo` fast-bootstrap |
| Single point of failure (binary was deleted once) | keep a SHA256-verified `bitcoind` backup; restore proven |
| RPC flood during IBD | concurrency limiter + debounced refresh + log-based sync (**done**) |
| Scope sprawl across threads | **north-star discipline** — Bitcoin WaaS first; everything else queues |
| Proprietary unknowns (`allchain.html`, PARSEC) | user-supplied, isolated behind adapters/registry |

---

## 6. What needs the user (decision points)

- **G3:** deposit a small mainnet UTXO for the first live send.
- **Phase II:** provide a pgvectorscale Postgres (`DATABASE_URL`) **and** the RAGEbtc ingest endpoint +
  `RAGE_TOKEN` (rage.pythai.net/`ragest`) so the existing `/api/rage/handoff` has a sink.
- **Phase III:** provide `allchain.html` (the chain registry).
- **Phase IV:** a GPU-capable host for the Qt Quick 3D globe.
- *(G2 no longer needs the user — disk is now free; the pruned node can be launched.)*

---

## 7. Scorecard

| Item | Status | Blocking dependency | Definition of done |
|------|--------|---------------------|--------------------|
| Bitcoin WaaS loop | ✅ proven (regtest) | — | regtest e2e + multisig PASS |
| OP_RETURN anchor | ✅ proven (regtest) | — | anchor→verify(true/false) PASS |
| Full sync (G1) | 🔨 ~43.6% | sync time | progress ≈ 1.0 |
| Pruned node (G2) | 🔨 **launched, syncing** | — (running, local drive) | WaaS cut over after its IBD |
| Live mainnet send (G3) | ⛔ gated | funds + sync | one confirmed send |
| Productionize (G4) | 🔨 mostly built | — | reboot-survivable |
| Node-intelligence (II) | ⬜ built | pgvectorscale DB | collector running, ANN queries |
| allchain (III) | ⬜ designed | `allchain.html` | multi-chain globe |
| 3D globe (IV) | ⬜ designed | GPU host | Qt Quick 3D backend live |
| Settlement rails (V) | ⬜ designed | contracts | x402/EVM/SATPAY adapters |
