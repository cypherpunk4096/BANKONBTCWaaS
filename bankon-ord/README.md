# bankon-ord (alpha)

An **optional, isolated** module that brings Bitcoin **ordinals / inscriptions / runes** to BANKON by
wrapping the official [`ordinals/ord`](https://github.com/ordinals) CLI — with the safety guardrails
ordinals demand. Install it only if you want ordinals; nothing in BANKON or `bankon-vault` depends on
it. Supports **mainnet and testnet** (also signet + regtest).

> Not affiliated with ordinals/ord. This wraps its CLI (CC0). Docs: <https://docs.ordinals.com>.

## Ordinal theory in one paragraph
Every satoshi is numbered by the order it was mined (**ordinals**), and sats move through transactions
**first-in-first-out**, so an individual sat can be tracked and transferred. Sats have **rarity**
(common → uncommon = first sat of a block → rare = first per difficulty adjustment → epic = first per
halving → legendary → **mythic** = the genesis sat). A sat can be **inscribed** with arbitrary content
in the transaction witness, making an **immutable, on-chain** Bitcoin-native artifact — no sidechain,
no token, no protocol change. (Summary of <https://docs.ordinals.com/overview.html>.)

## Why the guardrails matter (read this)
Bitcoin Core is **not** ordinals-aware. A normal spend from a wallet that holds inscriptions can send
the inscribed sat away as ordinary change and **destroy the inscription**. So this module enforces:
- **Wallet isolation** — a wallet must *declare* itself an **ordinal** wallet by name
  (`ord`/`ordinal`/`inscription`/`rune`); mutating actions on any other wallet are refused. Keep
  **ordinal** (inscription-bearing) and **cardinal** (plain-BTC) wallets strictly separate.
- **No material funds** — the ord docs warn against ordinals tooling on wallets with material funds;
  actions are blocked above 0.1 BTC. Move plain BTC to a cardinal wallet first.
- **Fail-closed approval** — inscribe/send require an explicit approval callback (shown the action)
  and are **dry-run by default** (`--yes` to broadcast). The generic BANKON/`bankon-vault` BTC signer
  is **never** pointed at ordinal UTXOs.
- **Loopback explorer** — the `ord server` hosts untrusted HTML/JS; bind it to `127.0.0.1` only.

## Requirements
- The `ord` binary (installer below). Runes + modern wallet need ord ≥ 0.18; **inscribing needs
  Bitcoin Core ≥ 28** and **`txindex=1`**. Reads work without inscribing capability.

## Install
```bash
bash bankon-ord/install.sh            # auto: official prebuilt → your fork (bankonvault/ord) → crate
ORD_SOURCE=fork bash bankon-ord/install.sh   # force-build github.com/bankonvault/ord
```
The installer runs the test suite and a mainnet+testnet preflight, then drops a `bankon-ord` launcher.
(Your own fork [`bankonvault/ord`](https://github.com/bankonvault/ord) — *"rare and exotic sats"* — is
preferred when building from source; related: [`Professor-Codephreak/wallet-utils`](https://github.com/Professor-Codephreak/wallet-utils).)

## Usage
```bash
bankon-ord preflight       --net testnet     # honest readiness report (ord? core? txindex? capable?)
bankon-ord create-wallet   --net testnet --wallet ord-test     # a dedicated ORDINAL wallet
bankon-ord receive         --net testnet --wallet ord-test
bankon-ord wallet-balance  --net testnet --wallet ord-test
bankon-ord inscriptions    --net testnet --wallet ord-test
bankon-ord inscribe        --net testnet --wallet ord-test --file art.png --fee-rate 5   # dry-run; --yes to send
bankon-ord send            --net testnet --wallet ord-test --to <addr> --outgoing <inscription_id> --fee-rate 5
```
Same commands with `--net mainnet` when you're ready (start on **testnet**).

Library:
```python
from bankon_ord import OrdCli
ord = OrdCli("testnet")
print(ord.preflight())                          # never mutates
ord.inscribe_gated("ord-test", "art.png", 5, approve=confirm, dry_run=True)
```

## Status
**Alpha (Step 2).** Read ops + preflight + wallet isolation + gated (dry-run) inscribe/send across
mainnet/testnet/signet/regtest; 10 passing tests (no `ord` needed to test). Next: live inscribe/send
flow verified on testnet, rune etch/mint, and an optional Qt panel.
