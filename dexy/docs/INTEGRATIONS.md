# DEXY integrations manifest — vendored tools & attach points

Everything from the original tool-drop is **included**: extracted under `vendor/<name>/`
(original zips preserved in `vendor/_archives/`). Phase 1 runs the core (projector,
accumulator, venues, ARRBY console). The rest are documented attach points — vendored,
inspectable, and wired in later phases without touching Phase-1 files.

| Piece | Vendored at | Attach | Status |
|-------|-------------|--------|--------|
| **BANKON toll** — golden-ratio tollkeeper (bridge/facilitation/mint) | `contracts/BankonToll.sol`, `BankonFacilitator.sol`, `BankonMinter.sol` | `forge test`; adopt via `is BankonToll` + `tolled`; off-chain quote `dexy/facilitator.mjs` + `/api/dexy/facilitator/quote` | **ACTIVE** — toll = gasFee × φ/10 (18 dp) → bankon.eth |
| **dexy accumulator/projector** (TS original) | `vendor/dexy-v1.0.0/` | ported to house `.mjs`: `dexy.mjs`, `aggregator.mjs`, `venues/*` | **ACTIVE (Phase 1)** — reference only |
| **ARRBY** — EVM flash-loan DEX→DEX arbitrage (Solidity + backend + console) | `vendor/arrby/` | console served at `/arrby`; backend (`:8787`, `cd vendor/arrby/arrby/backend && npm start`) proxied at `/api/dexy/arrby/*`; contracts deploy via Foundry per `vendor/arrby/arrby/README` | **ACTIVE (console/proxy)** — backend manual-start; execution only in the user's browser wallet |
| **x402 payment rails** (PYTHAI v2.1.0, Algorand/EVM HTTP-402) | `vendor/pythai-x402-payment-rails/` | Phase 2: port `settlement.ts`/`receipt.ts` from `vendor/dexy-v1.0.0/dexy/src/` and gate settlement on a confirmed x402 receipt via bankon-algo (:4444) | attach point |
| **Chainflip channel opening** (execution leg) | `vendor/dexy-v1.0.0/dexy/src/venues/chainflip.ts` (`openChainflipChannel`) | Phase 2: deposit-channel open at execution time, destAddress = user's BTC address, fill-or-kill refund | attach point |
| **BANKON vault multichain** — Tomb quorum key custody | `vendor/bankon-vault-multichain/` | Phase 2: treasury-side gated ops via `../bankon-vault/clients/vault-client.mjs` (PolicyEngine spend-limits/quorum) | attach point |
| **BANKON PYTHAI v2** — LayerZero OFT + X402AlgorandGateway | `vendor/bankon-pythai-v2/` | Phase 2/3: token-side EVM↔Algorand leg of the x402 rail | attach point |
| **Drawbridge** — PAI stablecoin / CDP / LayerZero gateway | `vendor/drawbridge/` | Phase 3: PAI as an EVM source asset for accumulation legs | attach point |
| **Logo registry + SpintradeDesk** — auto-listing desk | `vendor/pythai-logo-registry/` | Phase 3: `SpintradeDesk.sol` auto-listing tickets when a chain onboards | attach point |
| **fulmen** — Litecoin Lightning module report | `docs/fulmen-litecoin-lightning.md` | Phase 3: L402 rail parallel to x402 | design doc |
| **sBTC clean-room bridge** roadmap | `docs/stacks.md` + `docs/clarinet-*` | Phase 3: **bridge-OUT only** — sBTC→native-BTC exit lane; wrapped BTC is never a DEXY custody end state | design doc |
| **DeFiLlama API guide** | `docs/defillama.md` | in use by `dexy.mjs` (free tier, proof-of-reserve + protocol TVL); Pro endpoints documented for scale-up | **ACTIVE (Phase 1)** |
| **Immutable logo registry blueprint** | `docs/IMMUTABLE-LOGO-REGISTRY-BLUEPRINT.md` | provenance primitive for listed assets | design doc |

**Law that binds every future attach:** the destination of value is the user's own keys
(WaaS BTC Standard) — no wrapped-BTC end state, no server-side private material
(`rejectPrivate` stands), external data stays under `/api/dexy/*` with `source:` labels,
and `/api/pairs` stays chain-native.
