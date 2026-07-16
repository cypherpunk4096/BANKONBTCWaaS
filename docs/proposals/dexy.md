# ⟲ DEXY — sovereign-custody BTC liquidity mover (modular inclusion)

The liquidity problem in one sentence: CEXs custody tens of billions of dollars of actual
Bitcoin (proof-of-reserve wallets), while the entire **native-BTC** DEX side (THORChain,
Maya, Chainflip) is only tens of millions deep — so moving Bitcoin out of exchange custody
into self-custody-reachable venues is a **scheduling problem**, not a button. DEXY is that
scheduler, as a BANKON module on **:8091**.

## The sovereign-custody law

Every DEXY quote and plan requires a destination BTC address, and that destination is meant
to be **the user's own keys** (WaaS BTC Standard: keys minted client-side, server watch-only):

- Venues pay the user's address **directly** (THORChain/Maya memo, Chainflip channel) — DEXY
  never custodies, never signs, and holds no keys. The `rejectPrivate` guard 400s any request
  carrying private material, same as WaaS.
- The target asset is hardcoded native **`BTC.BTC`** — no wrapped-BTC (wBTC/sBTC) output path
  exists in the code. Wrapped forms are IOUs on another chain; DEXY exists to produce the
  opposite: **sovereign custodial** Bitcoin clients.
- `/api/dexy/custody/verify` proves a destination belongs to a registered WaaS watch-only
  wallet. Default: warn on unverified. `DEXY_STRICT_CUSTODY=1` refuses them outright.

## Pairs-policy resolution (external data vs chain-native)

`pairs.mjs` law stands: **no external feed, no fiat** on `/api/pairs`. DEXY serves `/api/pairs`
by importing `pairsRouter()` from bankon-waas **unmodified** (SPINTRADE drop-in). Everything
external — DeFiLlama proof-of-reserve, THORNode/Chainflip depth, MetaMask meta-quotes — lives
under `/api/dexy/*` and every response is labeled with its `source:`. The two worlds share a
port but never a pair.

## Modular inclusion — what was ADDED (no existing file overwritten)

| Piece | File | Attach | Detach |
|-------|------|--------|--------|
| DEXY service | `dexy/server.mjs` (+ `lib` `fetch` `custody` `dexy` `aggregator` `venues/*`) | `bankon dexy` (opt-in, like algo/eth) | `bankon dexy stop`; delete `dexy/` |
| Chain-native pairs passthrough | `bankon-waas/pairs.mjs` (imported, unmodified) | `app.use(pairsRouter())` — one line | remove the mount line |
| Trustless BTC HTLC leg | `bankon-waas/swap.mjs` (imported, unmodified) | `app.use(swapRouter())` — one line | remove the mount line |
| DEXY web UI | `dexy/public/index.html` | served statically at :8091 | delete the file |
| ARRBY console (EVM DEX→DEX) | `dexy/public/arrby.html` + `dexy/vendor/arrby/` | `/arrby` page + `/api/dexy/arrby/*` proxy (backend manual-start, quote-only) | delete `vendor/arrby/` |
| Vendored ecosystem (drawbridge, pythai-x402, vault-multichain, logo-registry, …) | `dexy/vendor/<name>/` + `dexy/vendor/_archives/*.zip` | Phase-2+ attach points, documented in `dexy/docs/INTEGRATIONS.md` | delete the vendor dir |
| Launcher hook | `bankon` (`dexy)` case + stop/status lines) | already additive | remove the case |
| Installer hook | `bankon.sh` (`dexy` in COMPONENTS + npm loop) | `./bankon.sh --only dexy` | remove from the loop |
| systemd unit | `systemd/bankon-dexy.service` | `systemctl --user enable --now bankon-dexy` | disable the unit |
| Env block | `bankon.env.example` (`BANKON_DEXY_PORT` etc.) | copy into `bankon.env` | delete the block |
| Tests | `dexy/test/test-dexy-offline.mjs`, `dexy/test/test-dexy-regtest.sh` | run directly | delete |

## What DEXY does (Phase 1)

- **CEX→DEX projection** (`/api/dexy/project`): DeFiLlama proof-of-reserve per CEX vs live
  native-BTC DEX depth → a tranche/day withdrawal schedule bounded by a daily absorption
  limit (default 10%/day of depth), with slip-cost estimate and fresh-UTXO/dust notes.
- **Native-BTC accumulation** (`/api/dexy/quote`, `/api/dexy/plan`): parallel quotes across
  THORChain (dual-node inbound cross-check — vault-poisoning defense), Maya, Chainflip
  (plain HTTP, no SDK) + MetaMask as a quote-only oracle; greedy tranche split under
  `maxSlipBps`. Output: deposit instructions the user executes **from their own wallet**.
- **DEX→DEX (EVM)**: vendored ARRBY flash-loan arbitrage kit — console at `/arrby`,
  backend proxied at `/api/dexy/arrby/*` (503 + start hint when down). Execution happens in
  the user's browser wallet inside the ARRBY UI; DEXY stays quote-only server-side.
- **Offline degradation**: 8s fetch timeout, last-good cache with `stale:` markers, and
  `DEXY_FIXTURES=1` for fully offline operation/tests on constrained boxes.

Phase 2+ attach points (x402 settlement, Chainflip channel opening, bankon-vault gated
custody, drawbridge, SpintradeDesk auto-listing, fulmen Lightning, sBTC clean-room —
bridge-out only, never a custody end state) are catalogued in `dexy/docs/INTEGRATIONS.md`.
