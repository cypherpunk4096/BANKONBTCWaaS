# BANKON Qt — Clean-House Multi-Chain WaaS Globe (spanning `allchainz`)

*Companion to the prior two globe documents. This one resolves a specific
architectural commitment: **BANKON's globe is built clean-house in Qt** — from Qt
primitives, with no CesiumJS, no QtWebEngine, and no Tauri (those belong to the
separate PARSEC shell) — and it must **span every chain in the `allchain`
registry**, because the BANKON Wallet-as-a-Service at `bankon.pythai.net` spans
them all. The prior Bitnodes research becomes one chain adapter inside a
multi-chain topology, not the whole picture.*

---

## 1. What "clean-house" commits you to

Clean-house here is the cypherpunk-greenfield posture applied to the visualization
layer: the globe is assembled from Qt's own building blocks and your own geodesy,
with no embedded browser engine and no third-party globe framework carrying its
own renderer, asset pipeline, or licence surface. Concretely it means four
rejections and four affirmations.

You reject **QtWebEngine/CesiumJS** (the Chromium dependency and the web stack
belong to PARSEC, not to a native Qt diagnostic tool), **Tauri** (a different shell
entirely), **any drop-in globe library** (react-globe.gl, three-globe, KDE Marble
as a dependency — Marble is worth *reading* as an LGPL reference but not linking),
and **any imagery/vector source that is not public-domain or permissively
licensed**. You affirm **Qt Quick 3D** as the scene layer (with a **C++/QRhi**
escape hatch for the performance ceiling), **your own WGS84 geodesy** as the single
source of truth, **public-domain NASA Blue Marble + Natural Earth** assets, and
**Apache-2.0 with a flat snake_case layout** throughout. The result links only Qt
(LGPLv3, dynamically) plus your own Apache-2.0 code — the cleanest possible licence
footprint, and the same discipline already established for the BANKON wallet shell.

This is the native sibling of the PARSEC globe, not a replacement for it. PARSEC is
a Tauri host that *envelopes* BANKON and renders its network view in CesiumJS;
BANKON Qt renders its *own* view natively for its own WaaS diagnostics. They share
concepts (WGS84, geodesic arcs, instanced markers) and nothing else — and crucially
they share no code, which is the point of clean-house.

---

## 2. From one chain to `allchainz`: the model inversion

The earlier Bitnodes work assumed a single network: one node set, one colour, one
geolocation source. The WaaS inverts that assumption. `bankon.pythai.net` spans
the whole `allchain` registry, so the globe's primary subject is no longer "Bitcoin
nodes" but **the union of every network the WaaS touches**, rendered as one
coherent topology in which each chain is a distinguishable layer.

The `allchain` registry (your proprietary `allchain.html` chainmapping — treated
here as input you supply) is the spine. Each entry already carries what the globe
needs: a CAIP-2 network id, the chain's role in your stack (settlement, anchor,
compute, bridge leg), its RPC endpoints, and — this is the new field the globe
demands — a **node-source descriptor** telling the globe *how* to obtain geolocated
participants for that chain. The globe iterates the registry, instantiates the
right node-source adapter per entry, and merges every adapter's output into one
geolocated, chain-tagged node set. Add a chain to `allchain.html` and it appears on
the globe; that is the whole extensibility story.

The unified record each adapter emits is deliberately chain-agnostic:

```
GlobeNode {
  chain_id        : str        # CAIP-2, joins back to allchain registry
  address         : str        # ip:port, validator id, or endpoint host
  role            : enum        # full_node | validator | sequencer | rpc | relay | waas_infra
  latitude        : f64
  longitude       : f64
  asn             : u32
  organization    : str
  height          : u64         # chain head as seen at this node, if known
  client          : str         # user agent / client version, if known
  healthy         : bool        # passed the adapter's health probe
}
```

Cross-chain relationships — your bridges and settlement routes — are a second
stream:

```
GlobeEdge {
  src_chain_id, dst_chain_id : str
  src_lat, src_lon, dst_lat, dst_lon : f64
  kind : enum   # bridge | settlement | anchor | xcm | wormhole
}
```

