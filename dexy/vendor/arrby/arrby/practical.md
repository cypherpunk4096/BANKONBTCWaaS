# ARRBY — practical.md

## A worked example, with real numbers

Say you've spotted USDC/WETH trading at slightly different prices on two
routers on Arbitrum:

- Router A (Sushiswap): 3,000 USDC → 1.000 WETH
- Router B (Camelot): 1.000 WETH → 3,012 USDC

You want to borrow 3,000 USDC, buy WETH on Sushiswap, sell it back to USDC
on Camelot, and keep the spread.

**Step 1 — quote it first, for free.**
In the console: asset = USDC, amount = 3000, routerA = Sushiswap, pathA =
[USDC, WETH], routerB = Camelot, pathB = [WETH, USDC]. Click **Quote**.

The contract's `quoteArbitrage` runs the exact math it would run for real:
- `grossReturned` ≈ 3,012 USDC
- Aave V3's flash loan fee is typically 0.05% (5 bps): premium = 3,000 × 0.0005 = 1.5 USDC
- `amountOwed` = 3,000 + 1.5 = 3,001.5 USDC
- `expectedProfit` = 3,012 − 3,001.5 = **10.5 USDC**

That's before gas. On Arbitrum, gas for a run like this is typically a few
tenths of a dollar, so this trade is worth executing. On Ethereum mainnet,
gas alone could be $10–$50+ depending on congestion — the same 10.5 USDC
spread might not clear gas costs there. **Always check gas against expected
profit for the specific chain you're on; the contract only guarantees the
*trade* is profitable, not that the trade is profitable net of gas.**

**Step 2 — set a minimum profit with a margin.**
Prices move between when you quote and when your transaction actually
mines. Don't set `minProfit` to exactly your quoted profit — set it lower
than the quote but above your real breakeven (quoted profit minus expected
gas), so minor price movement doesn't cause an unnecessary revert, but a
real deterioration in the opportunity still protects you. In this example,
something like `minProfit = 3` (USDC) gives room for the spread to narrow a
little while still guaranteeing you never lose money on the trade itself.

**Step 3 — execute and watch the ledger.**
The six-step ledger in the UI will show the loan requested, both legs
settling, repayment, and confirmation — typically all within one block. The
profit card will read the *actual* realized profit from the
`ArbitrageExecuted` event, which can differ slightly from the quote if the
pool moved between quote and execution (still guaranteed ≥ your minProfit,
or the whole thing reverted).

## Deployment checklist (mainnet — there's no testnet mode)

This kit deploys directly to whatever `--rpc-url` points at. There's no
staging flag. Before you run `forge script ... --broadcast`:

- [ ] `AAVE_POOL` is the correct address for the chain you're deploying to
      (cross-check against `config/chains.json` and, ideally, Aave's own
      address book directly — addresses do occasionally change on newer
      chains as Aave governance updates deployments).
- [ ] `OWNER` is an address you actually control and can sign from — this
      is the only key that can call `initiateArbitrage` and `withdraw`.
