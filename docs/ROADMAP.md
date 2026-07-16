# BANKON — Roadmap

> This roadmap lists **tasks**. For the **why and the order** (north-star, critical path, gates),
> see **[STRATEGY.md](STRATEGY.md)**.

**BANKON is an extension module for Bitcoin Core that facilitates non-custodial
Wallet-as-a-Service (WaaS) and Bitcoin services.**

## Architecture (decided)

```
                      ┌────────────────────── BANKON module ──────────────────────┐
  CLIENT (browser /   │   WaaS API (:8088)        Multi-node manager               │
  pure Node.js)       │   • watch-only import     • full node  (archival, txindex) │
  • mints keys/seed   │   • balance / receive     • pruned node (2 GB, WaaS)        │
  • signs PSBTs       │   • build UNSIGNED PSBT    Diagnostics (scientific)         │
  • keys NEVER leave  │   • broadcast             Wallet provisioning (bulk)        │
  └───────public xpub─┴──────────────────────────────┬─────────────────────────────┘
                                                      │ JSON-RPC (cookie auth)
                                          ┌───────────┴───────────┐
                                     full node (8332)      pruned node (8342)
                                     external drive        local drive (planned)
```

**Two-node model:** the **full** node serves explorer/arbitrary-txid lookup
(`txindex`); the **pruned** node (2 GB, full validation, no txindex) is the lean
WaaS transaction backend. `prune` and `txindex` are mutually exclusive, so they
must be separate instances. Security is identical on both (both fully validate).

**Non-custodial invariant:** BANKON never holds a private key or passphrase.
Keys are minted client-side; the node imports watch-only descriptors only;
signing happens on the client via PSBT. Enforced in `server.mjs` (rejects any
request containing private fields).

## Status legend  ✅ done · 🔨 in progress · ⛔ blocked · ⬜ todo

### Phase 0 — Foundation  ✅
- ✅ Crash recovery after power loss (no corruption; clean restart)
- ✅ Full node headless + 16 peers + `dbcache=2000` (sync 38%→42%+ and climbing)
- ✅ BANKON tools scaffolded in `~/bankon-tools/`
- ✅ Non-custodial client-side keygen proven (`keygen.mjs`, audited `@scure/*`)
- ✅ WaaS API + browser UI live on `:8088`; watch-only import proven end-to-end
- ✅ Wallet-type accordion explainer (Native SegWit / Taproot / Legacy)
- ✅ Pruning size-vs-security assessment (`PRUNING.md`) + mode tool

### Phase 1 — Multi-node infrastructure  ✅ (pruned node launched; WaaS cutover after its IBD)
- ✅ Multi-node manager + pruned-config builder (`bankon-nodes.sh`)
- ✅ Prune-size control + size-vs-security analysis (`bankon-node-mode.sh`, `PRUNING.md`)
- ✅ Disk freed — root now ~62% used (**85 GB free**); pruned node placed on the local drive
- ✅ **Pruned node launched** — `~/.bitcoin-pruned` (prune=2048, `listen=0`, RPC :8342), syncing in parallel
- 🔨 Point WaaS at pruned (`BITCOIN_RPC_URL=:8342`) — after the pruned node finishes its IBD
- ⬜ *(enhancement)* assumeUTXO (`loadtxoutset`) fast-bootstrap — future

### Phase 2 — WaaS core  ✅ (live MAINNET send gated on funds + full sync)
- ✅ Client-side keygen (`keygen.mjs`, audited `@scure/*`) + watch-only register (`POST /api/wallet`)
- ✅ Client-side PSBT signing (`sign.mjs` + in-browser `signPsbtBrowser`) — `test-sign.mjs` 4/4
- ✅ Send loop: build UNSIGNED PSBT → sign locally → broadcast
- ✅ Fee estimation (`/api/fees`, sat/vB tiers) + UI selector
- ✅ Wallet registry (`registry.mjs`, multi-user via owner) + balance/receive/history endpoints
- ✅ Full loop proven by **regtest e2e** (`test-e2e-regtest.sh`); ⛔ live mainnet send needs deposited UTXO + full sync

### Phase 3 — Security & productionization  ✅
- ✅ API auth + rate limiting (`shared/security.mjs`; off unless `BANKON_API_TOKEN` set)
- ✅ No-key-material-logging audit (servers log boot banners only)
- ✅ Client-code integrity — air-gapped `offline-client.html` (zero network calls)
- ✅ Encrypted descriptor backups (`bankon-backup.sh`, AES-256/PBKDF2)
- ✅ Regtest e2e + multisig test suites (both PASS)
- ✅ Dual license (GPLv3 client crypto / MIT infra) + `POLICY.md`

