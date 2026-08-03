# dexy v1.0.0

Sources native (L1) Bitcoin across THORChain, Maya, and Chainflip — with
MetaMask's swap aggregation as a meta-quote oracle and liquidity map — and
delivers it directly to a buyer once the sale payment is **actually received**
on Algorand (x402). Includes the CEX→DEX transfer projector built on
DeFiLlama proof-of-reserve data.

---

## 1. Technical explanation

### Settlement pipeline

```
BANKON / agenticplace ── POST /webhooks/sale-confirmed ──▶
awaitFundsReceipt (src/receipt.ts)
  polls Algorand indexer until payment txn to treasury is confirmed:
  receiver == TREASURY_ALGO_ADDRESS, asset & amount match, orderId in note.
  ── CONFIRMED SALE = RECEIPT OF FUNDS. On-chain state is the authority;
     the x402 receipt object is only a hint (optional txId). ──
        │
        ▼
planAccumulation (src/aggregator.ts)
  quotes THORChain, Maya, Chainflip in parallel (Promise.allSettled),
  filters legs over maxSlipBps, greedy-splits tranches to reach targetBtcSats,
  spreads consecutive tranches across venues to respect per-venue depth,
  trims the last leg to avoid >1% overshoot.
        │
        ▼
settleConfirmedSale (src/settlement.ts)
  per leg: destination = BUYER's BTC address.
  - THORChain/Maya: send source asset to a freshly verified inbound vault
    address with the quote memo ("=:BTC.BTC:<buyer>:<limit>").
  - Chainflip: open deposit channel at execution time
    (requestDepositAddressV2, destAddress = buyer, fill-or-kill refund).
  The service NEVER custodies BTC — venues pay the buyer directly.
        │
        ▼
monitorLegs — polls until confirmed / refunded; refunds are flagged for re-run.
```

### Venue adapters
- `venues/thorchain.ts` — THORNode quote + inbound-address cross-check across
  two independent nodes (hard abort on mismatch: defense against the May 2026
  $10M vault-churn address-poisoning exploit). 1e8 fixed-point units.
- `venues/maya.ts` — identical mechanics (friendly fork), MAYANode endpoints.
- `venues/chainflip.ts` — official @chainflip/sdk; egressAmount in sats,
  lowLiquidityWarning disqualifies the venue for that tranche.

### dexy (src/dexy.ts) — CEX → DEX transfer projection
- `fetchCexBtcHoldings()` — per-CEX assets held **on the Bitcoin chain** from
  DeFiLlama proof-of-reserve wallets (api.llama.fi/protocol/<slug>,
  chainTvls.Bitcoin). Snapshot at build time: Binance ≈ $38.7B on BTC chain.
- `fetchDexBtcDepth()` — live BTC-side depth: THORNode /thorchain/pools
  (BTC.BTC balance_asset × asset_tor_price) + Chainflip.
- `projectTransfer(moveUsd, depths)` — tranche schedule bounded by a daily
  absorption limit (default 10% of DEX BTC depth/day, 4 tranches/day),
  estimated slip cost, per-venue split proportional to depth.
- `GET /dexy?moveUsd=N` — full report as JSON.

The core asymmetry dexy quantifies: CEXs hold tens of billions of actual BTC;
the entire native-BTC DEX side is ~$25M deep. Any meaningful CEX→DEX transfer
must be scheduled over days, not sent in one shot.


### MetaMask integration (src/venues/metamask.ts)

MetaMask shipped native Bitcoin support on 2025-12-15: L1 BTC (Native SegWit
only, Taproot pending) with buy/send/receive/swap in the consumer wallet, one
BTC address auto-derived per multichain account, SRP import only (no BTC
private-key import). Source code:
- Wallet: github.com/MetaMask/metamask-extension, github.com/MetaMask/metamask-mobile
- BTC implementation: github.com/MetaMask/snap-bitcoin, github.com/MetaMask/accounts
- Swap/bridge routing: github.com/MetaMask/core (packages/bridge-controller)

MetaMask is an aggregator, not a liquidity source — BTC routes are sourced
through Li.Fi/Socket and ~18 providers (Relay, Mayan, Squid/Axelar, Across,
...), which settle native BTC on the same venues this project integrates
directly. The adapter therefore plays two roles and is **quote/map-only**:

1. `quoteMetamask()` — meta-quote in the aggregator. Never executable
   (deposit=null); if MetaMask's aggregation beats our best direct venue by
   >2%, we log a routing-table warning (its raw payload names the winning
   underlying provider so you can add that venue).
