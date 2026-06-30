# BANKON Console

A read-only, browser-based diagnostics dashboard over the background node(s).
http://127.0.0.1:8090 — renders reliably on this host's GPU (unlike `bitcoin-qt`, which
black-screens; a native [Qt version](#qt-version) exists too).

## Detail levels
A global **Basic / Detailed / Expert** toggle controls how much each tab shows:
- **Basic** — the essentials (sync bar + key cards).
- **Detailed** — full grids, tables, charts, accordions.
- **Expert** — adds raw RPC JSON accordions under each section.

## Tabs
A **header status pill** auto-polls `/api/health` (fast, 6s) and recognizes the running
node on the standard port: ● running · block N (green) / booting / validating-busy (amber)
/ stopped (red) — so the dashboard never falsely looks dead during IBD.

| Tab | Shows |
|-----|-------|
| Overview | sync progress, height, peers, mempool, disk, difficulty, uptime |
| **Node** | status (running/booting/busy/down), **Start/Stop** Bitcoin Core, live `debug.log` bootup/sync stream |
| Blocks | latest blocks + lookup by height/hash (+ block stats at Detailed) |
| Mempool | size/memory + fee-rate histogram + fee estimate |
| **Network** | **live topology map** + node chooser + peer table + peers-by-version + net totals (see below) |
| Indexes | txindex quality (synced / lag / PASS-DEGRADED) + the "crunching" indexer activity |
| **BTC.oracle** | the clock kept on a Bitcoin block — avg block time, mesh canvas, auto-measured new-block log (JSON/JSONL/CSV) |
| Wallets | loaded wallets, watch-only flags |
| Create Wallet | BTC STANDARD hero → links to the WaaS |
| Reference | every read-only RPC (44, 6 categories), runnable inline with examples |
| **RPC Console** | run any whitelisted read-only RPC + the **rageRPC** controller (tier/RAGE toggle + live breaker readout — see [ragebtc.md](ragebtc.md)) |
| **Monitor** | whitelisted read-only terminal commands (bitcoin-cli + debug.log/system), each a one-click button → response |

### Network tab
- **Live topology map** (canvas) — your node at centre, peers radial; link width/colour = traffic,
  **animated packets flow** centre→peer. **Gold ring = ★favourite**, orange = inbound. **Click any
  peer node** → a diagnostics panel with its full `getpeerinfo` + **★ Promote** / **⏏ Boot** actions.
  During IBD (peer RPC choked) it falls back to the **log-based connection-activity ring** so it's
  never bare.
- **Node chooser** — add / 1-try / remove peers (seed list or `ip:port`) to grow connections.
- **Peer table** — addr · subver · dir · ping · **↓ KB/s** · MiB↑↓ · height, with per-row
  **☆/★ Promote** and **⏏ Boot** buttons. The **connections** readout shows `N (out · in) · target ≥ 12`
  (the target is a *floor*, not a cap — exceeding it is healthy).
- **⌂ our node** line — the node's own advertised address(es) + subversion + protocol.
- **Connection activity** — log-based feed (works during the RPC choke).

**Node actions** — *Promote* marks a peer favourite (persisted) **and** keeps a persistent connection
(`addnode`); *Boot* disconnects it now (`disconnectnode`). Both dispatch directly via `bitcoin-cli`
so they work during IBD. Endpoints: `POST /api/node/promote` · `POST /api/node/boot` ·
`GET /api/node/favourites`.

## Safety
Only whitelisted **read-only** RPCs are reachable (`/api/rpc`); spends, `stop`, and
config-writes return HTTP 403. A node selector switches between **full :8332** and
**pruned :8342**. Auto-refresh (5s) covers Overview/Network/Mempool.

## Qt version
```bash
bankon qt        # or: ~/bankon-tools/bankon-qt.sh
```
Native PySide6 app (Overview / Node / Network / Network Map / Mempool / Blocks / Indexes /
BTC.oracle / RPC Console — plus an optional **Geo Map**, toggled from the toolbar, **off by default**
since it needs the GeoIP `.mmdb` files). The **Network Map** is a live QGraphicsScene topology with animated
traffic, click-to-diagnose peers, and Promote/Boot actions; peer-table columns are **drag-resizable
with visible divider grips** (the widths persist across refreshes). The **RPC Console** carries the
same **rageRPC** controller. RPCs run off the UI thread so it never freezes on the IBD lock. The
launcher installs PySide6 on first run and forces software rendering (`QT_OPENGL=software`) for the
Intel HD 3000.