Everything downstream — geodesy, instancing, colouring, health rollup — operates on
these two structures and never needs to know which chain produced them.

---

## 3. Per-chain node data: the heterogeneity you must absorb

The hard truth the WaaS globe has to encode is that **node-topology data is not
uniform across chains**, and pretending otherwise produces a dishonest map. There
are three tiers of source, and the registry's node-source descriptor names which
tier each chain uses.

**Tier 1 — public crawler/API (full P2P node set).** A few networks expose
their reachable-node set directly. Bitcoin has **Bitnodes**
(`https://bitnodes.io/api/`, the MIT-licensed crawler at
`https://github.com/ayeowch/bitnodes`), returning per-node lat/long/ASN/org. Ethereum
execution has **`ethereum/node-crawler`** (`https://github.com/ethereum/node-crawler`),
which crawls the devp2p network and geolocates with MaxMind GeoLite2, plus
**ethernodes.org** (Bitfly, `https://ethernodes.org/`) and the **Etherscan Node
Tracker** (`https://etherscan.io/nodetracker`). Ethereum consensus has **ChainSafe
NodeWatch** (`https://github.com/ChainSafe/nodewatch-api`), a discv5 crawler exposing
a GraphQL API, alongside MigaLabs' Monitoreth and ProbeLab's Nebula crawler (the
canonical list is on `https://ethereum.org/developers/docs/nodes-and-clients/`).
For these chains the adapter is a thin polling client over an existing API.

**Tier 2 — self-crawled (you run the discovery).** For chains with a public P2P
discovery protocol but no convenient API — and where you want sovereign,
unmediated data consistent with running your own infrastructure — the adapter
performs discovery itself: devp2p/discv5 for EVM-family chains, the chain's own
gossip/seed mechanism otherwise, then geolocates IPs with **MaxMind GeoLite2**
(`https://dev.maxmind.com/geoip/geolite2-free-geolocation-data`). This is the same
pattern Bitnodes and node-crawler use internally; you are simply running it under
the WaaS rather than trusting a third party. It is the most clean-house option and
the most operationally expensive.

**Tier 3 — endpoint/validator/infra mapping (no P2P node set exists).** Most L2s,
app-chains, and your own targets — 0G, Moonbeam, Base, Blast, and similar — either
run centralized sequencers or simply have no public reachable-node crawler. For
these the honest unit of visualization is not "all nodes" but **the endpoints the
WaaS actually depends on**: the chain's public RPC endpoints (geolocated by
resolving and IP-mapping their hosts), its validator/sequencer set where published,
and **the WaaS's own infrastructure** for that chain. This tier is where the globe
stops being a generic node map and becomes a true *WaaS* map — it shows the
operational surface BANKON relies on, which is exactly what a service-health view
should show.

Encoding the tier per chain keeps the map truthful: a viewer can see that Bitcoin's
ten thousand dots mean something different from an L2's three sequencer dots, and
the UI should label the `role` accordingly rather than implying false symmetry.

---

## 4. Rendering the multi-chain globe, clean-house in Qt

The rendering machinery is the Qt-native stack established in the prior globe
document, now parameterized by chain. The base is a `View3D` with a WGS84-textured
`#Sphere` (NASA Blue Marble equirectangular as `baseColorMap`), a `DirectionalLight`
sun, and an `OrbitCameraController`. Three things change for `allchainz`.

**Markers become per-chain instanced layers.** Each chain gets its own
`QQuick3DInstancing` subclass instance (or one shared instancing table with a
per-instance colour and a `customData` chain index), so all of a chain's nodes draw
in a single GPU call. Qt's instancing renders a million instances at 60fps using
~2% CPU, so even Bitcoin's full set plus every other chain's nodes is comfortable.
Colour encodes chain identity (a stable palette keyed by `chain_id`), and a small
altitude offset per chain — lifting each chain's shell a few kilometres in scene
units — separates overlapping dots so dense regions remain legible. Marker scale or
brightness can encode `healthy`. This is the multi-chain analogue of the
single-instancing approach: one table per registry entry, rebuilt off-thread when
that chain's adapter reports new data, each independently `markDirty()`-able so a
fast-moving chain does not force the slow ones to re-upload.