2. `mapMetamaskLiquidity()` — probes BTC source pairs (ETH, ETH.USDC, SOL,
   BASE.USDC → BTC.BTC) and records the winning provider + output per pair.
   Included in the /dexy report as `metamaskPairs`: a live map of who really
   holds the Bitcoin liquidity behind MetaMask Swaps.

The bridge API endpoint (bridge.api.cx.metamask.io, the same one MetaMask's
own bridge-controller calls) is undocumented and unversioned — the adapter is
fully defensive and degrades to an empty map if shapes change.

---

## 2. Usage summary

```bash
npm install
npm run build

TREASURY_ALGO_ADDRESS=<algorand treasury addr> \
TREASURY_REFUND_ADDRESS=<chainflip refund addr> \
MAX_SPEND_NATIVE=5000000000 \
node dist/server.js
```

Trigger a settlement (payment must already be en route on Algorand):
```bash
curl -X POST localhost:8080/webhooks/sale-confirmed -d '{
  "orderId": "ORD-1042",
  "buyerBtcAddress": "bc1q...",
  "targetBtcSats": "2500000",
  "sourceAsset": {"chain":"ETH","symbol":"USDC","contract":"0xA0b8...","decimals":6},
  "maxSlipBps": 100,
  "payAssetId": 31566704,
  "payMinAmount": "1500000000",
  "algoTxId": "OPTIONAL-X402-SETTLEMENT-TX"
}'
```
Response 202 with the planned legs once funds are confirmed received; 422 if
the payment never lands, venues can't cover the target, or a safety check trips.

CEX→DEX projection:
```bash
curl "localhost:8080/dexy?moveUsd=5000000"
```

Wire-up required before mainnet:
- `signer` in `src/server.ts` — treasury wallet (viem/ethers for EVM legs,
  PSBT signer if the source asset is BTC itself).
- Env: `TREASURY_ALGO_ADDRESS`, `TREASURY_REFUND_ADDRESS`, `MAX_SPEND_NATIVE`,
  optionally `ALGO_INDEXER_URL`, `ALGO_MIN_CONFIRMATIONS`.

Test path: THORChain **stagenet** + Chainflip **perseverance** testnet +
Algorand **testnet** indexer first, then dust-sized live swaps. Foundry covers
only your EVM treasury/x402 contracts — BTC legs cannot be simulated in an
EVM harness.

---

## 3. Limitations

1. **Depth ceiling.** Native-BTC DEX liquidity is tiny (~$25M BTC-side total).
   Orders beyond a few hundred k USD per leg will hit slippage caps and refund.
   dexy's projection is the honest answer for anything larger.
2. **THORChain incident risk.** After the May 2026 vault-churn exploit,
   THORChain 7d volume read zero on DeFiLlama at build time. The adapter
   cross-checks inbound addresses, but you must confirm trading has resumed
   and consider keeping the venue disabled until independently verified.
3. **Quotes are not fills.** Inbound addresses rotate on vault churn; quotes
   expire in ~60s. Instructions carry `expiresAt` and stale ones are refused,
   but a leg can still refund if price moves past the limit — the buyer is
   then short until the leg is re-run (monitorLegs flags this; re-execution
   is manual by design).
4. **No partial-fill netting.** Legs execute sequentially; if leg 2 refunds
   after leg 1 confirmed, the buyer received partial BTC. Reconciliation
   logic (top-up leg from re-quote) is left to the operator.
5. **dexy estimates, doesn't execute.** CEX withdrawal automation (exchange
   API keys, whitelisted addresses, withdrawal limits/KYC holds) is outside
   scope. Chainflip BTC-share of TVL is approximated (~40%); refine via the
   LP API for production sizing.
6. **DeFiLlama dependency.** CEX holdings come from published PoR wallets —
   exchanges without wallet lists (e.g. Coinbase, Kraken) are invisible, and
   figures are USD-denominated (BTC-price sensitive).
7. **Algorand note matching.** Funds receipt requires the orderId in the txn
   note (or an explicit txId). Payments without the note won't auto-match.
8. **MetaMask API is unofficial.** bridge.api.cx.metamask.io is MetaMask's
   internal aggregation endpoint, not a supported public API — response
   shapes, rate limits, and chain identifiers (CAIP for BTC) may change
   silently. The adapter is quote/map-only and fails soft, but do not build
   execution paths on it; execute on the winning underlying venue instead.
9. **No persistence.** In-memory only; add a store + idempotency keys before
   production (replayed webhooks would re-plan, though the funds-receipt gate
   prevents double-spend of the same payment only if you record consumed txIds).
