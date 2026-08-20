# BANKON QT — Complete Reference

The native desktop face of BANKON: a **PySide6 / Qt Widgets** diagnostics & node-control app for a
running Bitcoin Core — *"the agnostic face of Bitcoin Core."* This document is the authoritative
reference for the **shipped** app (`bankon-qt/`) and reviews the four aspirational `docs/qt/` guides
against what is actually built, so the design intent and the implementation gap are explicit.

> **One-line truth:** BANKON QT is a Qt **Widgets** app under **software rendering** (Intel HD 3000),
> reading one Bitcoin Core instance, resilient during IBD. The `docs/qt/` guides describe a future
> **Qt Quick 3D** chain-agnostic globe; this app implements their *hardware-independent* substance
> today and defers the GPU/multi-chain parts. See [§7](#7-review-of-the-docsqt-guides).

---

## 1. Run it

```bash
~/bankon-tools/bankon qt          # or: ~/bankon-tools/bankon-qt.sh
```
First run installs **PySide6** (~150 MB, one-time). The launcher forces **software rendering** with
`QT_OPENGL=software` because the Intel HD 3000 GL driver black-screens — the same reason the node
itself runs headless. Requires a `DISPLAY`.

> **HD 3000 gotcha:** use **only** `QT_OPENGL=software`. Adding `LIBGL_ALWAYS_SOFTWARE=1` or
> `QT_XCB_FORCE_SOFTWARE_OPENGL=1` forces a GL path that hangs — the process runs but **no window
> appears**. `QT_OPENGL=software` alone loads fast.

Manual launch:
```bash
cd ~/bankon-tools/bankon-qt
QT_OPENGL=software python3 bankon_qt.py
```

---

## 2. Architecture (as built) — MVVM service layer in Qt Widgets

The app follows the master guide's **MVVM service-layer discipline** without QML: a clean data layer
(`services/`) and chain abstraction (`adapters/`) under thin widget **views** (`bankon_qt.py`).

```
bankon-qt/
  bankon_qt.py            # views (9 tabs) + Main window, toolbar, timers, ZMQ wiring
  services/
    rpc_service.py        # rpc() → Console cache (resilient) w/ direct-node fallback; rpc_cached, synctip, fetch_json, flag
    zmq_service.py        # ZmqService(QThread): subscribes hashblock → block(hash,seq) signal (push refresh)
    geoip_service.py      # GeoLite2 City+ASN readers: geolocate(ip), asn(ip), WORLD outlines
    geodesy.py            # WGS84 constants, geodetic_to_ecef, great_circle_points (slerp arcs)
    network_view.py       # known_nodes(): whole network from getnodeaddresses (addrman), geolocated
  adapters/
    base.py               # ChainAdapter ABC (health_check, get_height, get_balance, build_tx, broadcast_tx, anchor, verify_anchor)
    bitcoin_core.py       # BitcoinCoreAdapter (CAIP-2 bip122:…) over rpc_service + WaaS
```

**Why Widgets, not QML:** the guides recommend Qt Quick 3D for its GPU scene graph. Under software
rendering on the HD 3000 that scene graph *regresses*, so Widgets is the pragmatic choice here; the
MVVM separation the guides actually care about is adopted regardless. QML/3D is the GPU-host target
(see [roadmap.md](roadmap.md)).

---

## 3. Data flow & IBD resilience

The node spends long stretches lock-bound during IBD, so the app never depends on a single live RPC:

- **Cache-routed RPC** — `rpc()` calls the **Console** proxy (`:8090/api/rpc`), which serves
  last-known cache instantly and is **concurrency-limited** so it can't flood the node's work queue;
  if the Console is down it falls back to the node directly (cookie auth).
- **Sync from the log** — the gauge reads `/api/synctip` (a tail of `debug.log` `UpdateTip`), which is
  always current with **no RPC**, plus a "+N blocks since last refresh" delta.
- **ZMQ push** — `ZmqService` subscribes to `zmqpubhashblock` (tcp://127.0.0.1:28332); each new block
  triggers `do_refresh()`, so updates are event-driven and the rate timer is a fallback heartbeat.
- **Off the UI thread** — every RPC/HTTP call runs in a `QThread` worker (`spawn` for RPC,
  `spawn_fn` for arbitrary fetches); results marshal back via queued signals. The UI never blocks.
- **Central refresh** — `Main.do_refresh()` drives the active tab from the timer / ↻ button / ZMQ /
  tab-switch and stamps `↻ HH:MM:SS · every <rate>`.

---

## 4. The tabs

Tabs are **drag-and-drop re-orderable** — grab a tab and drop it where you want it; the
chosen order **persists across sessions** (QSettings), and tabs a saved order doesn't
know keep their default place.

The **🛠 Admin** toolbar button opens the **ADMIN popup** — every toolbar toggle (Geo Map,
SPINTRADE, invert, blackICE, refresh rate, thermal pause) as two-way mirrors, plus admin
actions (refresh-all, open Console/WaaS, clear RPC cache, wipe `.history` with confirm,
reset saved layout) and a **🧊 I.C.E — the tool** section: open the console's ICE tab or
launch the full standalone controller (`~/ICE/ice.py`). **🖤 blackICE is only the THEME;
🧊 I.C.E is the tool** — the thermal wall + RF kill-switch (AIRGAP) + forensics; per
`ICE.md` the admin only *links* to it, every ICE action executes in ICE itself. The popup
**auto-opens at startup when it was open last time** (default on, remembered), docked
beside the globe. It follows the ₿ANKON launcher's window choreography: **⚓ DOCK**
parks it (**geo map** — its birth dock, console right/left/banner, **launcher right/left**
— the GTK launcher is found cross-process via wmctrl, screen corners, or open space) and
**📞 CALL console** summons the console window to the popup's display — the windows find
each other even across multiple displays. The popup **begins life docked on the geo map**
(the geo display's corner when the tab is up, the tab area otherwise) and can then be
moved as preference. Resizable; drag the **⠿ grip** and drop it — on the geo display,
near a console or launcher edge, or in a screen corner it snaps to that dock, anywhere
else it claims that open space — and both the dock choice and exact position are
remembered across sessions.

| Tab | What it shows |
|-----|---------------|
| **Overview** | Live sync gauge (log-based, 6-digit, candle-green ramp ≥51%→99%, FULL NODE at 100%), +N-blocks delta, height/peers/mempool/disk, BTC.oracle (avg blocktime), filesystem |
| **Node** | Node state (running/booting/validating/stopped), Start/Stop, live `debug.log` stream, "what/how/where Bitcoin Core is doing" |
| **Network** | Peer table (addr, subver, direction, ping, height) |
| **Net Map** | EtherApe-style: our node centre (`bankon:<addr>`), connected peers radial with **traffic-gradient** links (blue→green→orange), **inbound/outbound** tint, sizes by traffic, and a faint **cloud of all known nodes** (addrman). Traffic animates as **comets** — bright head, fading tail laid against the direction of travel (orange streams IN, green OUT), **brightness = link quality** (measured ping) |
| **Geo Map** | The **whole known network** (addrman, geolocated) as a density layer + connected peers with **great-circle arcs** + **ASN/org** colour & tooltips; EPSG:4326 plate carrée; disclosures (arcs inferred, geo approximate). **🎯 accuracy** toggle draws each address's GeoLite2 `accuracy_radius` circle (globe + flat + flatearth, tier in tooltip, median in caption); legend tallies peers **by country / speed (ping) / uptime**; **🕐 tz** picks UTC (default) / local / fixed offset for every stamp; **🪙 ₿ price** is an OPT-IN CoinGecko free-tier poll (casual, once an hour) drawn completely in-house as an overlay with each price **marked on the hour** — off = zero external contact. Packet flow rides the great-circle arcs as **comets** whose tails bend with the sphere (heading adapts to the projected angle; brightness = link quality), and in **🗺 political** mode each globe node point is labeled with its **address + geographic city, country**. Every globe point (peers **and** our node) answers **on hover** with its actual data — ip · location · ±km accuracy · speed (ping + live ▼/▲ B/s) · traffic · uptime · direction (gold ring on the hovered point). The **🏠 local node overlay** (top-left, in-house QPainter) shows this machine's node **prominently** from actual data — NODE (height · sync % · agent) / NET (peers · live ▼/▲ totals · our address) / BLOCKS (tip · age · headers), each line a toggle — with the **time in the chosen timezone** riding its header. The **📡 feed** toggle fills the globe's left flank with local-node output — live connection activity (from this node's log, geolocated) over a **🧾 transactions** feed (mempool Δ + connected blocks with tx counts); the right flank stays clear for the 🛠 admin's geo-map dock. **🔬 Advanced** is ₿ANKON network science from this node's live data: **🩺 network health** (country/ASN Herfindahl concentration → plain eclipse-risk verdicts, transport mix, ping distribution) and **📏 latency vs distance** (geodesic distance → light-in-fiber RTT bound vs measured ping → per-link efficiency %) — no external calls |
| **Mempool** | size, vbytes, memory, min relay fee |
| **Blocks** | Live chain tip (cached) + avg blocktime + recent-blocks table (from `/api/recentblocks`) |
| **Indexes** | txindex height / tip / % (live), refresh-stamped |
| **RPC Console** | Run any read-only whitelisted RPC |

---

## 5. The network view (single instance, no external API)

Both maps source the network from **one Bitcoin Core instance** via `getnodeaddresses` — the node's
addrman, which *is* the `getaddr`-gossiped reachable set (the same mechanism Bitnodes crawls).
`services/network_view.py::known_nodes()` pulls it, geolocates each IP with GeoLite2 (City + ASN),
and caches ~3 min. This is deliberately **no third party** (bitnodes.io is unreliable/down). Edges
are *inferred* (Core exposes no peer-edge list) and IP geolocation is approximate — both disclosed
in-UI.

---

## 6. Theming

Multi-chain palette (Bitcoin orange, Polygon purple, Ethereum blue, cash green; Cardano-blue/Solana
accents), **BANKON corporate blue/grey**, an **electric-blue auric shimmer** on the outer periphery
(animated `QGraphicsDropShadowEffect`), and a **candle-green** sync gauge that ramps from dark
(<51%) to candle green (`#16C784`, ≥99%) and flips to **FULL NODE** at 100%.

---

## 7. Review of the `docs/qt` guides

The four guides in [`docs/qt/`](qt/) are **aspirational target architecture**. How BANKON QT relates:

| Guide | Implemented | Adapted | Deferred |
|-------|-------------|---------|----------|
| **Master Architect Guide** (RPC+ZMQ, ChainAdapter, OP_RETURN anchor, MVVM) | ZMQ push; `ChainAdapter`/`BitcoinCoreAdapter`; OP_RETURN anchor (`bankon-waas/anchor.mjs`, regtest-proven); MVVM service layer | QtWidgets instead of QML/Qt Quick | EVM/Foundry, Algorand/x402, DAIO, SATPAY adapters |
| **Engineering Reference** (QML house style, tooling, LGPL matrix) | Off-UI-thread threading discipline; dynamic-link posture | Licensing read as **GPLv3 (client crypto) + MIT (infra)**, not Apache-2.0 | `qmllint`/`qmlformat`/QML module tooling (N/A to Widgets) |
| **3D Globe Reference** (Qt Quick 3D, WGS84, geodesics, instancing) | WGS84 geodesy (`geodesy.py`), great-circle slerp arcs, GeoIP, ASN colour | Density layer rendered to a 2D pixmap (the "instancing for tens of thousands" idea on a software renderer) | The 3D Qt Quick 3D / QRhi globe (needs a GPU) |
| **Clean-house allchain Globe** (no Cesium/WebEngine/Tauri; span `allchainz`; 3-tier sources) | Clean-house affirmed (zero web engine); Tier-2 self-crawl via `getnodeaddresses` for Bitcoin | Single-chain today | `allchain` registry, GlobeNode/GlobeEdge, multi-chain layers, cross-chain arcs |

**Net:** the science and structure the guides insist on (WGS84, slerp arcs, MVVM, ZMQ, clean-house,
single-node sovereignty) are **in the app today**; the GPU 3D globe and multi-chain `allchainz`
expansion are the forward path, gated on a GPU-capable host and the proprietary `allchain` registry.

---

## 8. Environment & config

| Var | Default | Purpose |
|-----|---------|---------|
| `BANKON_CONSOLE_URL` | `http://127.0.0.1:8090` | Console cache the app routes RPC through |
| `BANKON_WAAS_URL` | `http://127.0.0.1:8088` | WaaS (Create-Wallet button, adapter anchor/broadcast) |
| `BITCOIN_RPC_URL` / `BITCOIN_COOKIE` | `:8332` / `~/.bitcoin/.cookie` | direct-node fallback |
| `BANKON_GEOIP` / `BANKON_GEOIP_ASN` | `../geoip/GeoLite2-*.mmdb` | GeoIP databases |
| ZMQ | `tcp://127.0.0.1:28332/3/5` | requires `zmqpub*` in `bitcoin.conf` (+ node restart) |

---

## 9. Troubleshooting

- **Black screen / GL crash** → use the launcher (software-rendering flags); never run raw `bitcoin-qt`-style GL here.
- **Tabs empty during IBD** → expected; the node is lock-bound. Data fills from cache/log as gaps open; the ↻ stamp confirms refreshes are firing.
- **Net/Geo Map sparse** → `getnodeaddresses` is starved during heavy IBD; it populates when the node has capacity (off-thread, never blocks the UI).
- **ZMQ chip shows `○`** → `zmqpub*` not in `bitcoin.conf` or node not restarted; the app falls back to timer/log refresh.
- **GeoIP "DB missing"** → ensure `geoip/GeoLite2-City.mmdb` (+ `GeoLite2-ASN.mmdb`) exist.

---

## 10. Latest updates (2026-07)

Tab-by-tab, the current shipped behaviour:

- **Net Log** — rewritten from a plain text stream into a **detail table** (backed by
  `GET /api/nethealth`-style enrichment in `/api/netactivity`): time · event (colour-coded) ·
  peer id · role (outbound-full-relay / block-relay-only / manual / inbound / feeler) ·
  transport (v1 grey / **v2 encrypted green**) · **client** (subver, e.g. `Satoshi:31.0.0`) ·
  peer height · network class (ipv4/ipv6/tor/i2p/cjdns) · address · **note** (fail/disconnect
  reason, column auto-hides when empty). A live **summary bar** (tallies, transport/network mix,
  local addresses) and a **kind filter** sit above it. Currently-connected peers are shown as
  fully-populated rows from `getpeerinfo`; a session address cache backfills disconnect lines.
- **Blocks** — the **txs** column is populated (per-block `nTx`, enriched server-side); columns
  hug their content with the **hash column** absorbing slack (full hash retained for the detail
  dialog); tx counts right-aligned.
- **BTC.oracle** — the mesh graph is now a **calibrated block-interval timeline**: whole-minute
  gridlines (faint 1-min minor, labelled 5-min major), the **10-minute protocol target** highlighted,
  y-axis anchored on the actual average (15-min baseline) so ~10-min intervals fill the height, and
  the **average measured to the second** (`avg 9m 43s`). Three new stat cards — **avg peer ping**
  (avg/min ms across peers), **network ↓/↑ rate**, **network total ↓/↑** — from `/api/nethealth`.
  The **Quiet / Normal / Verbose / Scientific** verbosity now actually drives the measurement log
  (Quiet = id · Normal = +txs/interval · Verbose = +mined-time/hash · Scientific = +tx/s and async
  `getblockstats` economics), the log **seeds recent blocks on open** (no more waiting for a new
  block), and changing level re-logs the latest block immediately.
- **Indexes** — added an **on-disk size** column (background `du`), per-index **tooltips** describing
  what each enables, **total index disk** in the header, and an **Export UTXO snapshot** control
  (`dumptxoutset latest` → `POST /api/index/export-utxo`) with an explicit note that the raw indexes
  are node-local and non-portable — a UTXO snapshot is the shareable artifact (`loadtxoutset`).
- **Window sizing** — the main window sizes to **92 % of the screen it opens on** (centred, clamped
  to the display), so it is usable on any monitor without manual resizing.

---

*See also: [Console server reference](server.md) · [QT roadmap](roadmap.md) · [the four design guides](qt/) · [Console](console.md) · [Architecture](architecture.md) · [NAV](NAV.md).*
