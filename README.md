# BANKON

> ## ⚠️ INCOMPLETE VERSION — DIAGNOSTIC USE ONLY
> This is a work-in-progress build, **pending cypherpunk audit completion**. It is
> published for **node diagnostics and observation only** — do not rely on it for
> custody, mainnet transaction workflows, or production deployments. Interfaces,
> data formats, and safety gates are still changing without notice.

[![Client crypto: GPLv3](https://img.shields.io/badge/client%20crypto-GPLv3-9945FF.svg?logo=gnu&logoColor=white)](LICENSE.GPLv3)
[![Infrastructure: MIT/BSD-family](https://img.shields.io/badge/infra-MIT%20%2F%20BSD--family-8247E5.svg?logo=opensourceinitiative&logoColor=white)](LICENSE.MIT)
[![Bitcoin Core](https://img.shields.io/badge/Bitcoin%20Core-v31-F7931A.svg?logo=bitcoin&logoColor=white)](https://bitcoincore.org)
[![Node.js](https://img.shields.io/badge/Node.js-22-0AC18E.svg?logo=nodedotjs&logoColor=white)](https://nodejs.org)
[![Wallets: non-custodial](https://img.shields.io/badge/wallets-non--custodial-627EEA.svg?logo=ledger&logoColor=white)](docs/security.md)
[![BTC Standard](https://img.shields.io/badge/BTC-Standard-F7931A.svg?logo=bitcoin&logoColor=white)](https://github.com/cypherpunk2048)
[![PRs welcome](https://img.shields.io/badge/PRs-welcome-0033AD.svg?logo=github&logoColor=white)](https://github.com/cypherpunk2048)

> ### ⛓ Bitcoin Core is REQUIRED
> BANKON is **not** a standalone wallet or a hosted service — it is an extension that
> attaches to **your own running [Bitcoin Core](https://bitcoincore.org) full node**
> (v31 recommended). Every wallet, diagnostic, and API here reads from and talks to
> that node; without `bitcoind` running there is nothing to attach to. Don't have it?
> The installer sets it up for you, SHA256-verified: `./bankon.sh --only core`
> (or `./bankon install-core`). Your node, your chain, your keys.
>
> **Shipped version, pinned:** the exact Bitcoin Core this release runs against is
> forked at [cypherpunk4096/bitcoin](https://github.com/cypherpunk4096/bitcoin)
> (the immutable `v31.0` tag, [releases/tag/v31.0](https://github.com/cypherpunk4096/bitcoin/releases/tag/v31.0)) —
> an auditable snapshot that can't drift under BANKON BTC WaaS v0.0.2.

> ### ⚡ Effortless — one command
> ```bash
> ~/bankon-tools/bankon up
> ```
> Attaches BANKON to your running Bitcoin Core →
> **WaaS** http://127.0.0.1:8088 · **Console** http://127.0.0.1:8090
> First time on this machine? → [Install](#install) (one command too).

## Install

**Prerequisite: Bitcoin Core.** BANKON attaches to a running `bitcoind` — bring your own
node, or let step one below install v31 for you (SHA256SUMS-verified). Expect full-node
disk requirements (~900 GB and growing; pruned mode supported — see [PRUNING.md](PRUNING.md)).

From a clean machine to a ready stack — clone, one-shot install, run:

```bash
git clone https://github.com/cypherpunk4096/BANKONBTCWaaS.git ~/bankon-tools
cd ~/bankon-tools
./bankon.sh        # THE installer: prereqs → Bitcoin Core v31 (SHA256-verified)
                   #   → WaaS/Console deps → Qt (PySide6) → QR → doctor check
bitcoind           # start the node (skip if yours is already running)
./bankon up        # attach → WaaS :8088 · Console :8090
```

The installer is **idempotent and non-destructive**: it skips what's already present,
prints every action, and never touches your existing node or wallets. Tune it:

```bash
./bankon.sh --dry-run                 # print the full plan, change nothing
./bankon.sh --only waas,console,qt    # already have a node? install just the services
./bankon.sh --only core               # just Bitcoin Core (SHA256-verified download)
./bankon.sh --yes                     # unattended (scripts / CI)
```

Works on the bankonOS seed targets — package maps for **apt · apk · pkg_add · brew**
(Debian/Ubuntu · Alpine · OpenBSD · macOS). No root needed except for OS packages.
Verify the result any time with `./bankon doctor`. Every script is catalogued in
[installers.md](installers.md).

**An effortless, modular extension for a running Bitcoin Core** — non-custodial
**Wallet-as-a-Service (WaaS)** plus Bitcoin services (diagnostics, multi-node, wallet
provisioning). Wallets follow the **BTC Standard**
([github.com/cypherpunk2048](https://github.com/cypherpunk2048)): non-custodial,
client-side keys, BIP39/32 recovery.

Keys are minted in your browser and **never** touch the server. The node only ever holds
**watch-only** descriptors and builds **unsigned** PSBTs — you sign locally.

## Diagnostics first

Under the audit warning above, the **diagnostics surfaces are the supported use** today.
They are read-only against your node and honest about their sources — every number states
whether it is the **live RPC truth** or a **log-window event count**, because those diverge
(an airgap toggle piles up connect *events* while live peers stay small; both are correct).

- **Console** (`:8090`) — web dashboard: node health, sync, filesystem, connection
  activity parsed from `debug.log` (works even during the IBD RPC choke).
- **Qt desktop UI** (`bankon qt`) — the full instrument panel:
  - **Overview** — sync, mempool (count · MvB · min sat/vB), datadir card with a
    **log-scale disk-runway projection** that compounds chain growth (+10 %/yr, capped
    at the full-blocks ceiling) instead of pretending growth is linear.
  - **₿TC.oracle** — the clock kept on a Bitcoin block; every blocktime figure exact
    Decimal at 18 dp; anti-clockblock multi-source cross-checks; per-block science
    (`getblockstats`) with integer-satoshi economics.
  - **🧊 I.C.E.** (the tool — *Intrusion Countermeasures Electronics*; 🖤 blackICE is
    a theme) — CPU-heat + radio wall (AIRGAP), 🛡 ufw firewall diagnostics with
    one-click installer, geo/IP forensics, and a **live blockchain transaction
    monitor**: parses the node's ZMQ `rawtx` feed locally (txid, vsize, output sums
    in integer sats — verified exact against Core RPC).
  - **Net Map / Geo Map / Network log** — peers on a spinning globe that **opens
    centred on your own node**: per-point data labels + hover cards (ip · city,
    country · ±km GeoIP accuracy · ping · live ▼/▲ B/s), comet packet flow whose
    brightness encodes link quality, 🎯 accuracy rings, 🕛 UTC/date-line overlay,
    🌐 ₿itnodes-style world-nodes view, draggable/dockable 🏠 node + 🪙 price cards,
    ⛶ fullscreen with retract, extra watcher-globe windows, and a 📡 live feed
    column (connections · mempool Δ · blocks). Connection events carry BIP324
    transport + role detail; three numeric display modes (human · scientific ·
    18-dec exact).
  - **🛠 Admin popup** — every toolbar toggle + admin actions in one resizable
    window with launcher-style ⚓ DOCK / 📞 CALL choreography (finds the console
    and the GTK launcher across displays; dock position remembered).
  - **🖥 Control** — node ▶/■ with pressed-state truth, optional pruned-node
    runtime, host OS + thermal cards, service probes, and **🩸 monit0r**.
- **⛽ fee log + 🩸 monit0r** — the always-on Console samples fees (5 min) and
  every process's RSS (1 min) into bounded local logs, served with computed stats
  and leak verdicts at `/api/fees` and `/api/monit0r`. Measured locally — never an
  external API.
- **Watchdog + doctor** — `bankon-monitor.sh` (systemd timer) and `bankon doctor`.

## Exact BANKON creation

**Bring up the stack from scratch** (or just run [`./bankon.sh`](#install) — it does 1–2 for you):
```bash
# 1. Bitcoin Core (skip if already installed/running)
~/bankon-tools/bankon install-core      # downloads v31, verifies SHA256SUMS, installs
bitcoind                                 # daemon=1 is set in the default conf

# 2. Attach BANKON to the running node
~/bankon-tools/bankon up                 # → WaaS :8088, Console :8090
~/bankon-tools/bankon status             # confirm node + services
```

**Create a wallet (BTC Standard) — the exact non-custodial flow:**
```bash
# A) In the browser (recommended): http://127.0.0.1:8088
#    → "Use BTC Standard defaults" → Generate → write down the phrase → Register.

# B) Headless / scripted — mint client-side, then register the PUBLIC descriptor:
cd ~/bankon-tools/bankon-waas
node keygen.mjs native-segwit            # prints mnemonic (KEEP LOCAL) + external/internal descriptors
curl -s -X POST http://127.0.0.1:8088/api/wallet \
  -H 'content-type: application/json' \
  -d '{"type":"native-segwit","fingerprint":"<fp>","xpub":"<xpub>",
       "external":"<ext-desc>","internal":"<int-desc>","owner":"alice"}'
```

**Receive → sign → send (private key never leaves the client):**
```bash
# receive address
curl -s http://127.0.0.1:8088/api/wallet/<name>/receive
# build UNSIGNED psbt on the node
curl -s -X POST http://127.0.0.1:8088/api/wallet/<name>/send \
  -H 'content-type: application/json' -d '{"to":"bc1q...","amountBtc":0.001,"feeRate":8}'
# sign LOCALLY (browser Send panel, or offline-client.html, or sign.mjs), then:
curl -s -X POST http://127.0.0.1:8088/api/broadcast \
  -H 'content-type: application/json' -d '{"hex":"<signed-tx-hex>"}'
```

The full loop is verified end-to-end (incl. multisig) by the regtest suite — see
[docs/testing.md](docs/testing.md). For what's built vs. pending, see the
[ROADMAP](docs/ROADMAP.md) and [TODO](docs/TODO.md).

## Documentation
**[USAGE.md](USAGE.md)** — complete startup guide (web UI · Qt · CLI) · full index → **[docs/NAV.md](docs/NAV.md)** · desktop app → **[docs/QTbankonQT.md](docs/QTbankonQT.md)**

| Guide | |
|-------|-|
| [Getting Started](docs/getting-started.md) | install, `bankon up`, one-click Core install |
| [Architecture](docs/architecture.md) | multi-node design + non-custodial flow |
| [Wallets](docs/wallets.md) | types, BTC Standard, create→sign→send, multisig |
| [API](docs/api.md) | WaaS + Console HTTP reference |
| [Console](docs/console.md) | the diagnostics dashboard |
| [SECURITY.md](SECURITY.md) | **threat model, trust boundaries, disclosure policy** |
| [bankon-vault](bankon-vault/README.md) | **the chain-agnostic vault** — BTC signature access + gating, sign-don't-export, ICE frozen storage |
| [Origins](docs/origins.md) | the decades-old lineage (bankonme → … → cypherpunk2048) + [archive](docs/bankonme-archive.txt) |
| [Security](docs/security.md) | guarantees, auth, backups, offline client |
| [Operations](docs/operations.md) | launcher, systemd, monitoring, pruning |
| [Testing](docs/testing.md) | the regtest + signing test suites |

Planning docs: [ROADMAP.md](docs/ROADMAP.md) · [TODO.md](docs/TODO.md) · [PRUNING.md](PRUNING.md)

## Commands
```bash
bankon up | status | stop | doctor | install-core | qt
```

## Module map
| Path | Role |
|------|------|
| `bankon` | launcher |
| `bankon-waas/` | ₿TC WaaS API + UI + client keygen/sign + offline client |
| `bankon-console/` | read-only diagnostics dashboard (:8090) |
| `bankon-qt/` + `bankon-qt.sh` | native Qt diagnostics UI (Overview · oracle · ICE · maps) |
| `dexy/` | ⟲ DEXY — sovereign-custody BTC liquidity mover (:8091, opt-in) + golden-ratio toll |
| `bankon-vault/` | chain-agnostic vault — signature-gated secrets, sign-don't-export |
| `bankon-ord/` | optional ordinals / inscriptions / runes (wraps official `ord`, gated) |
| `bankon-eth/` · `bankon-algo/` | the EVM and Algorand WaaS twins (standalone, non-custodial) |
| `bankonos/` | sovereign Bitcoin workstation provisioning |
| `bankon-nodes.sh` · `bankon-node-mode.sh` | multi-node + prune-size control |
| `bankon-diag.sh` · `bankon-monitor.sh` | diagnostics + health watchdog |
| `bankon-backup.sh` | encrypted registry backup |
| `geoip/` | offline GeoLite2 (Net Map / Geo Map — no external API at runtime) |
| `shared/security.mjs` | API auth + rate limiting |
| `systemd/` | service + timer units |
| `docs/` | full documentation set |

## Status
Phases 0–5 built and verified (signing, regtest e2e, and 2-of-3 multisig tests all pass) —
but per the notice above, treat everything beyond **diagnostics** as unaudited until the
cypherpunk audit completes. Remaining items are gated on the user (free local disk →
launch pruned node; live mainnet send needs deposited funds + full sync).
See [ROADMAP.md](docs/ROADMAP.md) and [TODO.md](docs/TODO.md).

## License & policy
**Dual-licensed by component** (see [POLICY.md](POLICY.md) for the rationale):
- **Client-facing encryption** (keygen, signing, offline client) → **GNU GPLv3** ([LICENSE.GPLv3](LICENSE.GPLv3)) — copyleft, the "PGP is GPL" argument: like [GnuPG](https://gnupg.org), code that holds your keys stays free and auditable.
- **Everything else** (infrastructure, APIs, ops) → **MIT** ([LICENSE.MIT](LICENSE.MIT)) — a BSD/MIT-family permissive license; BANKON also builds on BSD/MIT software (Bitcoin Core, Node, Express, `@scure`/`@noble`).

**User sovereignty:** you own your filesystem and may do anything you deem appropriate with it — no license term here restricts what you do to your own files. BANKON is non-custodial and never phones home.

**Run it on free foundations:** [Alpine](https://alpinelinux.org/downloads/) · [Debian](https://www.debian.org/distrib/) · [OpenBSD](https://www.openbsd.org/).

Wallets implement the **BTC Standard** — https://github.com/cypherpunk2048
Summary of the split: [LICENSE](LICENSE).
