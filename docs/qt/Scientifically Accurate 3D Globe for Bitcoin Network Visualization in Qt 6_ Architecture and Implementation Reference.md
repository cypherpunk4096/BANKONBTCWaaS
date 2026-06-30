# Master Architect Reference: A Scientifically Accurate 3D Globe for Bitcoin Network Visualization in Qt 6

## TL;DR
- Build the globe as a **swappable component behind a stable interface**, and adopt **Qt Quick 3D** (PySide6/QML) as the primary path because it matches the BANKON house style; reserve **CesiumJS-in-QtWebEngine** as the scientific-fidelity option (WGS84-exact, geodesic arcs, Apache-2.0) and a **C++/QRhi** path for maximum performance. Feed all three from one Python data/geodesy core.
- To meet and exceed Bitnodes, poll its public JSON API (`https://bitnodes.io/api/v1/snapshots/latest/`, MIT-licensed project), render every reachable node as a GPU-instanced marker positioned by exact WGS84 geodetic→ECEF math, and draw peer connections as raised geodesic arcs — surpassing Bitnodes' flat 2D web map with a true 3D ellipsoidal globe.
- Keep API polling and geodesy off the UI thread, color nodes by ASN/organization, and source imagery/vectors from public-domain NASA Blue Marble and Natural Earth so the entire stack stays license-clean under the project's Apache-2.0/LGPLv3 posture.

## Key Findings