- [ ] `DEPLOYER_KEY` is funded with enough native gas token to deploy.
- [ ] You've run `forge test -vv` locally and everything passes.
- [ ] You understand that until you call `renounceOwnership()`, the deployer
      key is a standing dependency — treat it like any other hot-ish
      operational key (the kind you'd use to run a bot), not like a
      long-term custody key.

## Common gotchas

- **Decimals.** USDC is 6 decimals on every chain that's deployed it
  natively; WETH and most other tokens are 18. Getting this wrong in the UI
  means the amount you think you're borrowing is off by a factor of
  10^12 — the console's "Asset decimals" field exists specifically so this
  doesn't silently corrupt your inputs.
- **Path direction.** `pathAtoB` must start at the asset you're borrowing;
  `pathBtoA` must end at it, and the two paths must meet at the same
  intermediate token. The v1.1 audit pass added an on-chain check for this
  (`BadPath`), so a mistake here now reverts cleanly instead of stranding
  funds mid-trade — but it's still worth getting right in the UI to avoid a
  wasted transaction.
- **Deadlines.** If your deadline is too tight and network congestion delays
  your transaction past it, the router calls themselves will revert (most
  V2 routers check `block.timestamp <= deadline`). Five minutes is a
  reasonable default; shrink it only if you have a specific reason to.
- **Router liquidity.** `getAmountsOut` (used by `quoteArbitrage`) assumes
  the pool's current reserves don't change between your quote and your
  transaction. On thinly-traded pairs, someone else's transaction landing
  first can move the price enough that your `minProfit` check fails and you
  simply revert — annoying, but exactly the protection working as intended.
- **This is not a MEV-protected transaction by default.** A profitable
  arbitrage transaction is also visible to searchers/MEV bots once it hits
  the public mempool. If a bot can see your exact routers and path in
  advance and beat you to it, your transaction reverts (safely) rather than
  losing funds. If you're operating this at scale and it matters, route
  through a private mempool / builder relationship for the chain you're on
  — that's outside this kit's scope but worth knowing before you assume
  every trade you can see is one you'll actually capture.

## Using the opportunity finder well

- **Treat DefiLlama as the sanity line, routers as the truth.** DefiLlama's
  price is an aggregate; the router quote is what you'd actually get at
  your size. A huge spread where one router disagrees wildly with the
  DefiLlama reference usually means a thin/stale pool, not free money —
  those quotes tend to evaporate (or worse, the pool can't absorb your
  notional even though `getAmountsOut` says it can... it can't lie about
  reserves, but the price impact at 10x your scan notional is a different
  trade entirely).
- **Scan at the notional you'd actually trade.** `getAmountsOut` includes
  price impact at the amount you pass. A spread that exists at 100 USDC of
  notional often vanishes at 10,000 because the second trade eats the pool.
- **The bps chart hovering just above zero is normal.** Persistent 1–5 bps
  "spreads" between V2 forks are usually inside the two pools' combined
  0.3%+0.3% swap fees plus gas — the finder nets out Aave's premium but the
  router quotes already include swap fees, so anything green on the bar
  chart is genuinely net of everything except gas. Compare against gas
  before acting.
- **USDT now works** (v1.2): the contract's token calls tolerate
  non-standard ERC-20s that return no value from approve/transfer, so
  USDT-legged routes on Ethereum mainnet are usable. Earlier versions would
  have reverted on the very first approve.

## Autoearn, soberly

Autoearn is automation, not alchemy. It runs the same detect→quote→execute
loop you'd run by hand, faster and unattended — but it earns exactly what
the market offers and nothing more. Every execution passes the contract's
`minProfit` gate, so a dry spell produces reverts (gas), not losses of
principal. Set expectations accordingly: on quiet pairs it will mostly sit
in "watching," and that's correct behavior, not a malfunction.

Before you arm it:

- **Set `setMaxNotional` on-chain first.** This is the one guardrail that
  holds even if your loop config is wrong or your executor key is
  compromised — the contract itself refuses to over-borrow. The off-chain
  loop's own notional cap is convenience; the on-chain one is protection.
- **Start unarmed.** The runner prints "would execute" lines so you can
  watch its decisions for an hour before letting it sign anything.
- **Fund the executor key only for gas.** It never holds principal — the
  flash loan provides that per-transaction and the contract holds any profit
  until you sweep. The key's blast radius is its gas balance plus whatever
  profit is sitting unswept in the contract.
- **Mind the gas-cap and consecutive-fail breakers.** On a chain with
  expensive gas (mainnet), a run of reverts on a spread that keeps
  evaporating can burn real money in gas alone. The `dailyGasCapWei` and
  `maxConsecFails` breakers exist precisely for this; set them.
- **Compounding is optional and capped.** It grows your notional from
  realized profit up to `baseNotional × compoundCapMul` (default 3×), so a
  hot streak scales you up without an unbounded position size. Leave it off
  until you trust the setup.

The treasury split lets profit flow somewhere useful automatically — e.g.
route a share to a cold address on every sweep so not all earnings sit in a
hot-key-controlled contract. `setTreasury(beneficiary, bps)` on-chain,
`AUTO_SWEEP_EVERY_N` in the loop.

## Cross-chain monitoring, honestly

The backend's Cross-Chain Monitor ranks every configured chain's best
same-chain spread together. It is genuinely useful for deciding *where* to
point execution — but it is not, and doesn't claim to be, an atomic
cross-chain trade. If you came looking for "borrow on chain A, arbitrage
against chain B in one shot," that isn't a thing flash loans can do on
public chains today: bridges take real time, and anything happening across
that gap carries settlement risk a same-chain flash loan is specifically
designed to avoid. What you get instead: a live answer to "which of my
chains is hottest right now," with execution staying fully atomic on
whichever one wins.

## Liquidation watching, honestly

The liquidation scanner reads real health factors, live, from Aave's own
`Pool.getUserAccountData` — no staleness risk from subgraph indexing bugs.
But it only watches addresses you already know about. Building a
comprehensive watchlist means crawling Aave's subgraph once for the full
borrower list (documented in technical.md, needs your own Graph API key)
and refreshing it periodically — this kit gives you the watching half, not
the discovery half. Many production liquidation bots split these into
separate jobs for exactly this reason: discovery is infrequent and
expensive to run continuously; watching a known list is cheap and worth
running every block.

## Two flash sources, and why it matters which you pick

If the asset you want to borrow is DAI on Ethereum mainnet, use
`ARRBYFlash3156.sol` against DssFlash instead of `ARRBY.sol` against Aave —
DssFlash's fee is verified `0`, so every basis point of spread you find is
pure profit instead of losing ~5bps to Aave's premium before you even start.
For anything else, or on other chains, Aave's `ARRBY.sol` remains the
broadly-available default. `backend/src/flash-sources.js` automates this
choice — it checks whichever sources are configured for a chain and asset
and returns the cheaper one, live, rather than assuming DssFlash is always
better (a nonzero-fee ERC-3156 lender could in principle be worse than
Aave for some other asset; the comparison is real, not hardcoded).

## Where this sits relative to your existing infrastructure

If you're running this as part of PYTHAI/AgenticPlace/mindX/BANKON rather
than standalone:

- **Deployment** already follows your standard: Foundry, Apache-2.0,
  no-admin-post-renounce, mainnet-only. No changes needed there.
- **Chain selection** can defer to your own ALLCHAIN registry instead of the
  static `config/chains.json` once `chainmap_sync.js` is pointed at the real
  endpoint (see technical.md) — useful if you want ARRBY deployable to any
  of ALLCHAIN's 2500+ chains without hand-maintaining addresses here.
- **Observability** can flow into mindX/RAGE memory via `mindx_client.js`'s
  `logRun()` so every ARRBY execution — profitable or reverted — becomes
  part of the same record mindX already publishes to rage.pythai.net,
  rather than living only in this contract's event logs.
- **Metering/access control on the console itself** (as opposed to the
  on-chain transaction) can run through your existing x402 Algorand rails
  via parsec-wallet, using `x402_gate.js` as the pattern — e.g. gating
  "Execute Flash Loan" behind a small per-run payment the same way mindX's
  own query routes are metered.

None of that is required for ARRBY to work on its own — the contract,
tests, deploy script, and UI are a complete, self-contained kit without any
of it. The integrations are there for when/if you want ARRBY to stop being
a standalone tool and start being one more component in the same ecosystem
as AgenticPlace, mindX, and BANKON.
