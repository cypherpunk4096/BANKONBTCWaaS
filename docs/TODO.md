# BANKON — TODO (prioritized, actionable)

Companion to `ROADMAP.md`. `[ ]` todo · `[~]` in progress · `[x]` done · `[!]` blocked

## Multi-node (Phase 1) — pruned node launched
- [x] Disk freed (root ~62% used, 85 GB free)
- [x] Pruned node launched: `~/.bitcoin-pruned` (prune=2048, `listen=0`, RPC :8342), syncing
- [~] Point WaaS at pruned (`BITCOIN_RPC_URL=http://127.0.0.1:8342`, `BITCOIN_COOKIE=~/.bitcoin-pruned/.cookie`) — after pruned IBD completes
- [~] `bankon-nodes.sh status` both green — full synced + pruned still in IBD

## Blocked on user (external — cannot complete autonomously)
- [!] Live MAINNET send with real funds (needs deposited UTXO + full sync) — mechanism already proven on regtest

## WaaS core (Phase 2)
- [x] Client-side PSBT **signing** in browser (`sign.mjs` + in-page `signPsbtBrowser`)
- [x] Send flow wired: build unsigned PSBT (node) → sign locally → broadcast
- [x] Signing roundtrip test (`test-sign.mjs`): segwit + taproot + negative test, all pass
- [x] Live end-to-end with real funds — **regtest e2e PASS** (`test-e2e-regtest.sh`): fund → PSBT → client-sign → broadcast → confirm
- [x] Fee estimation endpoint (`/api/fees`, sat/vB tiers) + UI selector
- [x] Wallet registry (`registry.mjs`, walletId → metadata, multi-user via owner) + `/api/wallets`
- [x] Balance + tx-history endpoints (`/api/wallet/:n/balance|history|receive`)

## Console / diagnostics
- [x] Tabs: Overview · Blocks · Mempool · Network · Indexes · Wallets · Create Wallet · Reference · RPC Console
- [x] Progressive disclosure: Basic/Detailed/Expert toggle + accordions + raw-JSON at Expert
- [x] Full read-only RPC catalog (44 methods, 6 categories) in Reference tab, runnable inline
- [x] Visuals: sync progress bar, mempool fee histogram, peer-version chart
- [x] Read-only RPC whitelist proxy (no spend/stop from the dashboard)
- [x] Node selector (full :8332 / pruned :8342); auto-refresh + manual RPC runner
- [x] Create Wallet page with obvious link to WaaS
- [x] **Node tab** — recognizes running Core on standard port (`/api/health`: running/booting/busy/down), Start/Stop control, live debug.log bootup/sync stream, header status pill (auto-polls)

## Packaging / effortless extension
- [x] `bankon` launcher — up / status / stop / doctor / install-core / qt
- [x] One-click Bitcoin Core installer (`bankon install-core`, SHA256-verified)
- [x] Qt version of the UI (`bankon-qt/bankon_qt.py` + `bankon-qt.sh`, software-rendered)
- [x] BANKONBTC skill (embeds Bitcoin-Core + frontend + Qt) at ~/.claude/commands/bankonbtc.md

## Security / prod (Phase 3)
- [x] API auth + rate limiting (`shared/security.mjs`, off unless BANKON_API_TOKEN set; wired into both servers)
- [x] Verified no key material ever logged (servers log boot banners only; audit clean)
- [x] Client-code integrity — standalone air-gapped `offline-client.html` (keygen + PSBT sign, zero network calls)
- [x] Encrypted descriptor backups (`bankon-backup.sh`, AES-256/PBKDF2, roundtrip tested)
- [x] Regtest e2e tests (`test-e2e-regtest.sh` + `test-multisig-regtest.sh`, both PASS)

## Services / ops (Phase 4–5)
- [x] Multisig wallets (`/api/wallet/multisig`, 2-of-3 regtest PASS) + single-sig Taproot (keygen/sign tested)
- [x] Real-time events → webhooks (`events.mjs`, polling; ZMQ noted as low-latency upgrade)
- [x] systemd units (`systemd/*.service` + `.timer` + `install-units.sh`)
- [x] Scheduled monitoring (`bankon-monitor.sh` + `bankon-monitor.timer`, runs clean)
- [ ] Taproot script-path / MuSig2 multisig (advanced — future)

## Node intelligence & native diagnostics (Phase 6)
- [x] ZMQ push notifications — `zmqpub*` in `bitcoin.conf` + Qt `ZmqService` (refresh on each block)
- [x] OP_RETURN canonical anchor — `anchor.mjs` + `/api/anchor` `/api/verify`; **regtest PASS** (`test-anchor-regtest.sh`)
- [x] Qt MVVM refactor — `bankon-qt/services/*` + `adapters/*` (ChainAdapter + BitcoinCoreAdapter)
- [x] Node-native maps — EtherApe Net Map + Geo Map from `getnodeaddresses` + GeoLite2 (great-circle arcs, ASN colour); single instance, no external API
- [x] pgvectorscale collector — `node-collector.mjs` + `db/schema.sql` (geo/version/uptime + `vector(8)` + StreamingDiskANN); `/api/nodes/collect|stats`
- [x] Ops — restored deleted `bitcoind` binary (SHA256-verified); Console RPC concurrency limiter + debounced refresh (flood fixed)
- [x] Docs — [QTbankonQT.md](QTbankonQT.md) reference, [NAV.md](NAV.md), [QT roadmap](roadmap.md)
- [!] Apply `db/schema.sql` to a live pgvectorscale Postgres + run collector at scale — **user (needs the DB)**
- [ ] allchain multi-chain node sources (Tier 1/2/3) + GlobeNode/GlobeEdge model — see [QT roadmap](roadmap.md)
- [ ] Qt Quick 3D / QRhi 3D globe behind a swappable interface — **gated on a GPU-capable host**

## Done
- [x] Crash recovery; full node headless + 16 peers + dbcache=2000
- [x] Non-custodial keygen (`keygen.mjs`) proven
- [x] WaaS API + UI live (:8088); watch-only import proven e2e
- [x] Wallet-type accordion explainer
- [x] Pruning assessment (`PRUNING.md`) + `bankon-node-mode.sh`
- [x] Multi-node manager (`bankon-nodes.sh`)
- [x] Roadmap + TODO
- [x] Documentation set (`docs/`: getting-started, architecture, wallets, api, console, security, operations, testing)
