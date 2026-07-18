# ⟲ DEXY — sovereign-custody ₿TC liquidity mover

A BANKON module (`:8091`, loopback) that moves Bitcoin liquidity **out of CEX custody and
between DEX venues into self-custody**: native L1 BTC landing at addresses whose keys YOU
hold (WaaS BTC Standard) — never wrapped BTC, never a server-side key. DEXY plans and
quotes; **you execute from your own wallet**.

```bash
~/bankon-tools/bankon dexy        # → http://127.0.0.1:8091   (stop: bankon dexy stop)
```

## What it does

- **CEX ₿TC custody vs DEX depth** — DeFiLlama proof-of-reserve per exchange vs live
  native-BTC depth on THORChain/Chainflip. The gap is the problem DEXY schedules around.
- **CEX→DEX projection** — `GET /api/dexy/project?moveUsd=…` → a tranche/day withdrawal
  schedule (CEX → your address) bounded by a daily absorption limit.
- **Native-₿TC accumulation** — `GET /api/dexy/quote` / `POST /api/dexy/plan` → parallel
  venue quotes (THORChain dual-node inbound cross-check, Maya, Chainflip; MetaMask as a
  quote-only oracle), greedy split under `maxSlipBps`. Venues pay **your address directly**.
- **DEX→DEX (EVM)** — vendored ARRBY flash-loan arbitrage console at `/arrby`
  (backend: `cd vendor/arrby/arrby/backend && npm start`, proxied at `/api/dexy/arrby/*`).
- **SPINTRADE-compatible** — `GET /api/pairs` is the chain-native pairs engine imported
  from bankon-waas unmodified (no external feed, no fiat there — external data lives under
  `/api/dexy/*` with `source:` labels).
- **Trustless BTC leg** — `/api/swap/htlc/*` (bankon-waas swap.mjs, unmodified).
- **BANKON toll** — the golden-ratio tollkeeper for bridge/facilitation/mint: every facilitated
  transfer takes `toll = gasFee × φ/10` (golden ratio, 18 dp) on top of gas, held in
  bankon.eth. Contracts in `contracts/` (`forge test`), quoted at
  `/api/dexy/facilitator/quote`. The `BankonFacilitator` escrow holds the client's asset
  through the transfer; `BankonMinter`/`BankonMinterFactory` show the `tolled` adoption pattern.

## Sovereign custody

Every quote/plan **requires** `btcAddress` and verifies it against your node
(`/api/dexy/custody/verify` proves it belongs to a registered WaaS watch-only wallet).
Default: warn on unverified. `DEXY_STRICT_CUSTODY=1` refuses anything that isn't provably
yours. The `rejectPrivate` guard 400s any request carrying key material.

## Tests (no mainnet, no real funds)

```bash
node test/test-dexy-offline.mjs     # projector math + planner on fixtures — zero network
./test/test-dexy-regtest.sh         # e2e on an isolated regtest node
```

Config: `BANKON_DEXY_PORT` (8091) · `DEXY_ABSORPTION_PCT` · `DEXY_MAX_SLIP_BPS` ·
`DEXY_STRICT_CUSTODY` · `DEXY_FIXTURES` · `ARRBY_URL` — see `bankon.env.example`.
Vendored ecosystem + Phase-2/3 attach points: `docs/INTEGRATIONS.md`.

© 2026 ₿ANKON — all rights preserved.