### Phase 4 — Bitcoin services / enhancements  ✅ (Taproot script-path = future)
- ✅ Multisig wallets (`/api/wallet/multisig`, `wsh(sortedmulti)`; 2-of-3 regtest PASS)
- ✅ Real-time events → webhooks (`events.mjs`, polling; ZMQ = low-latency upgrade)
- ✅ Diagnostics Console (44 read-only RPCs) — explorer-grade views
- ⬜ Taproot script-path / MuSig2 multisig — future

### Phase 5 — Ops  ✅
- ✅ systemd units for node + WaaS + Console + monitor timer (`systemd/`, `install-units.sh`)
- ✅ Health watchdog (`bankon-monitor.sh` + `.timer`)
- ✅ Encrypted backup strategy (`bankon-backup.sh`)

### Phase 6 — Node intelligence & native diagnostics  ✅ (allchain multi-chain + 3D globe = future)
- ✅ **ZMQ push** — `zmqpubhashblock/rawtx/sequence` in `bitcoin.conf`; Qt `ZmqService` refreshes on each block (real-time, no polling)
- ✅ **OP_RETURN canonical anchor** — `bankon-waas/anchor.mjs` + `/api/anchor` `/api/verify`; **regtest roundtrip PASS** (`test-anchor-regtest.sh`)
- ✅ **Qt MVVM refactor** — `bankon-qt/services/*` + `adapters/*` (ChainAdapter + BitcoinCoreAdapter); routes through the Console cache with node fallback
- ✅ **Node-native network maps** — Net Map (EtherApe) + Geo Map render the whole network from `getnodeaddresses` (addrman) + GeoLite2, great-circle arcs, ASN colour — single instance, no external API
- ✅ **pgvectorscale collector** — `bankon-waas/node-collector.mjs` + `db/schema.sql`: live nodes → Postgres with geo/version/uptime + `vector(8)` embedding + StreamingDiskANN; `/api/nodes/collect` `/api/nodes/stats`
- ✅ Ops resilience — restored a deleted `bitcoind` binary (re-download + SHA256 verify); RPC **concurrency limiter** + debounced background refresh in the Console (kills the IBD work-queue flood)
- ⛔ Apply `db/schema.sql` to a live pgvectorscale Postgres + run the collector at scale — *user (needs the DB)*
- ⬜ allchain multi-chain globe (Tier 1/2/3 sources) + Qt Quick 3D / QRhi 3D globe (GPU host) — see [QT roadmap](roadmap.md)

