# BANKON QT — Roadmap & TODO

The path for the native desktop app (`bankon-qt/`). Companion to the complete
[QTbankonQT.md](QTbankonQT.md) reference and the four design guides in [`qt/`](qt/).
Whole-BANKON planning lives in [ROADMAP.md](ROADMAP.md) / [TODO.md](TODO.md).

Legend: `[x]` done · `[~]` in progress · `[ ]` todo · `[!]` blocked/gated

**Guiding constraints:** this host is an **Intel HD 3000 under software rendering** (→ Qt Widgets, 2D
now; GPU 3D is a separate target) and the node is **single-instance, often mid-IBD** (→ cache/log/ZMQ
resilience, no external APIs).

---

## Phase A — Native diagnostics (done)
The shipped app: a resilient Qt Widgets face of one Bitcoin Core instance.
- [x] Qt Widgets app, 9 tabs (Overview/Node/Network/Net Map/Geo Map/Mempool/Blocks/Indexes/RPC Console)
- [x] MVVM service layer — `services/{rpc_service,zmq_service,geoip_service,geodesy,network_view}`
- [x] Chain abstraction — `adapters/{base.ChainAdapter, bitcoin_core.BitcoinCoreAdapter}`
- [x] IBD resilience — Console-cache routing + direct-node fallback; sync from `debug.log` (`/api/synctip`) + "+N blocks" delta
- [x] **ZMQ push** — `ZmqService` refreshes on each new block; central `do_refresh()` + visible ↻ stamp
- [x] **Net Map** (EtherApe) — traffic-gradient links, inbound/outbound tint, known-node cloud, `bankon:<addr>` centre
- [x] **Geo Map** — whole addrman network density + great-circle (slerp) arcs + ASN colour + EPSG:4326 + disclosures
- [x] Node-native network view (`getnodeaddresses`) — single instance, no external API
- [x] Clean-house cleanup — dead code removed, `__pycache__`/`.gitignore`, GeoIP extracted to a service

## Phase B — allchain data model (next)
Generalize the network view from one chain to the `allchain` registry (the clean-house globe doc).
- [ ] `allchain_registry` parser (proprietary `allchain.html`) → chain entries + node-source descriptors
- [ ] `GlobeNode` / `GlobeEdge` unified records; merge adapters off-thread with per-chain cadence
- [ ] Tier-1 source adapter (public crawler/API) — Bitcoin via the existing node-native path
- [ ] Tier-2 source adapter (self-crawl + GeoLite2) for EVM-family discovery
- [ ] Tier-3 source adapter (RPC/sequencer/validator + WaaS-infra endpoint mapping)
- [ ] Per-chain colour palette + chain filter/legend in Net/Geo Map
- [ ] Cross-chain edges (bridge/settlement/anchor) as great-circle arcs

## Phase C — 3D globe (GPU-capable host only)
The guides' Qt Quick 3D globe, behind a swappable interface — gated on real GPU hardware.
- [!] Requires a GPU host (not the HD 3000 under software rendering)
- [ ] `GlobeView` interface (`set_nodes`, `set_edges`, `refresh`) so 2D and 3D backends are swappable
- [ ] Qt Quick 3D backend — WGS84-textured `#Sphere`, instanced markers (`QQuick3DInstancing`), `OrbitCameraController`
- [ ] Geodesic arcs via `QQuick3DGeometry` (reuse `geodesy.great_circle_points`)
- [ ] Atmosphere/fresnel `CustomMaterial`, Natural Earth outlines, star skybox (public-domain assets)
- [ ] C++/QRhi (`QRhiWidget`/`QQuickRhiItem`) escape hatch for the performance ceiling

## Phase D — WaaS health surface
Make the globe the operational health view (clean-house globe §5).
- [ ] Per-chain `health_check` rollup → green/amber/red legend; dim unhealthy endpoints
- [ ] Height-divergence check (node head vs consensus) to surface stuck/forked nodes
- [ ] Snapshot diff + appear/disappear animation (`instanceCountOverride`) instead of full rebuilds
- [ ] OP_RETURN anchor of a snapshot digest (reuse `bankon-waas/anchor.mjs`) for a Bitcoin-timestamped record

---

*Pinned dependencies for later phases (from the guides): `geographiclib`, `pyproj`, NASA Blue Marble +
Natural Earth (public domain), GeoLite2. No CesiumJS / QtWebEngine / Tauri — those belong to the
separate PARSEC shell; BANKON QT stays clean-house Qt-native.*