**Cross-chain edges become geodesic arcs in custom geometry.** Your bridges and
settlement routes — SATPAY, XCMB/XCM, Wormhole legs, the Bitcoin anchor — are the
`GlobeEdge` stream, drawn as raised arcs via a `QQuick3DGeometry` subclass filling a
line/strip buffer with points sampled along the great circle (slerp between
endpoint unit vectors) or true geodesic, lifted by a midpoint bump proportional to
distance. Arc colour or dash-animation encodes `kind`, so a viewer distinguishes a
Wormhole leg from a native settlement at a glance. These cross-chain arcs are the
visual payload that a single-chain Bitnodes map can never show and the clearest
expression of "the WaaS spans `allchainz`."

**The UI gains a chain filter and a legend.** Because the registry drives
everything, the filter is generated from it: toggle a chain and its instancing layer
and arcs hide. This is also how you manage Tier-1's scale against Tier-3's sparsity —
let the operator isolate Bitcoin's crowd or focus on just the L2 sequencers.

Atmosphere glow (a `CustomMaterial` fresnel shader compiled with `qsb`), a
public-domain star skybox, and Natural Earth country outlines as overlay geometry
finish the scene. None of these introduce a non-permissive dependency.

For the performance ceiling — if the merged multi-chain set plus animated arcs ever
strains Qt Quick 3D — the same clean-house principle points to the **C++/QRhi**
backend (`QRhiWidget`/`QQuickRhiItem`) behind the identical node/edge interface,
authored in Vulkan-style GLSL transpiled by `qsb` to every backend. Clean-house does
not mean low-performance; it means the performance work is yours and Qt's, not a
web engine's.

---

## 5. The globe as the WaaS diagnostic surface

This globe is not decoration; it is the network-health view of the BANKON WaaS, so
the same adapters that supply nodes also supply health. Each chain adapter
implements a uniform `health_check` returning sync status, head height, peer/endpoint
reachability, and RPC latency, exactly mirroring the chain-agnostic `health_check`
the BANKON wallet already defines for its adapters. The globe rolls these into a
per-chain status that drives the legend (green/amber/red per chain) and the marker
styling (unhealthy endpoints dimmed). A height-divergence check — comparing each
node's reported `height` against the chain's consensus head — surfaces stuck or
forked nodes as a colour anomaly. Bitcoin Core remains the canonical anchor: its
block height is the immutable reference the diagnostic view can timestamp the whole
multi-chain snapshot against, and an OP_RETURN anchor of a snapshot digest gives the
WaaS a verifiable, Bitcoin-timestamped record of network state at a moment in time.

In other words, the globe answers an operator's real question — *is the WaaS healthy
across every chain it serves right now?* — geographically and at a glance, which a
flat per-chain dashboard cannot.

---

## 6. Clean-house project structure and threading

Flat, snake_case, Apache-2.0, Qt-native dependencies only:

```
bankon_globe/
  allchain_registry.py      # parses allchain.html -> chain entries + node-source descriptors
  node_source_bitnodes.py   # Tier 1: Bitcoin via Bitnodes API
  node_source_eth_crawler.py# Tier 1/2: Ethereum via node-crawler/ethernodes/nodewatch
  node_source_devp2p.py     # Tier 2: self-crawl + GeoLite2
  node_source_endpoints.py  # Tier 3: RPC/validator/sequencer/WaaS-infra mapping
  geodesy.py                # WGS84 geodetic->ECEF, slerp/geodesic arc sampling (single source of truth)
  node_model.py             # merge adapters -> GlobeNode/GlobeEdge; snapshot diffing
  health.py                 # per-chain health_check rollup
  globe_instancing.py       # QQuick3DInstancing subclass (per-chain layers)
  arc_geometry.py           # QQuick3DGeometry subclass (cross-chain arcs)
  globe_view.py + Globe.qml # Qt Quick 3D scene (clean-house, no Cesium/WebEngine)
  globe_rhi.py              # optional C++/QRhi performance backend, same interface
  assets/                   # NASA Blue Marble, Natural Earth, GeoLite2 db, star skybox
```

