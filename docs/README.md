# BANKON Documentation

**BANKON is an effortless, modular extension for a running Bitcoin Core** that provides
non-custodial **Wallet-as-a-Service (WaaS)** and Bitcoin services (diagnostics,
multi-node, wallet provisioning). Wallets follow the **BTC Standard**
(https://github.com/cypherpunk2048): non-custodial, client-side keys, BIP39/32 recovery.

## Contents
> **[NAV.md](NAV.md)** — the master index of *every* BANKON doc, categorized.

| Doc | What it covers |
|-----|----------------|
| [NAV.md](NAV.md) | **Master navigation** — every doc, categorized, with one-line hooks |
| [STRATEGY.md](STRATEGY.md) | **Strategy** — north-star, critical path to a shipped Bitcoin WaaS, long-term phases |
| [QTbankonQT.md](QTbankonQT.md) | **Complete BANKON QT reference** — the desktop app's architecture, tabs, and design-guide review |
| [roadmap.md](roadmap.md) | BANKON QT roadmap + todo (native → allchain → 3D globe) |
| [getting-started.md](getting-started.md) | Install, `bankon up`, the URLs, one-click Core install |
| [architecture.md](architecture.md) | Multi-node design, non-custodial model, data flow |
| [wallets.md](wallets.md) | Wallet types, BTC Standard, create → sign → send, multisig |
| [api.md](api.md) | WaaS + Console HTTP API reference |
| [console.md](console.md) | The diagnostics Console (tabs, levels, RPC catalog, Network topology map + node actions) |
| [ragebtc.md](ragebtc.md) | **RAGEbtc** — rageRPC (accelerated, self-protecting RPC controller) + the RAGE handoff to pgvectorscale |
| [security.md](security.md) | Non-custodial guarantees, auth, backups, offline client |
| [operations.md](operations.md) | Launcher, systemd, monitoring, multi-node, pruning |
| [testing.md](testing.md) | The test suite (sign, e2e, multisig) |
| [waas-accuracy.md](waas-accuracy.md) | **Accurate WaaS positioning** — proven vs built vs gated vs future |
| [wallet-categories.md](wallet-categories.md) | All Bitcoin Core wallet RPCs, categorized |
| [bitcoin-core-rpc.md](bitcoin-core-rpc.md) | Local Bitcoin Core RPC command reference |
| [upstream/](upstream/README.md) | Local copies of key Bitcoin Core docs (descriptors, PSBT, multisig, …) |
| [inspiration-bitnodes.md](inspiration-bitnodes.md) | Bitnodes review + local source (`reference/bitnodes/`, MIT) — ideas BANKON can borrow |
| [qt/Master Architect Guide](qt/Qt%206%20Desktop%20Wallet%20with%20Bitcoin%20Core%20Anchor%20for%20BANKON%20Chain-Agnostic%20WaaS_%20Master%20Architect%20Guide.md) | **Aspirational** Qt 6 / chain-agnostic wallet architecture (RPC+ZMQ, OP_RETURN anchor, ChainAdapter). Bitcoin foundation being built incrementally |
| [qt/Engineering Reference](qt/bankon_qt_engineering_reference.md) | **Aspirational** Qt 6 house style, QML tooling chain, LGPL licensing matrix (read against BANKON's GPLv3+MIT) |
| [ROADMAP.md](ROADMAP.md) | Phased build plan + status |
| [TODO.md](TODO.md) | Prioritized task list |
| [../POLICY.md](../POLICY.md) | Licensing & encryption policy (GPLv3 + MIT/BSD, user sovereignty, OS foundations) |

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