### Phase 7 — ICE forensics, SPINTRADE, bankonOS shipping  🔨
- ✅ **🧊 ICE forensic toolkit** — geo/IP forensics, Net Map cross-link, 18-dp precision metrics,
  `.history` connectivity evidence (rotating, exportable) with **coreutils [shred(1)](https://manpages.debian.org/testing/coreutils/shred.1.en.html)**
  secure erase — 'care' = 7 overwrite passes; wipe intensity casual (default) · recommended 93% · immediate 100% CPU
- ✅ **ICE transport switches** (in 🧊 ICE + ⟲ SPINTRADE only, OS-backed shared state): VPN · ₿luetooth · Ethernet · Infrared on/off
- ✅ **ICE UI-interception shield** — app-wide event filter drops synthetic/injected input (UI automation, AT-SPI introspection);
  persistently **recommends AIRGAP** for the vectors it can't cover (OS screenshots, kernel injection). Honest scope stated in-UI
- ✅ **⟲ SPINTRADE** — chain-native trading pairs priced in SAT (SATPAY, SAT/vB book, ₿TC/BLOCK, SAT/HASH); optional module,
  default OFF, consent-gated ("innerstand"), absolute attach/detach, candle-green ON / red OFF, ICE-AIRGAP compatible.
  Swaps cryptocurrency for other assets; shortest-route locator via ICE geo. Engine: `bankon-waas/pairs.mjs` (additive)
- ✅ **Ordinal minter** — inscribe the `.history` digest for tamper-evident verification, **gas measured in SAT** from the
  local node, then **follow the transaction on the ₿itcoin network from your own node** (mempool → confirmations, no explorer)
- ✅ **Exit hygiene** — exit is fast; recommends wiping the public `.history` before leaving, clears the RPC cache completely,
  and scrubs any private-key/signature material from memory (`scrub_memory`)
- ✅ Retention: `.history` 5 MB × 20 = 100 MB ceiling (tunable); **price collection is separate public storage** (`.pricehistory`)
- ⬜ **bankonOS shipping** — physical builds of the OS with the blockchains as a service
  (Bitcoin Core + BANKON WaaS pre-provisioned), on a **power-of-two GB ladder**:

  | Build | Capacity | Decimal | Tier | Carries |
  |---|---|---|---|---|
  | **1024 GB** | 1 TiB | 1.10 TB | proto | chain (binding constraint) |
  | **2048 GB** | 2 TiB | 2.20 TB | production | chain + headroom |
  | **4096 GB** | 4 TiB | 4.40 TB | data proto | chain + local index (~$666 tier) |
  | **8192 GB** | 8 TiB | 8.80 TB | pgvectorscale | chain + **rageBTC on-box** — eyes on 10 TB drives (9,313 GiB usable → the 8192 build fits with ~1.1 TiB spare) |

  The 8192 build is the fully **AIML-enhanced `.bitcoin` search** appliance: the chain exporter's
  pgvectorscale store + the **/rage retrieval tool** (`bankon-waas/rage-search.mjs`) ship on-box.
  **Partition isolation:** the **AI partition** (pgvectorscale + RAGE) is contained on **OpenBSD**,
  separated from the **blockchain partition** (Bitcoin Core datadir) — OpenBSD's pledge/unveil +
  separate slices keep the node and the model store from reaching into each other. The Qt console's
  disk-runway diagnostic is the fit gauge.

  **Seed order — three OS targets, all with PostgreSQL** (pgvectorscale runs on each):
  1. **Debian** — broadest hardware/driver support; the reference seed.
  2. **Alpine** — minimal, hardened, small footprint; the lean production image.
  3. **OpenBSD** — the isolation target: hosts the AI partition apart from the blockchain partition.

  Provisioning via `bankonos/` (POSIX `sh`, per-OS `pkgname()` map, verified installs) seeds the
  builds in that order.
- ✅ **/rage tool restored** — `bankon-waas/rage-search.mjs`: the retrieval side of RAGE (semantic
  vector kNN + structured SQL over the exported `.bitcoin` data); standalone `:8095/rage`, dry-run
  without a DB. Ingest side is `ragest-server.mjs`; both read the same pgvectorscale schema.
- ⬜ WaaS as a **standalone dapp**, compatible with and **dockable into** the BANKON ₿TC console (feature-peer to the Qt UI)

## Module inventory (`~/bankon-tools/`)
| File | Role | State |
|------|------|-------|
| `bankon` | launcher — up/status/stop/doctor/install-core/qt | running |
| `bankon-waas/` | WaaS API (:8088) + UI; `keygen.mjs`, `sign.mjs`, `registry.mjs`, `events.mjs`, `rpc.mjs`, `anchor.mjs`, `offline-client.html` | running |
| `bankon-waas/node-collector.mjs` + `db/` | live-node → Postgres/pgvectorscale collector (geo/version/uptime + embedding) | built; needs a DB |
| `bankon-waas/test-*.mjs` / `test-*-regtest.sh` | signing + e2e + multisig + **anchor** tests | all PASS |
| `bankon-console/` | progressive-disclosure diagnostics dashboard (44 RPCs); cache + concurrency limiter | running on :8090 |
| `bankon-qt/` (`bankon_qt.py`, `services/`, `adapters/`) + `bankon-qt.sh` | native Qt diagnostics UI — MVVM, ZMQ push, EtherApe Net Map, node-native Geo Map | running ([reference](QTbankonQT.md)) |
| `bankon-nodes.sh` · `bankon-node-mode.sh` | multi-node manager + prune-size control | built; pruned launch ⛔ disk |
| `bankon-diag.sh` · `bankon-monitor.sh` | diagnostics + health watchdog | built |
| `bankon-backup.sh` | encrypted registry backup (AES-256) | tested |
| `bankon-wallet.sh` | custodial bulk creator (admin only) | built |
| `shared/security.mjs` | API auth + rate limiting | wired into both servers |
| `systemd/` | service + timer units + installer | built |
| `README.md` · `POLICY.md` · `LICENSE*` · `PRUNING.md` | landing + licensing + pruning | done |
| `docs/` | guide set incl. [NAV](NAV.md), [QTbankonQT](QTbankonQT.md), [QT roadmap](roadmap.md), ROADMAP, TODO, api, wallets, security, operations, + `qt/` design guides | done |
| `~/.claude/commands/bankonbtc.md` | **BANKONBTC skill** (Core + frontend + Qt) | installed |
