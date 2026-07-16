# BANKON — shell scripts (installers & launchers)

A single reference for every `.sh` in the BANKON stack (plus the sibling **ICE** and
**OpenMind** helpers). All are plain `bash`, run from a terminal, and stream their logs
to that terminal. None need root unless noted.

> Convention: run from the repo root, e.g. `./bankon-qt.sh`. Most accept `--help`.

---

## 📦 Installer (one-shot setup)

| Script | What it does | Notes |
|--------|--------------|-------|
| `bankon.sh` | **THE installer** — prereqs (node·npm·python3·pip·curl) → Bitcoin Core v31 (SHA256-verified) → WaaS/Console `npm install` → Qt PySide6 → `qrencode` → optional systemd units → `bankon doctor`. Idempotent; orchestrates the pieces, duplicates none. | `--only core,waas,…` · `--dry-run` · `--yes` · `--help`. Per-OS pkg map (apt·apk·pkg_add·brew) for the bankonOS seed targets. Run: `./bankon.sh` then `./bankon up`. |

## 🚀 Launchers (UI / WaaS)

| Script | What it does | Notes |
|--------|--------------|-------|
| `bankon-qt/bankon.sh` | **Simple foreground launcher for the BANKON Qt UI** — CLI flags, sane defaults, in-terminal logs. **Start here.** | `--btc-bin` `--datadir` `--waas-url` `--peers` `--gpu` `--help`; software rendering by default |
| `bankon-qt.sh` | Launch the BANKON Qt diagnostics UI (original launcher). | |
| `bankon-eth.sh` | Launch the **BANKON.ETH** WaaS (EVM twin of bankon-waas), standalone. | |
| `bankon-algo.sh` | Launch the **BANKON ALGO** WaaS (Algorand twin of bankon-waas), standalone. | |
| `bankon dexy` | Launch **⟲ DEXY** — sovereign-custody BTC liquidity mover (CEX→DEX projection · native-BTC DEX accumulation · ARRBY EVM DEX→DEX) on :8091. | Opt-in like algo/eth; quotes/plans only — destination is always YOUR address, DEXY never signs |

**Quick start**
```bash
cd bankon-qt && ./bankon.sh            # BANKON BTC WaaS UI, logs here, Ctrl-C to stop
./bankon.sh --help                     # all flags + defaults
```

---

## 🖧 Node & wallet management

| Script | What it does |
|--------|--------------|
| `bankon-nodes.sh` | BANKON **multi-node manager**. |
| `bankon-node-mode.sh` | Set the Bitcoin Core **blockchain-size mode** (edits `bitcoin.conf`; makes a timestamped backup first). |
| `bankon-wallet.sh` | BANKON **wallet provisioning** layer for Bitcoin Core (v31). |
| `bankon-backup.sh` | Encrypted **backup/restore** of BANKON's watch-only state. |

---

## 🔬 Diagnostics & monitoring

| Script | What it does |
|--------|--------------|
| `bankon-diag.sh` | BANKON **scientific diagnostics** for Bitcoin Core (v31). |
| `bankon-monitor.sh` | **Health watchdog** for the BANKON stack — run on a timer. |

---

## ♻️ Duplication as a Service (DaaS) — a WaaS sub-component

BANKON offers the expensive-to-rebuild Bitcoin **index** as a portable bundle, so a peer
skips the long verify/rebuild (the chain is in the ~1TB zone — real hardware + electricity +
time to recreate). The local machine acts as a UI + external blockchain-reference device.

| Script | What it does |
|--------|--------------|
| `bankon-index-export.sh` | Export `~/.bitcoin/indexes/*` + a manifest (height, best-block hash, Core version, sha256) as `bankon-index-<h>-<hash>.tar.zst`. `--stop` (safe) · `--force` · `--datadir` · `--out` · `--gzip`. Import: extract into another node's datadir while its `bitcoind` is stopped, then start — it validates against its own blocks. |

## ⚙️ Install / systemd

| Script | What it does | Root? |
|--------|--------------|-------|
| `systemd/install-units.sh` | Install BANKON **user systemd units**. | No root — user units |

---

## 🧪 Regtest tests (`bankon-waas/`)

Each spins up an isolated regtest node — safe, no mainnet exposure.

| Script | What it does |
|--------|--------------|
| `bankon-waas/test-e2e-regtest.sh` | Full **non-custodial WaaS loop** on an isolated regtest node. |
| `bankon-waas/test-multisig-regtest.sh` | Register & fund a **2-of-3 watch-only multisig** on regtest. |
| `bankon-waas/test-anchor-regtest.sh` | **OP_RETURN canonical anchor** roundtrip on an isolated regtest. |
| `dexy/test/test-dexy-regtest.sh` | **⟲ DEXY e2e**: chain-native pairs from the regtest tip, CEX→DEX projection (fixtures, zero network), sovereign quote gate, HTLC accept, non-custodial guard. |

---

## 🧊 ICE — separate repo (`~/ICE`, github.com/Professor-Codephreak/ice)

The wall between the network and the wallet (CPU/thermal + radio kill-switch). **Needs sudo**
(self-elevates).

| Script | What it does |
|--------|--------------|
| `ice.sh` | Launcher — foreground, sudo prompt + logs in your terminal. `--apply` / `--uninstall` / `--help`. |
| `run.sh` | Legacy launcher (same behaviour). |

```bash
cd ~/ICE && ./ice.sh                   # asks for sudo, opens tray, logs here
```

---

## 🧠 OpenMind — diagnostics (`~/OpenMind/diagnostics`)

Ollama LAN / API helpers, also runnable from OpenMind's Diagnostics tab.

| Script | What it does |
|--------|--------------|
| `curlllama.sh` | LAN discovery + connectivity verifier + curl command map. |
| `sync-ollama-models.sh` | Sync available Ollama models into the OpenCode config. |
| `test-opencode-ollama.sh` | OpenCode + Ollama end-to-end integration check. |

---

*This index lists the launchers/installers only. Application code lives in `bankon-qt/`
(Python/PySide6), `bankon-waas/` (Node), and the WaaS twins.*
