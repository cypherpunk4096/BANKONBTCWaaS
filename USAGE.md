# BANKON — Usage Guide

Complete guide to starting and using BANKON: the **web UIs**, the **Qt desktop app**, and
**CLI** interaction. BANKON attaches to a running Bitcoin Core; it is non-custodial (your
keys are minted client-side and never sent to the server).

- WaaS (create/manage wallets): **http://127.0.0.1:8088**
- Console (diagnostics + node control): **http://127.0.0.1:8090**
- Qt desktop app: `bankon qt`
- Ports: full node RPC 8332 / P2P 8333 · pruned node RPC 8342 / P2P 8334

---

## 0. One-command start (everything)
```bash
~/bankon-tools/bankon up        # detects the running node, starts WaaS + Console
~/bankon-tools/bankon status    # node sync %, peers, services up?
~/bankon-tools/bankon qt        # launch the Qt desktop app
```
No node yet? `~/bankon-tools/bankon install-core && bitcoind && ~/bankon-tools/bankon up`

---

## 1. Web UI — startup

### Start the servers
```bash
~/bankon-tools/bankon up
# or individually:
cd ~/bankon-tools/bankon-waas    && node server.mjs    # :8088  (BANKON_PORT to change)
cd ~/bankon-tools/bankon-console && node server.mjs    # :8090  (BANKON_CONSOLE_PORT to change)
```
Then open the URLs in a browser. Stop with `~/bankon-tools/bankon stop`.

### WaaS (:8088) — create / receive / send a wallet
1. Click **Use BTC Standard defaults** → **Generate my wallet**. Write down the recovery
   phrase (the only copy — BANKON cannot recover it).
2. Click **Register watch-only with BANKON** (sends only the public descriptor).
3. **Receive**: deposit to the shown address.
4. **Send** (step 4 panel): recipient + amount → pick a fee tier → paste your phrase →
   **Prepare & sign locally** (signs in your browser) → **Broadcast**.

### Console (:8090) — diagnostics + node control
- **Detail toggle** (Basic/Detailed/Expert) and a **refresh-rate** selector
  (off / 10s / 30s / **1 min default** / 5 min) in the header.
- **Header status pill**: ● running (green) / booting / validating (orange) / stopped (red).
- Tabs: Overview · **Node** (Start/Stop + live debug.log) · Blocks · Mempool · **Network**
  (live topology map + node chooser + ★Promote/⏏Boot peer actions) · Indexes · **BTC.oracle** ·
  Wallets · **Create Wallet** · Reference (all 44 read-only RPCs) · **RPC Console** (with the
  **rageRPC** controller) · **Monitor**.
- During IBD, block-level data is served from cache (the node holds the chain lock while
  validating); Mempool/Indexes/Network update live. The Network map falls back to a log-based
  connection-activity ring while the peer RPC is choked, so it's never bare.
- **rageRPC** keeps it all live during IBD — an accelerated, self-throttling RPC controller. See
  **[docs/ragebtc.md](docs/ragebtc.md)** (RAGEbtc).

### Offline / air-gapped client
`http://127.0.0.1:8088/offline-client.html` — or save the file and open it offline. Mints
keys and signs PSBTs with **zero network calls**. Broadcast the resulting hex from any node.

---

## 2. Qt desktop app — startup
```bash
~/bankon-tools/bankon qt          # or: ~/bankon-tools/bankon-qt.sh
```
First run installs **PySide6** (~150 MB, one-time). The launcher forces **software
rendering** with `QT_OPENGL=software` so it doesn't black-screen the Intel HD 3000 (the
reason the node itself runs headless). **Do not** add `LIBGL_ALWAYS_SOFTWARE=1` or
`QT_XCB_FORCE_SOFTWARE_OPENGL=1` — on the HD 3000 they make the window never appear;
`QT_OPENGL=software` alone is correct and loads fast.

Manual launch (equivalent):
```bash
cd ~/bankon-tools
QT_OPENGL=software python3 bankon-qt/bankon_qt.py
```
Tabs: Overview · **Node** (status + Start/Stop + live boot log) · **Network** (peer table with
drag-resizable columns + right-click ★Promote/⏏Boot) · **Network Map** (live topology — animated
traffic, click-to-diagnose, Promote/Boot) · Mempool · Blocks · Indexes · **BTC.oracle** ·
**RPC Console** (with the **rageRPC** controller). An optional **🌍 Geo Map** tab is toggled from the
toolbar — **off by default** (it needs the GeoIP `.mmdb` files; see [geoip/README.md](geoip/README.md)).
Toolbar has a **refresh-rate** combo (1-min
default) and a live node-status label. RPCs run off the UI thread so it never freezes during IBD.
Requires a `DISPLAY` (it's a desktop GUI). Close the window to exit (clean shutdown — all timers,
ZMQ, and workers stopped deterministically).

---

## 3. CLI interaction

### The node (Bitcoin Core)
```bash
export PATH="/home/luvai/bitcoin-31.0/bin:$PATH"
bitcoind                          # start headless (daemon=1 in conf)
bitcoin-cli getblockchaininfo     # chain / sync %
bitcoin-cli getconnectioncount    # peers
bitcoin-cli getmempoolinfo
bitcoin-cli getindexinfo          # txindex progress
bitcoin-cli stop                  # graceful shutdown
```
Full local command reference: [docs/bitcoin-core-rpc.md](docs/bitcoin-core-rpc.md) ·
wallet RPCs categorized: [docs/wallet-categories.md](docs/wallet-categories.md).

