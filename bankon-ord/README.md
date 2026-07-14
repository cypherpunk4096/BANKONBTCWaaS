# bankon-ord (0.3.0-alpha)

An **optional, isolated** module that brings Bitcoin **ordinals / inscriptions / runes** to BANKON by
wrapping the official [`ordinals/ord`](https://github.com/ordinals/ord) CLI — with the safety
guardrails ordinals demand. Install it only if you want ordinals; nothing in BANKON or `bankon-vault`
depends on it. Supports **mainnet and testnet** (also signet + regtest).

> Not affiliated with ordinals/ord. This wraps its CLI (CC0). Docs: <https://docs.ordinals.com>.
>
> **Read alongside:** [SCIENCE.md](SCIENCE.md) — *Ordinals: A Science* + **SciFi = Scientific Finance**
> (accuracy-first oracles / NFTs / ordinals / bridges) · [TOOLS.md](TOOLS.md) — the instrument
> catalogue, incl. our ordinals org [satoshigen](https://github.com/satoshigen).

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

## Install — deploy from source and/or binary, your choice
```bash
bash bankon-ord/install.sh                     # auto: SOURCE build if cargo exists, else prebuilt
ORD_SOURCE=source bash bankon-ord/install.sh   # cargo build (recommended — always matches your glibc)
ORD_SOURCE=binary bash bankon-ord/install.sh   # official prebuilt (needs glibc >= 2.38 — checked)
ORD_SOURCE=fork   bash bankon-ord/install.sh   # cargo build of github.com/bankonvault/ord
```
The installer runs the test suite and a mainnet+testnet preflight, then drops a `bankon-ord` launcher.
(Your own fork [`bankonvault/ord`](https://github.com/bankonvault/ord) — *"rare and exotic sats"* — is
preferred when building from source; related: [`Professor-Codephreak/wallet-utils`](https://github.com/Professor-Codephreak/wallet-utils).)

## Usage reference

**CLI** (`bankon-ord` launcher; every mutating command is dry-run by default — add `--yes` to
broadcast; modern ord wallet commands need a running `ord server`, point at it with
`--server-url http://127.0.0.1:80`):
```bash
bankon-ord preflight       --net testnet     # honest readiness report (ord? core? txindex? capable?)
bankon-ord create-wallet   --net testnet --wallet ord-test     # a dedicated ORDINAL wallet
bankon-ord receive         --net testnet --wallet ord-test
bankon-ord wallet-balance  --net testnet --wallet ord-test
bankon-ord inscriptions    --net testnet --wallet ord-test
bankon-ord outputs         --net testnet --wallet ord-test
bankon-ord inscribe        --net testnet --wallet ord-test --file art.png --fee-rate 5   # dry-run; --yes to send
bankon-ord send            --net testnet --wallet ord-test --to <addr> --outgoing <inscription_id> --fee-rate 5
bankon-ord mint            --net testnet --wallet ord-runes --rune UNCOMMON.GOODS --fee-rate 2
bankon-ord etch            --net testnet --wallet ord-runes --rune UNCOMMON.GOODS --fee-rate 2 \
                           --divisibility 2 --supply 1000 --symbol ¢ --premine 100   # dry-run shows the batchfile
```
Same commands with `--net mainnet` when you're ready (start on **testnet**).

**Library** (`bankon_ord` package — see the source map below):
```python
from bankon_ord import OrdCli, is_ordinal_wallet, validate_rune_name

o = OrdCli("testnet", server_url="http://127.0.0.1:8080")   # server_url for wallet mutations
print(o.preflight())                                        # never mutates
o.wallet_balance("ord-test"); o.wallet_inscriptions("ord-test"); o.output("txid:0")

# every mutation runs the same fail-closed gate: ordinal wallet · no material funds ·
# KNOWN balance · human approval — and is dry-run unless you say otherwise
o.inscribe_gated("ord-test", "art.png", 5, approve=confirm, balance_sats=bal, dry_run=True)
o.send_gated("ord-test", addr, inscription_id, 5, approve=confirm, balance_sats=bal)
o.mint_gated("ord-runes", "UNCOMMON.GOODS", 2, approve=confirm, balance_sats=bal)
o.etch_gated("ord-runes", "UNCOMMON.GOODS", 2, approve=confirm, divisibility=2,
             supply="1000", symbol="¢", premine="100", balance_sats=bal)  # dry-run → batchfile text
```

**Qt panel** — `~/bankon-tools/bankon-qt.sh`, toolbar → **🜚 Ordinals** (read-only: preflight +
wallet balance/inscriptions/outputs with a live ordinal/cardinal isolation badge; mutations stay
in this gated CLI by design).

**Live end-to-end proof** — `python3 bankon-ord/tests/test_live_regtest.py` (needs `ord` +
`bitcoind`; self-skips otherwise): throwaway regtest node → create → fund → inscribe LIVE →
send LIVE → gates verified on the live path. Isolated datadir + regtest ports — safe to run
beside a live mainnet node.

## Source code & references
| What | Where |
|---|---|
| **This module's source** | [`bankon-ord/bankon_ord/`](https://github.com/Professor-Codephreak/bankon-tools/tree/main/bankon-ord/bankon_ord) — [`ord_cli.py`](https://github.com/Professor-Codephreak/bankon-tools/blob/main/bankon-ord/bankon_ord/ord_cli.py) (wrapper + gated mutations) · [`isolation.py`](https://github.com/Professor-Codephreak/bankon-tools/blob/main/bankon-ord/bankon_ord/isolation.py) (wallet isolation, rune-name validation, guards) · [`cli.py`](https://github.com/Professor-Codephreak/bankon-tools/blob/main/bankon-ord/bankon_ord/cli.py) (the `bankon-ord` command) |
| **Tests** | [`tests/test_ord.py`](https://github.com/Professor-Codephreak/bankon-tools/blob/main/bankon-ord/tests/test_ord.py) (14 unit, no binary needed) · [`tests/test_live_regtest.py`](https://github.com/Professor-Codephreak/bankon-tools/blob/main/bankon-ord/tests/test_live_regtest.py) (live flow) |
| **Upstream `ord` source** | <https://github.com/ordinals/ord> (Rust, CC0) · crate: <https://crates.io/crates/ord> |
| **Ordinals handbook** | <https://docs.ordinals.com> — [overview](https://docs.ordinals.com/overview.html) · [inscriptions](https://docs.ordinals.com/inscriptions.html) · [runes](https://docs.ordinals.com/runes.html) · [wallet guide](https://docs.ordinals.com/guides/wallet.html) |
| **Ordinal theory spec** | [ord BIP draft](https://github.com/ordinals/ord/blob/master/bip.mediawiki) |
| **Our fork / org** | [`bankonvault/ord`](https://github.com/bankonvault/ord) ("rare and exotic sats") · [satoshigen](https://github.com/satoshigen) · [wallet-utils](https://github.com/Professor-Codephreak/wallet-utils) |

## Status
**0.3.0-alpha — feature-complete for the alpha scope.** Read ops + preflight + wallet isolation + gated (dry-run) inscribe/send across
mainnet/testnet/signet/regtest, **gated rune etch/mint** (validated names, reviewable batchfile);
14 unit tests (no `ord` needed), an **optional read-only Qt panel** (bankon-qt toolbar →
🜚 Ordinals), and a **LIVE regtest integration test** (`tests/test_live_regtest.py`,
self-skipping): real `ord` + throwaway regtest node → create ordinal wallet → fund →
**inscribe_gated live** → list → **send_gated live**, with the cardinal-wallet refusal
verified on the live path too. Installer offers a **deploy choice**: `ORD_SOURCE=source`
(cargo, recommended — prebuilt needs glibc ≥ 2.38) | `binary` | `fork` | `auto`.
