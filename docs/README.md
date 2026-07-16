# BANKON Documentation

**BANKON is an effortless, modular extension for a running Bitcoin Core** that provides
non-custodial **Wallet-as-a-Service (WaaS)** and Bitcoin services (diagnostics,
multi-node, wallet provisioning). Wallets follow the **BTC Standard**
(https://github.com/cypherpunk2048): non-custodial, client-side keys, BIP39/32 recovery.

## Contents

**[NAV.md](NAV.md) is the master index** of every BANKON doc, categorized with one-line hooks —
start there. Quick jumps: [STRATEGY](STRATEGY.md) (why/order) · [ROADMAP](ROADMAP.md) (status) ·
[architecture](architecture.md) (design) · [getting-started](getting-started.md) ·
[api](api.md) · [security](security.md) · [operations](operations.md) (incl. pruning) ·
[ICE](ICE.md) · [ragebtc](ragebtc.md).

## The 30-second version
```bash
~/bankon-tools/bankon up      # attaches WaaS + Console to your running node
# → WaaS (create wallets):  http://127.0.0.1:8088
# → Console (diagnostics):  http://127.0.0.1:8090
```
Keys are minted in your browser and **never** touch the server; the node only ever
holds **watch-only** descriptors and builds **unsigned** PSBTs. You sign locally.

## Module map (`~/bankon-tools/`)
- `bankon` — launcher (up/status/stop/doctor/install-core/qt)
- `bankon-waas/` — WaaS API + browser UI + client keygen/sign + offline client
- `bankon-console/` — read-only diagnostics dashboard
- `bankon-qt/` + `bankon-qt.sh` — native Qt diagnostics UI
- `bankon-nodes.sh`, `bankon-node-mode.sh` — multi-node + prune-size control
- `bankon-diag.sh`, `bankon-monitor.sh` — diagnostics + health watchdog
- `bankon-backup.sh` — encrypted registry backup
- `shared/security.mjs` — API auth + rate limiting
- `systemd/` — service + timer units
- `PRUNING.md`, `docs/` (guides + `ROADMAP.md` + `TODO.md`)