The non-negotiable threading rule carries over unchanged: **all registry polling,
crawling, geolocation, and geodesy run off the UI thread** (PySide6 `QThread`/worker
objects or `QThreadPool`), emitting finished `GlobeNode`/`GlobeEdge` arrays back to
the GUI thread via queued signals; the QML scene graph is touched only on the main
thread. Each chain adapter polls on its own cadence (Bitnodes ~10 min; an L2's RPC
health every few seconds), and the model merges asynchronously so one slow chain
never stalls the view. Respect every source's limits — Bitnodes' rate limit and
`retry-after`, MaxMind's licence and accuracy caveats, and any third-party crawler
API's terms.

---

## 7. Where this sits relative to PARSEC

To keep the two efforts from blurring: **BANKON Qt's globe is clean-house, native,
and diagnostic** — Qt Quick 3D / QRhi, your geodesy, your instancing, no web engine —
and it serves the WaaS at `bankon.pythai.net` by mapping `allchainz`. **PARSEC's
globe is a Tauri/React/CesiumJS view inside the host shell** that envelopes BANKON
among many wallets. Same scientific concepts (WGS84, geodesic arcs), deliberately
separate implementations, zero shared code. If you ever want them to agree on
numbers, the agreement happens at the data layer — both can consume the same
`allchain` registry and the same node-source outputs — but the renderers stay
independent. That separation is itself a clean-house guarantee: neither globe can
drag the other's dependencies into its licence or threat surface.

---

## Source references

**Multi-chain node data sources**
- Bitnodes API: <https://bitnodes.io/api/> · crawler (MIT): <https://github.com/ayeowch/bitnodes>
- Ethereum node-crawler (devp2p + GeoLite2): <https://github.com/ethereum/node-crawler>
- ethernodes.org (Bitfly): <https://ethernodes.org/>
- Etherscan Node Tracker: <https://etherscan.io/nodetracker>
- ChainSafe NodeWatch (discv5, GraphQL): <https://github.com/ChainSafe/nodewatch-api>
- Ethereum nodes-and-clients (tracker list): <https://ethereum.org/developers/docs/nodes-and-clients/>
- MaxMind GeoLite2 (geolocation): <https://dev.maxmind.com/geoip/geolite2-free-geolocation-data>

**Qt-native rendering (clean-house)**
- Qt Quick 3D: <https://doc.qt.io/qt-6/qtquick3d-index.html>
- Instancing (1M @60fps): <https://www.qt.io/blog/qtquick3d-instanced-rendering>
- QQuick3DInstancing: <https://doc.qt.io/qt-6/qquick3dinstancing.html>
- QQuick3DGeometry: <https://doc.qt.io/qt-6/qquick3dgeometry.html>
- Custom Geometry example: <https://doc.qt.io/qt-6/qtquick3d-customgeometry-example.html>
- QRhiWidget (performance backend): <https://doc.qt.io/qt-6/qrhiwidget.html>
- KDE Marble (LGPL reference to read, not link): <https://github.com/KDE/marble>

**Geodesy (single source of truth)**
- WGS84 (NGA): <https://earth-info.nga.mil/index.php?dir=wgs84&action=wgs84>
- GeographicLib (Karney geodesics): <https://geographiclib.sourceforge.io/>

**Imagery / vectors (public domain)**
- NASA Visible Earth / Blue Marble: <https://visibleearth.nasa.gov/>
- Natural Earth: <https://www.naturalearthdata.com/>

---

*`allchain.html` / `allchainz`, the BANKON WaaS, SATPAY/XCMB bridge routes, and the
per-chain node-source descriptors are proprietary to you and not publicly
documented; this guide supplies the clean-house architecture and the honest
multi-tier data model, and you fill in the registry contents and the WaaS infra
endpoints. IP geolocation is approximate and many non-Bitcoin chains have no
complete public node set — the three-tier model above is the truthful way to render
that asymmetry rather than implying every chain offers a Bitnodes-grade census.*