The task is achievable with three complementary rendering approaches, and the correct architectural decision is not to pick one but to isolate the renderer behind an interface so all three can coexist. Bitnodes itself is an MIT-licensed Python crawler that discovers reachable nodes via the Bitcoin P2P `getaddr` mechanism and exposes a free, unauthenticated JSON API; its own visualization is a flat 2D web map, which establishes a low bar to exceed with a 3D ellipsoidal globe. The scientific requirement is satisfied by adopting the WGS84 datum and exact geodetic-to-ECEF conversion for node placement, and by drawing peer links either as great-circle (spherical slerp) arcs or true geodesics (Karney's algorithm in GeographicLib). Qt Quick 3D's instancing renders one million cubes at 60fps using only ~2% CPU, which comfortably covers the node count Bitnodes typically reports — recent live snapshots report roughly 23,900–24,000 reachable nodes (e.g., snapshot `1764325181` reports `"total_nodes": 23877`, and snapshot `1764319476` reports `24032`). CesiumJS offers the highest geodetic authority out of the box but drags in the heavy Chromium dependency of QtWebEngine. KDE Marble is a mature LGPL reference implementation worth studying.

## Details

### 1. Bitnodes: the baseline to meet and exceed

Bitnodes (`https://github.com/ayeowch/bitnodes`, by Addy Yeow) estimates the size of the Bitcoin peer-to-peer network by finding all reachable nodes. Its methodology, documented in the project wiki (`https://github.com/ayeowch/bitnodes/wiki`), sends `getaddr` messages recursively starting from a set of seed nodes; the crawler (`crawl.py`, `https://github.com/ayeowch/bitnodes/blob/master/crawl.py`) is a greenlet-based concurrent scanner (default 700 workers per `conf/crawl.conf.default`) that speaks protocol version 70016 and writes results into Redis (the Redis schema is documented at `https://github.com/ayeowch/bitnodes/wiki/Redis-Data`). Geolocation is performed using MaxMind GeoIP databases (the repo provisions a `geoip/.maxmind_license_key` and runs `geoip/update.sh`). A new snapshot is produced roughly every 10 minutes.

**License.** The repository's `LICENSE` file (`https://github.com/ayeowch/bitnodes/blob/master/LICENSE`) is the MIT License ("Permission is hereby granted, free of charge, to any person obtaining a copy of this software… "), Copyright (c) 2014 Addy Yeow Chin Heng. MIT is fully compatible with the project's Apache-2.0 posture.

**The public API** (`https://bitnodes.io/api/`) is unauthenticated and free. Per the API v1.0 documentation, "Requests originating from the same IP address is limited to a maximum of 10 requests per day," while "Requests made using authenticated API key is limited to a maximum of 200,000 requests per day. Authenticated API key access is available only to PRO plan users." The `ratelimit-remaining` response header reports remaining quota, and exceeding it yields HTTP 429 with a `retry-after` header. Because snapshots refresh about every 10 minutes, the docs recommend polling at most every 10 minutes. Key endpoints:

- **List snapshots:** `GET https://bitnodes.io/api/v1/snapshots/?page=&limit=` — paginated list of recent snapshots (kept ~60 days), each with `timestamp`, `total_nodes`, `latest_height`.
- **List nodes (the workhorse):** `GET https://bitnodes.io/api/v1/snapshots/<TIMESTAMP>/` where `<TIMESTAMP>` may be `latest`. Returns `timestamp`, `total_nodes`, `latest_height`, and a `nodes` object keyed by `"ADDRESS:PORT"`. An optional `?field=coordinates` returns just unique lat/long pairs, and `?field=user_agents` returns unique user agents.
- **Node status / latency / ranking:** `GET https://bitnodes.io/api/v1/nodes/<ADDRESS>-<PORT>/` and related endpoints provide per-node `status` (PENDING/UP/DOWN), `mbps`, `rtt`, and Peer Index (PIX) rankings (requires prior activation of the node).
- **List addresses, data propagation, DNS seeder:** additional endpoints for all observed addresses, inv propagation statistics, and bootstrap DNS records.

**Per-node data fields.** Each node array contains, in order: (1) protocol version, (2) user agent, (3) connected-since timestamp, (4) services, (5) height, (6) hostname, (7) city, (8) country code, (9) latitude, (10) longitude, (11) timezone, (12) ASN, (13) organization name. This is exactly the payload needed to plot, label, and color nodes — latitude/longitude give placement, ASN/organization drive coloring, and user agent/height/services drive filtering and tooltips.

**What "exceeding" means.** The existing bitnodes.io map is a 2D web (Leaflet-style) projection. A Qt 6 globe exceeds it by rendering on a true 3D WGS84 ellipsoid, drawing peer-connection arcs (which the public API does not directly expose as an edge list — peer relationships must be inferred from the crawler's `peer:` data or approximated, a limitation to disclose), offering smooth orbit/zoom, GPU-instanced markers colored by network/ASN, and live in-app refresh integrated into the wallet's diagnostics.

### 2. Qt Quick 3D (primary, QML-native)

Qt Quick 3D (`https://doc.qt.io/qt-6/qtquick3d-index.html`) is the house-style fit because BANKON is PySide6/QML. A globe scene is a `View3D` containing a `PerspectiveCamera`, a `DirectionalLight`, and a `Model` using the built-in `#Sphere` mesh with a `PrincipledMaterial` whose `baseColorMap` is an equirectangular Earth texture. The PySide6 entry point mirrors the official intro example: set `QSurfaceFormat.setDefaultFormat(QQuick3D.idealSurfaceFormat(4))`, load QML via `QQmlApplicationEngine` (`https://doc.qt.io/qtforpython-6/examples/example_quick3d_intro.html`). Qt Quick 3D for Python is available under GPLv3/commercial; PySide6 itself is LGPLv3 — consistent with the project's LGPLv3 use of PySide6.

**Placing nodes from lat/long.** Convert geographic coordinates to Cartesian positions on the sphere. For a unit-style sphere of radius R the standard mapping is `x = R·cos(φ)·cos(λ)`, `y = R·sin(φ)`, `z = R·cos(φ)·sin(λ)` (Qt uses a Y-up coordinate system, so latitude maps to Y). For scientific accuracy use the full WGS84 ellipsoidal conversion (Section 5) and scale to scene units.

**Instanced markers.** Qt Quick 3D instancing (`https://doc.qt.io/qt-6/quick3d-instancing.html`, `https://doc.qt.io/qt-6/qml-qtquick3d-instancing.html`) renders one mesh many times in a single draw call via `glDrawElementsInstanced`. The Qt engineering blog (`https://www.qt.io/blog/qtquick3d-instanced-rendering`) reports that "QtQuick3D can render one million cubes at 60 frames per second (FPS), using only 2% CPU time," whereas "the same scene recreated with the API in Qt 6.0, using Repeater3D… starts to struggle at ten thousand cubes: only managing 42 FPS and using 100% of the CPU" — decisive for tens of thousands of nodes. For dynamic data, subclass `QQuick3DInstancing` (`https://doc.qt.io/qt-6/qquick3dinstancing.html`) in C++/Python and implement `getInstanceBuffer()`, packing each node's transform and color via `calculateTableEntry(position, scale, eulerRotation, color, customData)`; call `markDirty()` when the node set changes. For small static sets, `InstanceList`/`InstanceListEntry` works in pure QML. `instanceCountOverride` cheaply animates how many instances are drawn.

**Great-circle arcs as custom geometry.** Subclass `QQuick3DGeometry` (`https://doc.qt.io/qt-6/qquick3dgeometry.html`), set primitive type to `Lines` (or build a tube of triangles for thickness), fill an interleaved vertex buffer with positions sampled along the arc, call `setVertexData`, `setStride`, `setBounds`, and `addAttribute(PositionSemantic, …)`. The arc points come from spherical interpolation (slerp) between the two endpoints' unit vectors, lifted above the surface (Section 5). Register the class with `QML_NAMED_ELEMENT` and assign it to a `Model.geometry`. The official Custom Geometry example (`https://doc.qt.io/qt-6/qtquick3d-customgeometry-example.html`) is the working reference.

**Atmosphere and camera.** A `CustomMaterial` with a fragment shader implementing rim/fresnel glow produces an atmospheric halo; Qt's custom material framework consumes `.qsb` shaders built by the `qsb` tool from Qt Shader Tools. Camera control uses `OrbitCameraController` or `WasdController` from the Qt Quick 3D Helpers module for orbit/zoom/pan.

### 3. C++ / OpenGL / QRhi (maximum performance and fidelity)

For the performance ceiling, render the globe directly through Qt's Rendering Hardware Interface (`https://doc.qt.io/qt-6/qrhi.html`), the cross-backend abstraction (Vulkan, Metal, Direct3D 11/12, OpenGL) that Qt Quick itself is built on. The cleanest integration paths are `QRhiWidget` (`https://doc.qt.io/qt-6/qrhiwidget.html`, stable since Qt 6.7, introduced as tech preview in 6.7) for a Widgets UI and `QQuickRhiItem` (the modern replacement for `QQuickFramebufferObject`) for a QML UI. Subclass `QRhiWidget` and implement `initialize()` and `render(QRhiCommandBuffer*)`; the widget renders into a backing texture composited by the top-level widget's backingstore (see the Simple and Cube RHI Widget examples, `https://doc.qt.io/qt-6/qtwidgets-rhi-cuberhiwidget-example.html`). Shaders are authored once in Vulkan-style GLSL and transpiled by `qsb` to every backend. This path enables an ellipsoidal (not merely spherical) tessellated globe, level-of-detail terrain, and custom GLSL/HLSL atmosphere — at the cost of writing and maintaining low-level pipeline, buffer, and resource-binding code. The legacy `QOpenGLWidget`/`QOpenGLWindow` route still works but ties you to a single API; `QRhiWidget` is the forward-looking choice. For integrating bespoke rendering into the Qt Quick 3D pipeline specifically, Qt 6.7+ exposes `QQuick3DRenderExtension` and the semi-public QSSG/QRhi classes, allowing custom render passes inside a Quick 3D scene.

A mature reference to study is **KDE Marble** (`https://github.com/KDE/marble`, `https://marble.kde.org/`), a virtual globe and world atlas built on Qt/C++ and **licensed under LGPL 2.1**; its `MarbleModel`/`MarbleWidget` separation and tiled imagery/terrain architecture are instructive even if you do not reuse the code. Lighter open-source Qt globe references include `afourmy/pyEarth` (a ~150-line Qt/OpenGL globe in Python using `pyshp`, `shapely`, and `pyproj`, `https://github.com/afourmy/pyEarth`) and `3DGISKing/QGlobe` (a Qt5 + Irrlicht Google-Earth-style globe, `https://github.com/3DGISKing/QGlobe`).

### 4. CesiumJS via QtWebEngine (scientific gold standard)

CesiumJS (`https://github.com/CesiumGS/cesium`, `https://cesium.com/platform/cesiumjs/`) is the open-source WebGL virtual globe used across aerospace and geospatial industries, and it is **Apache-2.0 licensed** (`https://github.com/CesiumGS/cesium/blob/main/LICENSE.md`) — an ideal fit for the project's Apache-2.0 stance. Its coordinate model is exactly what "scientific" demands: `Cartographic` (longitude/latitude/height in radians on the WGS84 ellipsoid), `Cartesian3` (ECEF), and `Ellipsoid.WGS84` with `cartographicToCartesian`/`cartesianToCartographic` doing the rigorous conversions. `EllipsoidGeodesic` computes true geodesic distances and interpolated points, and polylines can be drawn with geodesic interpolation natively — the scientific gold standard for arcs. It ships with terrain, imagery layers, entities, and time-dynamic visualization, and its bundled Natural Earth imagery is public domain.

**Embedding in Qt.** Host CesiumJS in a `QWebEngineView` and bridge Qt↔JavaScript with `QWebChannel` (`https://doc.qt.io/qtforpython-6/PySide6/QtWebChannel/index.html`). On the Python side, register a `QObject` (`channel.registerObject("backend", obj)`, `page().setWebChannel(channel)`); on the JS side load `qrc:///qtwebchannel/qwebchannel.js` and construct `new QWebChannel(qt.webChannelTransport, cb)` to access the object's slots, signals, and properties. Live Bitnodes data is pushed from a Python worker into JS by calling a registered `@Slot` or emitting a signal that the page listens for, then materialized as Cesium entities/polylines.

**Tradeoffs.** QtWebEngine embeds a Chromium snapshot. Per the Qt licensing page (`https://doc.qt.io/qt-6/qtwebengine-licensing.html`), its Qt-specific parts are available under LGPLv3/GPLv3/GPLv2 plus commercial, and the bundled Chromium contains code whose most restrictive license is LGPL 2.1; the GPL-listed components "are only used to access Linux system resources" and are not linked or distributed. So WebEngine is usable under LGPL terms, but it is heavy: large binary footprint, a full browser engine to ship and patch, and a sandboxed process model that complicates a Podman-packaged desktop wallet. Use it when geodetic authority and ready-made terrain outweigh bundle size.

### 5. Scientific geodesy — the "definitive" requirement

**WGS84 vs. a sphere.** The definitive datum is WGS84, defined by semi-major axis a = 6,378,137.0 m and flattening f = 1/298.257223563 (NGA/NIMA TR8350.2 / NGA.STND.0036, `https://earth-info.nga.mil/index.php?dir=wgs84&action=wgs84`; PDF mirror `https://gis-lab.info/docs/nima-tr8350.2-wgs84fin.pdf`). Derived constants: semi-minor axis b = a·(1−f) = 6,356,752.3142 m and first eccentricity squared e² = 2f − f² ≈ 0.00669437999014. A plain sphere ignores the ~21 km equatorial-vs-polar difference; the ellipsoid is what makes the visualization scientifically correct.

**Geodetic → ECEF.** With geodetic latitude φ, longitude λ, height h, and the prime vertical radius of curvature `N = a / sqrt(1 − e²·sin²φ)`:

```
X = (N + h)·cos φ·cos λ
Y = (N + h)·cos φ·sin λ
Z = (N·(1 − e²) + h)·sin φ
```

(The (1−e²) factor equals (b/a)².) These are the canonical equations reproduced from TR8350.2; the same transform is available programmatically in `pyproj` via `Transformer.from_crs("EPSG:4326", "EPSG:4978", always_xy=True)` (EPSG:4978 is geocentric ECEF) and in CesiumJS via `Ellipsoid.WGS84.cartographicToCartesian`.

**Great-circle vs. geodesic.** A great circle is the shortest path on a sphere; a geodesic is the shortest path on the ellipsoid. For an authoritative ellipsoidal path, use Charles Karney's algorithm (Karney, "Algorithms for geodesics," J. Geodesy 87(1), 43–55, 2013, DOI 10.1007/s00190-012-0578-z) implemented in **GeographicLib** (`https://geographiclib.sourceforge.io/`, MIT licensed). Per the GeographicLib documentation, Karney's results "are accurate to round-off for terrestrial ellipsoids (the error in the distance is less than 15 nanometers, compared to 0.1 mm for Vincenty)," and unlike Vincenty (1975) the inverse solution "is always found," including for near-antipodal points where Vincenty fails to converge. The Python package `geographiclib` exposes `Geodesic.WGS84.Inverse/Direct/InverseLine` for sampling points along the geodesic.

**Rendering the arc (slerp + height).** For a visually clean great-circle arc, convert each endpoint to a unit vector p = (cos φ·cos λ, cos φ·sin λ, sin φ), then interpolate with spherical linear interpolation (Shoemake, "Animating Rotation with Quaternion Curves," ACM SIGGRAPH 1985, DOI 10.1145/325165.325242):

```
slerp(v0, v1; t) = [sin((1−t)Ω)/sin Ω]·v0 + [sin(tΩ)/sin Ω]·v1,   cos Ω = v0·v1
```

Sample t ∈ [0,1], convert each result back to lat/long or scale to scene radius. To lift the arc off the surface for clarity, scale the radius along the arc by a bump that is zero at the endpoints and maximal at the midpoint (e.g. `radius(t) = R + H·sin(πt)` or `R + H·4t(1−t)`), with peak height H proportional to the chord/great-circle distance so long links rise higher — the convention used by deck.gl's `ArcLayer` (`https://deck.gl/docs/api-reference/layers/arc-layer`), where height scales with source–target distance.

**Projections for any 2D fallback.** If a 2D mode is ever needed, use Web Mercator (EPSG:3857, the Google/Bing convention CesiumJS implements as `WebMercatorProjection`) or equirectangular/plate carrée (EPSG:4326, `GeographicProjection`). The **PROJ** library (`https://proj.org/`) and its Python binding **pyproj** (`https://pyproj4.github.io/pyproj/`) perform all such transforms; `Transformer.from_crs(..., always_xy=True)` avoids axis-order surprises.

### 6. IP geolocation

Bitnodes already returns latitude/longitude, ASN, and organization, so the simplest path is to trust its fields. For independent verification or enrichment, use MaxMind GeoLite2/GeoIP2 (`https://dev.maxmind.com/geoip/`) with the official `geoip2` Python library (`https://github.com/maxmind/GeoIP2-python`, docs `https://geoip2.readthedocs.io/`). Open a `geoip2.database.Reader('GeoLite2-City.mmdb')` for lat/long/city and `Reader('GeoLite2-ASN.mmdb')` for `autonomous_system_number`/`autonomous_system_organization`; reuse the Reader across lookups (construction is expensive). GeoLite2 is free under a Creative Commons Attribution-ShareAlike license, and MaxMind explicitly warns that "IP geolocation is inherently imprecise" — locations are often near population centers and returned as a circle defined by lat/long and an accuracy radius — which should be disclosed in the UI. ASN/organization is the natural key for coloring nodes by hosting provider or network, immediately revealing concentration in a few large cloud ASNs.

### 7. Earth textures and authoritative imagery

NASA's Blue Marble / Visible Earth (`https://visibleearth.nasa.gov/`) provides public-domain equirectangular Earth textures suitable for the sphere's `baseColorMap`; the 2002 Blue Marble is 1 km/pixel (21,600 px wide), and Blue Marble Next Generation adds monthly/seasonal and topography+bathymetry variants (`https://neo.gsfc.nasa.gov/`). Pre-tiled high-resolution derivatives (e.g., 86400×43200) exist for streaming large textures. Natural Earth (`https://www.naturalearthdata.com/`, public domain, supported by NACIS) supplies vector country boundaries at 1:10m/1:50m/1:110m, available as shapefiles or GeoJSON (`https://github.com/nvkelso/natural-earth-vector`, `https://github.com/martynafford/natural-earth-geojson`) for drawing coastlines/borders as overlay geometry. Add night-lights (Black Marble), normal/specular maps for terrain relief and ocean specularity, and a star-field skybox for the background. All of these are public domain or permissively licensed, keeping the bundle license-clean.

### 8. Performance and architecture

Render thousands of nodes via instancing (Section 2), not per-object models. Keep connection arcs as a small number of batched geometry buffers rather than thousands of separate `Model`s; rebuild geometry on a worker and hand finished buffers to the render thread. Apply frustum culling and level-of-detail (fewer arc samples when zoomed out), and use GPU point sprites for very dense marker fields.

Crucially, keep **all** Bitnodes polling and geodesy computation off the UI thread. In PySide6 this means a `QThread`/worker `QObject` (or `QThreadPool`/`QRunnable`) that fetches snapshots, runs the WGS84 conversions and geodesic sampling, and emits finished arrays back to the GUI thread via queued signals — never touching scene-graph objects directly. This matches the established Qt threading discipline: data in, immutable results out, UI updates only on the main thread. Real-time updates follow the ~10-minute snapshot cadence; diff successive snapshots to animate nodes appearing/disappearing using `instanceCountOverride` and buffer updates rather than full rebuilds.

Architecturally, the globe is a self-contained diagnostic/network-health view that plugs into the larger BANKON wallet's diagnostic dashboard (consistent with the WaaS diagnostic concept; the user fills in proprietary specifics). It exposes a narrow interface — "here is a list of geolocated nodes and edges; render them" — so the rendering backend behind it is swappable.

### 9. Synthesis and recommendation

| Criterion | Qt Quick 3D | C++ / QRhi | CesiumJS + QtWebEngine |
|---|---|---|---|
| Scientific accuracy | High (you supply WGS84 math) | Highest (full ellipsoid/LOD control) | Highest out-of-the-box (WGS84, geodesics, terrain) |
| Performance | Excellent (instancing ~1M @60fps) | Highest (hand-tuned) | Good (WebGL, Chromium overhead) |
| Licensing fit | PySide6 LGPLv3; QtQuick3D GPLv3/commercial | Same Qt licensing | **Apache-2.0** (Cesium) atop WebEngine LGPL/Chromium LGPL2.1 |
| Dev effort | Low–moderate | High | Moderate (JS bridge, two languages) |
| House-style fit (PySide6/QML) | **Best** | Moderate (C++ needed) | Weak (web stack embedded) |
| Bundle size | Small | Small | **Large** (Chromium) |

**Recommendation.** Adopt **Qt Quick 3D as the primary path**: it is the natural PySide6/QML fit, instancing handles the node count with headroom, and you retain full control of the WGS84 math. Structure the code so the globe is a **swappable component behind a stable interface** (e.g., `GlobeView` with methods `set_nodes(...)`, `set_edges(...)`, `refresh()`), so that **CesiumJS-in-QtWebEngine** can be dropped in when maximum geodetic authority or ready-made terrain is wanted, and a **C++/QRhi** backend can be added where raw performance dominates. The geodesy and data layers (Bitnodes client, MaxMind enrichment, WGS84/GeographicLib conversions) are pure Python and shared across all three backends.

**Recommended project structure (flat, snake_case):**
- `bitnodes_client.py` — threaded API polling, rate-limit handling
- `geodesy.py` — WGS84 geodetic→ECEF, slerp arcs, GeographicLib geodesics
- `geoip_enrich.py` — optional MaxMind verification/coloring
- `node_model.py` — in-memory model, snapshot diffing
- `globe_view_qml.py` + `Globe.qml` — Qt Quick 3D backend
- `globe_view_cesium.py` + `cesium_globe.html` — CesiumJS backend
- `globe_instancing.py` — `QQuick3DInstancing` subclass
- `arc_geometry.py` — `QQuick3DGeometry` subclass
- `assets/` — Blue Marble textures, Natural Earth vectors, GeoLite2 dbs

**Dependencies:** PySide6 (QtQuick3D, QtWebChannel, optionally QtWebEngine), `geographiclib`, `pyproj`, `geoip2`, `requests`/`httpx`. Python ≥3.12, packaged via Podman.

**Phased roadmap:** (1) Python data core — Bitnodes client + node model + WGS84 geodesy, validated headless. (2) Qt Quick 3D MVP — textured WGS84 sphere, instanced markers from live data, orbit camera. (3) Arcs — slerp/geodesic raised connection geometry, ASN coloring, tooltips. (4) Polish — atmosphere shader, night lights, Natural Earth borders, snapshot-diff animation. (5) Optional CesiumJS backend behind the same interface for the scientific-fidelity mode. (6) Integrate as the wallet's network-health diagnostic view.

## Recommendations

Start by building the **pure-Python data and geodesy core** and validating it headless against the live API (`https://bitnodes.io/api/v1/snapshots/latest/`), because that core is reused by every rendering backend and de-risks the most license- and correctness-sensitive work first. Implement conservative polling — given the documented unauthenticated ceiling of **10 requests/day per IP**, fetch at most once per ~10-minute snapshot cadence, honor `ratelimit-remaining`/`retry-after`, and consider a PRO API key (200,000/day) if higher-frequency or per-node endpoints are needed. Implement the exact WGS84 conversion before any pixels are drawn. Then build the **Qt Quick 3D MVP** with an instanced marker layer, and only add arcs once markers are correct. Treat CesiumJS as a **second, optional backend** behind the same interface rather than the default, to avoid taking the Chromium dependency unless the scientific-terrain payoff is needed. Benchmarks that should change the plan: if instanced markers plus arcs cannot sustain 60fps at the real node count on target hardware, move rendering to a **C++ `QQuick3DInstancing`/QRhi** backend; if stakeholders require certified geodetic terrain or imagery streaming, switch the default to **CesiumJS**. Disclose in the UI that peer-connection edges are inferred (the public API does not expose a definitive edge list) and that IP geolocation is approximate.

## Caveats

The Bitnodes public API exposes reachable nodes and their attributes but does **not** provide a clean peer-to-peer edge list, so connection arcs are necessarily inferred or illustrative; this should be stated plainly. On the unauthenticated rate limit, sources seen during research conflicted (a direct fetch of the API page rendered "300 requests per day" while the canonical API v1.0 documentation text and targeted verification both state **10 requests per day**); design for the stricter 10/day figure and read the live `ratelimit-remaining` header at runtime. IP geolocation is inherently imprecise and tends to resolve to population centers, so node positions are approximate. QtWebEngine's Chromium dependency is large and updates frequently with security patches, a real maintenance and packaging burden for a Podman-shipped wallet. Qt Quick 3D instancing was a Tech Preview in Qt 6.1 but is mature in current Qt 6; verify API stability against your pinned Qt version. Finally, the QRhi family of classes carries only limited source/binary compatibility guarantees across Qt minor releases, so a C++/QRhi backend should be pinned to a known Qt version.

## Source Reference

**Bitnodes**
- Repository: https://github.com/ayeowch/bitnodes
- Crawler source: https://github.com/ayeowch/bitnodes/blob/master/crawl.py
- License (MIT): https://github.com/ayeowch/bitnodes/blob/master/LICENSE
- Wiki / methodology: https://github.com/ayeowch/bitnodes/wiki
- Redis schema: https://github.com/ayeowch/bitnodes/wiki/Redis-Data
- API documentation: https://bitnodes.io/api/
- Latest snapshot: https://bitnodes.io/api/v1/snapshots/latest/

**Qt Quick 3D**
- Module index: https://doc.qt.io/qt-6/qtquick3d-index.html
- Instanced rendering: https://doc.qt.io/qt-6/quick3d-instancing.html
- Instancing QML type: https://doc.qt.io/qt-6/qml-qtquick3d-instancing.html
- QQuick3DInstancing: https://doc.qt.io/qt-6/qquick3dinstancing.html
- QQuick3DGeometry: https://doc.qt.io/qt-6/qquick3dgeometry.html
- Custom Geometry example: https://doc.qt.io/qt-6/qtquick3d-customgeometry-example.html
- Custom Instanced Rendering example: https://doc.qt.io/qt-6/qtquick3d-custominstancing-example.html
- Instancing blog (1M cubes @60fps): https://www.qt.io/blog/qtquick3d-instanced-rendering
- PySide6 QtQuick3D: https://doc.qt.io/qtforpython-6/PySide6/QtQuick3D/index.html
- PySide6 intro example: https://doc.qt.io/qtforpython-6/examples/example_quick3d_intro.html
- qtquick3d source: https://github.com/qt/qtquick3d

**QRhi / OpenGL / Widgets**
- QRhi: https://doc.qt.io/qt-6/qrhi.html
- QRhiWidget: https://doc.qt.io/qt-6/qrhiwidget.html
- Simple/Cube RHI Widget examples: https://doc.qt.io/qt-6/qtwidgets-rhi-cuberhiwidget-example.html
- Graphics overview: https://doc.qt.io/qt-6/topics-graphics.html
- Qt 6.7 release (QRhiWidget, QQuickRhiItem): https://www.qt.io/blog/qt-6.7-released
- Graphics in Qt 6 (QRhi): https://www.qt.io/blog/graphics-in-qt-6.0-qrhi-qt-quick-qt-quick-3d

**CesiumJS**
- Repository: https://github.com/CesiumGS/cesium
- License (Apache-2.0): https://github.com/CesiumGS/cesium/blob/main/LICENSE.md
- Platform page: https://cesium.com/platform/cesiumjs/
- Ellipsoid (WGS84): https://cesium.com/learn/cesiumjs/ref-doc/Ellipsoid.html
- Cartographic: https://cesium.com/learn/cesiumjs/ref-doc/Cartographic.html
- GeographicProjection / WebMercatorProjection: https://cesium.com/learn/cesiumjs/ref-doc/GeographicProjection.html , https://cesium.com/learn/cesiumjs/ref-doc/WebMercatorProjection.html

**QWebChannel / QtWebEngine**
- PySide6 QtWebChannel: https://doc.qt.io/qtforpython-6/PySide6/QtWebChannel/index.html
- QWebChannel JS API: https://doc.qt.io/qtforpython-6/overviews/qtwebchannel-javascript.html
- WebEngine licensing: https://doc.qt.io/qt-6/qtwebengine-licensing.html

**Geodesy**
- WGS84 (NGA): https://earth-info.nga.mil/index.php?dir=wgs84&action=wgs84
- TR8350.2 PDF: https://gis-lab.info/docs/nima-tr8350.2-wgs84fin.pdf
- GeographicLib: https://geographiclib.sourceforge.io/
- GeographicLib geodesics (Python): https://geographiclib.sourceforge.io/html/python/geodesics.html
- Karney 2013 DOI: https://doi.org/10.1007/s00190-012-0578-z
- Shoemake 1985 (slerp) DOI: https://doi.org/10.1145/325165.325242
- PROJ: https://proj.org/
- pyproj: https://pyproj4.github.io/pyproj/
- deck.gl ArcLayer: https://deck.gl/docs/api-reference/layers/arc-layer

**IP geolocation**
- MaxMind GeoIP/GeoLite2: https://dev.maxmind.com/geoip/
- GeoLite2 free data: https://dev.maxmind.com/geoip/geolite2-free-geolocation-data/
- geoip2 Python: https://github.com/maxmind/GeoIP2-python , https://geoip2.readthedocs.io/

**Imagery / vectors**
- NASA Visible Earth: https://visibleearth.nasa.gov/
- NASA NEO (Blue Marble NG + topo/bathy): https://neo.gsfc.nasa.gov/
- Natural Earth: https://www.naturalearthdata.com/
- Natural Earth vector (GitHub): https://github.com/nvkelso/natural-earth-vector
- Natural Earth GeoJSON: https://github.com/martynafford/natural-earth-geojson

**Reference globe implementations**
- KDE Marble (LGPL 2.1): https://github.com/KDE/marble , https://marble.kde.org/
- pyEarth (Qt/OpenGL/Python): https://github.com/afourmy/pyEarth
- QGlobe (Qt/Irrlicht): https://github.com/3DGISKing/QGlobe