### BANKON launcher & tools
```bash
bankon up | status | stop | doctor | install-core | qt

bankon-nodes.sh init-pruned | start pruned | stop pruned | status   # multi-node
bankon-node-mode.sh default|min|generous|full [--apply]             # prune size
bankon-diag.sh [--deep]            # scientific node diagnostics
bankon-monitor.sh                  # health watchdog (exit 1 on warnings)
bankon-backup.sh create | restore <file.enc>                        # encrypted registry backup
```

### WaaS via CLI (headless, non-custodial)
```bash
# 1) mint a wallet client-side (prints mnemonic — keep it local — + descriptors)
cd ~/bankon-tools/bankon-waas && node keygen.mjs native-segwit

# 2) register the PUBLIC descriptor (watch-only) with the API
curl -s -X POST http://127.0.0.1:8088/api/wallet -H 'content-type: application/json' \
  -d '{"type":"native-segwit","fingerprint":"<fp>","xpub":"<xpub>","external":"<ext>","internal":"<int>","owner":"alice"}'

# 3) balance / receive / history
curl -s http://127.0.0.1:8088/api/wallet/<name>/balance
curl -s http://127.0.0.1:8088/api/wallet/<name>/receive
curl -s http://127.0.0.1:8088/api/wallet/<name>/history?n=20

# 4) build UNSIGNED psbt → sign locally → broadcast
curl -s -X POST http://127.0.0.1:8088/api/wallet/<name>/send -H 'content-type: application/json' \
  -d '{"to":"bc1q...","amountBtc":0.001,"feeRate":8}'
# sign with the offline client or sign.mjs, then:
curl -s -X POST http://127.0.0.1:8088/api/broadcast -H 'content-type: application/json' \
  -d '{"hex":"<signed-tx-hex>"}'
```

### Console read-only RPC proxy
```bash
curl -s http://127.0.0.1:8090/api/health                 # node state (running/booting/busy/down)
curl -s http://127.0.0.1:8090/api/catalog                # the 44-RPC read-only catalog
curl -s -X POST http://127.0.0.1:8090/api/rpc -H 'content-type: application/json' \
  -d '{"method":"getblockstats","params":[800000]}'      # any whitelisted read-only RPC
```

### Tests (regtest — isolated, no real funds)
```bash
node bankon-waas/test-sign.mjs                  # signing unit test (segwit+taproot+negative)
bash bankon-waas/test-e2e-regtest.sh            # full send loop e2e
bash bankon-waas/test-multisig-regtest.sh       # 2-of-3 multisig e2e
```

---

## Settings (env vars)
- `BANKON_NODE_HAMMER=1` — opt in to **continuous burst polling** ("node hammer") to keep
  block-level data hot. **Default off (gentle)**: BANKON polls the node lightly and only
  bursts on demand (when you open Blocks and the cache is stale), so it never floods the
  RPC work queue. Node-side headroom: `rpcworkqueue=64` / `rpcthreads=8` (in `bitcoin.conf`,
  applies on node restart).
- `BANKON_WARM_MS` (default 25000) — gentle metric refresh interval (server-side cache).
- `BANKON_API_TOKEN` — require `Authorization: Bearer <token>` on `/api/*` (auth off by default).
- `BANKON_NODE_CONTROL=0` — disable node control (Start/Stop, addnode, Promote/Boot).
- **rageRPC** — `BANKON_RAGE=1` (default) accelerated posture; `BANKON_RPC_MAX_INFLIGHT` (default tier
  **256**), `BANKON_RPC_DISTRESS_MS`, `BANKON_RAGE_CEIL` (RAGE ceiling, default 100000). Live state
  and tuning at `GET`/`POST /api/settings`. Full reference: **[docs/ragebtc.md](docs/ragebtc.md)**.
- **RAGE handoff** — `RAGE_URL` / `RAGE_INGEST_PATH` / `RAGE_TOKEN` (the pgvectorscale ingest at
  `bankon.pythai.net/ragest`; the RAGE engine lives at rage.pythai.net · github.com/gaterage).

## Endpoints of note (Console :8090)
- `/api/oracle` — **BTC.oracle**: average block time (genesis starts the clock) + a
  blocktime-derived bandwidth/poll throttle (`recommendedPollMs`).
- `/api/settings` — **rageRPC** live policy + circuit-breaker state (GET) and runtime tuning (POST):
  `rage`, `maxInflight`/tier, `effInflight` (adaptive cap), `inflight`/`waiting`, `circuitOpen`.
- **Node actions** — `/api/node/addnode` · `/api/node/promote` (favourite + persistent) ·
  `/api/node/boot` (disconnect) · `/api/node/favourites` · `/api/node/peerspeed` · `/api/node/connect-fast`.
- **RAGE** — `/api/rage/status` · `/api/rage/handoff` (node addresses → pgvectorscale).
- `/api/netactivity` — log-based connection activity (powers the Network IBD fallback ring).
- `/api/filesystem` — the node datadir's drive (df + chain size).
- `/api/health`, `/api/recentblocks`, `/api/rpc`, `/api/catalog`, `/api/overview`.

## Notes
- **Non-custodial**: never send a mnemonic/xprv to the API — the server rejects it (HTTP 400).
- **Auth (optional)**: set `BANKON_API_TOKEN` to require `Authorization: Bearer <token>` on `/api/*`.
- More: [docs/README.md](docs/README.md) · [docs/getting-started.md](docs/getting-started.md) ·
  [docs/wallets.md](docs/wallets.md) · [docs/console.md](docs/console.md) · [README.md](README.md).
