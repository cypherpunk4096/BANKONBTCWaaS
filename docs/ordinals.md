# 🜚 Ordinals — inscriptions · runes · sat hunting in BANKON

BANKON's ordinals support is an **optional, isolated** module (`bankon-ord`, wrapping the official
[`ordinals/ord`](https://github.com/ordinals/ord) CLI) surfaced in three places that all drive the
**same gated engine**: the WaaS page's **🜚 Ordinals** tab, the web Console's 🜚 tab, and the Qt
app's **🜚 Ordinals** tab. Nothing else in BANKON depends on it.

## Ordinal theory in one paragraph

Every satoshi is numbered by the order it was mined (**ordinals**), and sats move through
transactions first-in-first-out — so an individual sat can be tracked, collected, and transferred.
Sats have **rarity** (common → uncommon → rare → epic → legendary → **mythic**, the genesis sat).
A sat can be **inscribed** with arbitrary content carried in a taproot witness, making an
immutable, on-chain Bitcoin-native artifact — no sidechain, no protocol change. **Runes** are
fungible tokens etched/minted with the same tooling. Reference: <https://docs.ordinals.com>.

## Custody: ordinal wallets are DIFFERENT from WaaS wallets

The browser-minted WaaS wallets are non-custodial — keys live in your browser, the node only
watches. **Ordinal wallets are descriptor wallets inside your own Bitcoin Core** — the node holds
those keys. Different custody model, deliberately: `ord` needs a Core wallet to build its special
transactions. Both stay on *your* machine; nothing changes hands.

## The guardrails (why BANKON gates every action)

Bitcoin Core is **not** ordinals-aware: a normal spend from a wallet holding inscriptions can send
the inscribed sat away as ordinary change and **destroy the artifact**. `bankon-ord` therefore
enforces, fail-closed:

- **Wallet isolation** — only wallets *named* as ordinal wallets (`ord`/`ordinal`/`inscription`/
  `rune`) may mutate; every other wallet is refused. Keep **ordinal** (inscription-bearing) and
  **cardinal** (plain-BTC) wallets strictly separate.
- **No material funds** — mutations are blocked above **0.1 ₿TC** wallet balance, and a wallet
  whose balance can't be read is refused outright.
- **Two-step, dry-run first** — every mutation (create · inscribe · etch · mint · send) first
  **dry-runs**, showing the gate verdict and the exact command/batchfile; only an explicit
  **⚠ ₿ROADCAST** plus confirmation runs it for real. The generic BANKON/`bankon-vault` signer is
  never pointed at ordinal UTXOs.
- **Loopback explorer** — `ord server` hosts untrusted HTML/JS; bind it to `127.0.0.1` only.

## Requirements & diagnostics

- `ord` binary ≥ 0.18 (source build recommended — the prebuilt needs glibc ≥ 2.38). Install:
  `bash bankon-ord/install.sh` (drops it in `~/.cargo/bin`).
- **Inscribing** needs Bitcoin Core ≥ 28 with **`txindex=1`**; reads work regardless.
- Wallet operations need a running **`ord server`** (loopback).
- The Qt 🜚 tab runs an automatic **preflight on first open** and keeps a 🩺 diagnostics strip
  (ord binary + version, key preflight facts, ord-server reachability); press **▶ preflight** any
  time for the full honest report. Start on **testnet**.

*Deeper reading in the module: `bankon-ord/README.md` (usage, CLI, library),
`bankon-ord/SCIENCE.md` (Ordinals: A Science · SciFi = Scientific Finance),
`bankon-ord/TOOLS.md` (instrument catalogue).*
