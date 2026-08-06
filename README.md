# BANKON

> ## ⚠️ INCOMPLETE VERSION — DIAGNOSTIC USE ONLY
> This is a work-in-progress build. It is published for **node diagnostics and
> observation only** — do not rely on it for custody, mainnet transaction workflows,
> or production deployments. Interfaces, data formats, and safety gates are still
> changing without notice.

[![Client crypto: GPLv3](https://img.shields.io/badge/client%20crypto-GPLv3-9945FF.svg?logo=gnu&logoColor=white)](LICENSE.GPLv3)
[![Infrastructure: MIT/BSD-family](https://img.shields.io/badge/infra-MIT%20%2F%20BSD--family-8247E5.svg?logo=opensourceinitiative&logoColor=white)](LICENSE.MIT)
[![Bitcoin Core](https://img.shields.io/badge/Bitcoin%20Core-v31-F7931A.svg?logo=bitcoin&logoColor=white)](https://bitcoincore.org)
[![Node.js](https://img.shields.io/badge/Node.js-22-0AC18E.svg?logo=nodedotjs&logoColor=white)](https://nodejs.org)
[![Wallets: non-custodial](https://img.shields.io/badge/wallets-non--custodial-627EEA.svg?logo=ledger&logoColor=white)](docs/security.md)
[![BTC Standard](https://img.shields.io/badge/BTC-Standard-F7931A.svg?logo=bitcoin&logoColor=white)](https://github.com/cypherpunk2048)
[![PRs welcome](https://img.shields.io/badge/PRs-welcome-0033AD.svg?logo=github&logoColor=white)](https://github.com/cypherpunk2048)

> ### ⚡ Effortless — one command
> ```bash
> ~/bankon-tools/bankon up
> ```
> Attaches BANKON to your running Bitcoin Core →
> **WaaS** http://127.0.0.1:8088 · **Console** http://127.0.0.1:8090
> No node yet? `~/bankon-tools/bankon install-core && bitcoind && ~/bankon-tools/bankon up`

**An effortless, modular extension for a running Bitcoin Core** — non-custodial
**Wallet-as-a-Service (WaaS)** plus Bitcoin services (diagnostics, multi-node, wallet
provisioning). Wallets follow the **BTC Standard**
([github.com/cypherpunk2048](https://github.com/cypherpunk2048)): non-custodial,
client-side keys, BIP39/32 recovery.

```bash
~/bankon-tools/bankon up      # attaches WaaS + Console to your running node
# → WaaS (create wallets):  http://127.0.0.1:8088
# → Console (diagnostics):  http://127.0.0.1:8090
```

Keys are minted in your browser and **never** touch the server. The node only ever holds
**watch-only** descriptors and builds **unsigned** PSBTs — you sign locally.

## Exact BANKON creation

**Bring up the stack from scratch:**
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
| `bankon-waas/` | WaaS API + UI + client keygen/sign + offline client |
| `bankon-console/` | read-only diagnostics dashboard (:8090) |
| `bankon-qt/` + `bankon-qt.sh` | native Qt diagnostics UI |
| `bankon-nodes.sh` · `bankon-node-mode.sh` | multi-node + prune-size control |
| `bankon-diag.sh` · `bankon-monitor.sh` | diagnostics + health watchdog |
| `bankon-backup.sh` | encrypted registry backup |
| `shared/security.mjs` | API auth + rate limiting |
| `systemd/` | service + timer units |
| `docs/` | full documentation set |

## Status
Phases 0–5 built and verified (signing, regtest e2e, and 2-of-3 multisig tests all pass).
Remaining items are gated on the user (free local disk → launch pruned node; live mainnet
send needs deposited funds + full sync). See [ROADMAP.md](docs/ROADMAP.md) and [TODO.md](docs/TODO.md).

## License & policy
**Dual-licensed by component** (see [POLICY.md](POLICY.md) for the rationale):
- **Client-facing encryption** (keygen, signing, offline client) → **GNU GPLv3** ([LICENSE.GPLv3](LICENSE.GPLv3)) — copyleft, the "PGP is GPL" argument: like [GnuPG](https://gnupg.org), code that holds your keys stays free and auditable.
- **Everything else** (infrastructure, APIs, ops) → **MIT** ([LICENSE.MIT](LICENSE.MIT)) — a BSD/MIT-family permissive license; BANKON also builds on BSD/MIT software (Bitcoin Core, Node, Express, `@scure`/`@noble`).

**User sovereignty:** you own your filesystem and may do anything you deem appropriate with it — no license term here restricts what you do to your own files. BANKON is non-custodial and never phones home.

**Run it on free foundations:** [Alpine](https://alpinelinux.org/downloads/) · [Debian](https://www.debian.org/distrib/) · [OpenBSD](https://www.openbsd.org/).

Wallets implement the **BTC Standard** — https://github.com/cypherpunk2048
Summary of the split: [LICENSE](LICENSE).
