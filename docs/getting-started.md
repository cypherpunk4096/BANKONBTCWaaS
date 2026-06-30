# Getting Started

## Prerequisites
- A running **Bitcoin Core** node (v31) with RPC enabled (cookie auth in `~/.bitcoin/.cookie`).
- **Node.js** (v22+) and npm — used by the WaaS API and Console.
- Don't have Bitcoin Core? `bankon install-core` fetches v31, verifies SHA256SUMS, and installs it.

## 1. Bring BANKON up
```bash
~/bankon-tools/bankon up
```
This detects the running node, installs npm deps on first run, and starts:
- **WaaS** — http://127.0.0.1:8088 (create/manage wallets)
- **Console** — http://127.0.0.1:8090 (read-only diagnostics)

## 2. Check health
```bash
~/bankon-tools/bankon status     # node sync %, peers, services up?
~/bankon-tools/bankon doctor     # bitcoind/node/npm/RPC/cookie checks
```

## 3. Create your first wallet
Open http://127.0.0.1:8088, click **Use BTC Standard defaults**, then **Generate**.
- Write down the recovery phrase (the only copy — BANKON can't recover it).
- Click **Register watch-only with BANKON** — the node now tracks it.
- See [wallets.md](wallets.md) for receiving, signing, and sending.

## One-click Bitcoin Core install
```bash
~/bankon-tools/bankon install-core
# downloads bitcoin-31.0, verifies SHA256SUMS, extracts to ~/bitcoin-31.0,
# writes a default ~/.bitcoin/bitcoin.conf. Then:  bitcoind  &&  bankon up
```

## Other entry points
```bash
~/bankon-tools/bankon qt          # native Qt diagnostics UI (auto-installs PySide6)
~/bankon-tools/bankon stop        # stop BANKON services (node keeps running)
```

## Run as background services (optional)
```bash
~/bankon-tools/systemd/install-units.sh    # user systemd units; see operations.md
```
