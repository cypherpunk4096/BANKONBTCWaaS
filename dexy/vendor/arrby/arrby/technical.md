# ARRBY — technical.md

## Architecture

```
src/ARRBY.sol                  ← the contract (IFlashLoanSimpleReceiver)
src/interfaces/IPool.sol       ← Aave V3 Pool + callback interface
src/interfaces/IV2Router.sol   ← Uniswap-V2-shaped router interface
test/ARRBY.t.sol                ← Foundry unit tests, fully offline
test/mocks/Mocks.sol            ← MockERC20 / MockPool / MockRouter
script/Deploy.s.sol             ← one deploy script, env-driven per chain
config/chains.json              ← Aave Pool + router addresses per chain
ui/index.html                   ← single-file dApp console
integrations/
  chainmap_sync.js              ← ALLCHAIN registry sync (stub — see below)
  mindx_client.js                ← mindX pre-flight advisory + run logging (stub)
  x402_gate.js                   ← x402/parsec payment gate for console access (stub)
```

## Contract design

### Why `flashLoanSimple`, not multi-asset `flashLoan`

Aave V3 exposes both a single-asset `flashLoanSimple` (cheaper, one token)
and a multi-asset `flashLoan` (more flexible, more gas, more surface area).
ARRBY only needs one borrowed asset per run — the arbitrage itself is a
two-leg round trip in that one asset — so `flashLoanSimple` is strictly
better here: less gas, smaller attack surface, and it's the interface Aave
recommends for exactly this use case.

### The repay pattern

Aave V3's flash loan doesn't `transfer` funds back to itself — it calls
`transferFrom(receiver, pool, amount + premium)` *after* `executeOperation`
returns `true`. That means the receiver's job is to end `executeOperation`
having **approved** the Pool for `amount + premium`, not to have sent
anything. ARRBY does exactly that:

```solidity
IERC20Min(asset).approve(address(POOL), amountOwed);
```

Approving more than the Pool can actually collect isn't a risk — an
allowance is a ceiling, not a transfer, and the Pool only ever pulls what
it's owed.

### The profitability gate is the only slippage control, and that's deliberate

Both swap legs pass `0` as `amountOutMin` to the router. That looks
dangerous in isolation, but the real protection is the check right after:

```solidity
if (grossReturned < amountOwed + arb.minProfit) revert Unprofitable(...);
```

Because the entire transaction reverts if this fails, there's no
intermediate state where a sandwich attacker can extract value from a bad
fill — the worst outcome is the transaction reverts and you paid gas.
Per-leg slippage minimums would add gas cost and complexity without adding
protection beyond what the aggregate check already provides. If you want
per-leg minimums anyway (e.g. because you're also worried about MEV
statistics, not just economic outcome), both router calls are already
`calldata`-driven, so this is a small, isolated change — ask if you want
that variant.

### Non-standard ERC-20 handling (added in the v1.2 audit pass) — highest-severity finding so far

The v1.1 contract called tokens through an interface declaring
`approve(...) returns (bool)` and `transfer(...) returns (bool)`. Solidity's
ABI decoder **reverts when a call declared to return a bool returns no data
at all** — and the single most flash-borrowed token on Ethereum mainnet,
USDT, returns nothing from both functions. In practice this meant ARRBY
could not borrow, swap through, or withdraw USDT (or any of the other
no-return tokens: BNB's old ERC-20, OMG, etc.) — every run would have
reverted at the first `approve`. Severity: high (core functionality broken
for a major asset class), likelihood: certain on affected tokens.

Fix: all token interactions now go through `_safeApprove`/`_safeTransfer`,
which use a raw `call`, accept empty return data as success, and treat
`false` as failure when data *is* returned. `_safeApprove` additionally
resets the allowance to zero before setting a new value, because USDT
reverts on any nonzero→nonzero allowance change. A `MockNoReturnToken`
that replicates both USDT behaviors was added to the test suite with a
regression test (`test_WithdrawWorksWithNoReturnToken`).

### Path validation (added in the v1.1 audit pass)

The original version trusted the caller (the owner) to pass consistent
paths. The audited version validates, before any router call:

```solidity
function _validatePath(address asset, ArbParams calldata arb) private pure {
    if (arb.pathAtoB.length < 2 || arb.pathBtoA.length < 2) revert BadPath();
    if (arb.pathAtoB[0] != asset) revert BadPath();
    if (arb.pathBtoA[arb.pathBtoA.length - 1] != asset) revert BadPath();
    if (arb.pathAtoB[arb.pathAtoB.length - 1] != arb.pathBtoA[0]) revert BadPath();
}
```

This closes a real (if low-severity, owner-only) foot-gun: previously a
mistyped path could leave the contract holding an intermediate token instead
of the borrowed asset, with no revert to signal the mistake until the
repayment step failed for an unrelated reason.

### Reentrancy lock (added in the v1.1 audit pass)

`initiateArbitrage` now carries a `nonReentrant` modifier (a simple
`_locked` flag, not OpenZeppelin's implementation, to keep the contract
dependency-free). Aave's own call pattern is synchronous and single-shot, so
this isn't closing a known Aave-side hole — it's defense-in-depth against a
non-standard ERC-20 with transfer hooks, or a router with an unexpected
callback, re-entering `initiateArbitrage` mid-flight.

### What was removed in the audit pass

`event ArbitrageReverted(string reason)` existed in the original draft but
could never actually fire: by the time `executeOperation` hits a revert
condition, the EVM is already unwinding state, and an event emitted right
before a `revert` is discarded along with everything else in that call
frame. Keeping it around implied a monitoring capability the contract didn't
have. It's gone; use the transaction receipt's `status` field (the UI
already does this) to detect a reverted run.

### Access control model

- `initiateArbitrage`, `withdraw`, `renounceOwnership` — owner-only.
- `executeOperation` — callable by anyone, but the first two lines make it a
  no-op for anyone except the Pool calling back into a loan this same
  contract initiated:
  ```solidity
  if (msg.sender != address(POOL)) revert NotPool();
  if (initiator != address(this)) revert NotInitiator();
  ```
- No upgradeability, no proxy, no admin key beyond `owner` — and `owner` can
  permanently zero itself out via `renounceOwnership()`. This matches the
  cypherpunk2048 standard: Apache-2.0, no admin keys post-deploy if you
  choose to renounce, no upgradeable proxy, ever.

## Gas notes

Two router calls plus one Aave callback is the dominant cost. Expect
roughly 250k–400k gas per run depending on the router (some V2 forks meter
differently) and whether the tokens involved have any non-trivial transfer
logic. `quoteArbitrage` is a `view` call and costs nothing to call
off-chain (it does cost gas if called from another contract on-chain).

## Testing strategy

`test/mocks/Mocks.sol` implements a `MockPool` that mimics Aave's exact
call/callback/pull sequence and a `MockRouter` with a *configurable fixed
exchange rate*, so tests can dial in a profitable or unprofitable trade
deterministically without needing a mainnet fork. This keeps `forge test`
fast and offline. For a closer-to-production check before a real deploy,
fork-test against the actual chain:

```bash
forge test --fork-url $ARBITRUM_RPC_URL -vv
```

(Not included by default — add a `*.fork.t.sol` file if you want CI to run
both offline and fork suites separately.)

> **Compilation note:** this kit's tests were written and traced by hand
> against the mock contracts' arithmetic; the sandbox this was built in
> could not reach a solc binary host to actually run `forge build`/`forge
> test`. Run them yourself before deploying — the logic has been reviewed
> twice but hasn't been machine-verified in this session.

## Signal isolation (added v1.3): detection separated from action

The opportunity finder's detection logic is now isolated as a standalone
signal source — `integrations/arby_signal.js` — with the trigger expressed
as an explicit state machine:

```
IDLE ──start()──▶ WATCHING ──bps ≥ threshold, N consecutive ticks──▶ SIGNAL
                     ▲                                                  │
                     └────────── bps < clearBps (hysteresis) ───────────┘
```

Three properties make this a usable trigger rather than a noisy one:

- **Confirmations**: the spread must hold above the threshold for N
  consecutive scans before firing. One tick of a thin pool briefly
  misquoting doesn't trigger anything.
- **Hysteresis**: after firing, the signal doesn't re-arm until the spread
  decays below a separate, lower `clearBps`. A spread oscillating around the
  threshold produces exactly one signal, not one per tick.
- **Detection never acts.** The module has no keys, no contract writes, no
  UI. It emits `tick` / `signal` / `clear` / `error` events; the consumer
  decides what a signal *does*. This is the isolation the trigger needs to
  be trustworthy — the thing that watches cannot be the thing that spends.

The state machine's transition logic is machine-verified: the build session
drove `_advance()` directly through confirm-reset-confirm-fire-hold-clear-
refire sequences with assertions (no network involved), all passing.

Two consumers ship with it:

1. **`integrations/signal_runner.js`** — headless Node runner with three
   escalating trigger levels via `TRIGGER=` env: `log` (print + optional
   mindX logging), `quote` (re-confirm the signal with ARRBY's own on-chain
   `quoteArbitrage` — live premium, exact paths — and stand down if the
   contract math disagrees), and `execute` (submit `initiateArbitrage`,
   which additionally requires `EXECUTE_ARMED=yes` and an `EXECUTOR_KEY`).
   Even at full escalation the contract's `Unprofitable` revert is the final
   gate; the worst case is a reverted transaction.
2. **The console UI** — embeds the same state machine (visible as the
   idle/watching n/N/SIGNAL badge, with the threshold drawn as a red dashed
   line on the D3 spread chart). "On signal" modes mirror the runner:
   notify, prefill + quote, or auto-execute — the last gated behind an
   explicit per-session "arm" checkbox *and* a connected wallet, and it
   sets `minProfit` to half the trigger threshold so a real-but-slightly-
   decayed spread still clears while the revert protection stays live.

## v1.6 — shared core refactor, treasury, and autoearn

### Audit-driven refactor: one core, not two copies

The two contracts had grown duplicate copies of `_safeApprove`,
`_safeTransfer`, the reentrancy lock, ownership, path validation, and
`withdraw`/`renounceOwnership`. Duplication is an audit smell in itself —
two implementations of a security-sensitive helper can silently diverge
under future edits, and a fix applied to one can be forgotten on the other.
v1.6 hoists all of it into `src/ARRBYCore.sol`, an abstract base both
`ARRBY` and `ARRBY3156` now inherit. There is now exactly one audited
implementation of each shared concern. The concrete contracts shrank to
just their flash-source-specific mechanics (Aave's `executeOperation` vs.
ERC-3156's `onFlashLoan`, and each one's `quoteArbitrage`).

Behavior is unchanged for existing callers: constructors keep the same
signatures, `withdraw`/`renounceOwnership`/`owner` all still work (now
inherited), and every prior test still references the same error selectors
(inherited errors remain in the contract's namespace, so
`ARRBY.NotOwner.selector` still resolves).

### Treasury (on-chain)

`ARRBYCore` adds real on-chain treasury accounting:

- **`cumulativeProfit[asset]`** — lifetime realized profit per asset,
  written by `_recordProfit` on every successful arbitrage. Crucially this
  is a **storage-only** write inside the flash-loan callback — it adds no
  new external call to the most sensitive code path, so it can't introduce
  a reentrancy vector.
- **`setTreasury(beneficiary, bps)`** — designate a beneficiary and its
  share (basis points) of each sweep. Defaults to the owner at 0 bps, so
  the feature is entirely opt-in and changes nothing until configured.
- **`sweepTreasury(token)`** — splits the contract's balance between the
  treasury (`treasuryBps`) and the owner (remainder) and sends both out.
  This is a **separate transaction from any flash loan** — the callback
  never distributes funds, it only records accounting. Sweep's only external
  calls are the two payouts, both under the reentrancy lock and both via the
  USDT-tolerant `_safeTransfer`.

Design choice worth stating: distribution deliberately does *not* happen
automatically inside the arbitrage transaction. Doing so would add
attacker-influenceable external calls (to whatever `treasury` is) into the
flash-loan callback for no real benefit — you'd pay that gas on every run
regardless of whether you wanted to move funds yet. Accounting in the
callback, distribution on your own schedule, is both cheaper and safer.

### Notional guardrail

**`setMaxNotional(cap)`** (0 = unlimited) makes any single run revert with
`NotionalTooLarge` if it tries to borrow more than the cap. This is the
on-chain half of autoearn's safety story: even if the off-chain loop is
misconfigured or compromised, it physically cannot borrow more than the cap
the owner set on-chain. Belt and suspenders — the loop also caps itself.

### Autoearn (`integrations/autoearn.js`)

The headline feature: an autonomous earning loop. It's a thin, honest
orchestrator — it cannot manufacture profit that isn't there, because every
execution still passes through the contract's on-chain `minProfit` check, so
the worst outcome of any single iteration is a reverted transaction (gas
spent, principal safe). What it adds over `signal_runner.js` is unattended
operation *with the guardrails that make "leave it running" defensible*:

- **maxNotional** — never borrow more than this (mirrored on-chain via
  `setMaxNotional`)
- **cooldownMs** — minimum gap between executions
- **maxConsecFails** — circuit breaker: halt after N reverts/errors in a row
- **dailyGasCapWei** — halt once 24h cumulative gas spend crosses a budget
- **maxRuntimeMs** — hard wall-clock stop
- **compounding** — grow notional from realized profit, capped at
  `baseNotional × compoundCapMul` so a good streak can't run away
- **autoSweepEveryN** — periodically call `sweepTreasury` so profit leaves
  the contract on a schedule, split per the on-chain `treasuryBps`

Detection stays separated from action: autoearn *consumes* `ArbySignal`'s
events, it doesn't re-implement scanning, and the signing key is used only
after the contract's own `quoteArbitrage` confirms profit. Its circuit
breakers and compounding logic were unit-tested headlessly in the build
session (consecutive-fail trip, runtime trip, gas-cap trip, compound growth
and cap, idempotent halt) — all passing.

The console (section 06) mirrors these controls for interactive use with a
live cumulative-profit D3 chart, an on-chain treasury readout
(`cumulativeProfit`, `treasury`, `treasuryBps`, contract balance), a
sweep-now button, and the circuit-breaker state — but true unattended
operation belongs in the Node runner, not a browser tab.

The backend exposes a read-only `/treasury` endpoint that reports on-chain
`cumulativeProfit` and contract balance for any configured chain whose
watch-config carries an `arrbyAddress`.

## v1.5 — EIP conformance audit + unified real-time flow

### Contract audit findings (v1.5)

Three changes, applied to both `ARRBY.sol` and `ARRBYFlash3156.sol`:

1. **Fail-fast deadline check.** Previously an already-expired `deadline`
   was only caught deep inside the router's own `block.timestamp <= deadline`
   guard — *after* the flash loan had already been taken, wasting the gas of
   borrowing before the inevitable revert. Both contracts now check
   `arb.deadline >= block.timestamp` at the top of `initiateArbitrage` and
   revert with `Expired(deadline, now)` before touching the lender. Low
   severity (owner-only, funds never at risk) but a real gas-waste fix.

2. **`ArbitrageInitiated` event.** Emitted right before the flash loan is
   taken, giving off-chain consumers (the console's real-time pipeline,
   mindX) an explicit start marker to correlate with the terminal
   `ArbitrageExecuted` event by transaction hash — rather than inferring the
   start from the transaction's mere existence.

3. **EIP-165 on the ERC-3156 sibling.** `ARRBYFlash3156.sol` now implements
   `supportsInterface`, advertising both the ERC-165 self-id (`0x01ffc9a7`)
   and the ERC-3156 flash-borrower interface id. That borrower id is the
   `onFlashLoan(address,address,uint256,uint256,bytes)` selector,
   `0x23e30c8b` — **computed and verified in-session** with keccak256, not
   guessed (an earlier placeholder was wrong by a full 4 bytes and was
   corrected). ERC-3156 lenders and tooling commonly introspect a receiver
   before calling into it; advertising conformance is part of being a
   well-behaved 3156 citizen, and aligns with the cypherpunk2048 preference
   for standards-conformant, clearly-typed components. `ARRBY.sol` (the Aave
   receiver) deliberately does *not* add this — Aave identifies receivers by
   its Pool-only `msg.sender` check, not by ERC-165 introspection, so
   advertising it there would imply a discovery path Aave doesn't use.

New regression tests cover both the `Expired` revert (both contracts) and
`supportsInterface` returning true for the two real interface ids and false
for a random one.

### Why no more surface than that (cypherpunk2048)

The temptation on an "audit and improve" pass is to add rescue functions,
pausability, admin setters, configurable fees. All of those were considered
and rejected: they add attack surface and admin trust for capability this
single-purpose contract doesn't need. The contract still has no upgradeable
proxy, no admin key beyond a renounceable `owner`, no `receive()`/`fallback`
(so it can't accidentally custody ETH), and Apache-2.0 licensing —
consistent with the cypherpunk2048 standard the rest of the stack follows.

### Unified real-time flow (UI)

The three previously-separate moments — finding an opportunity, taking it,
and watching it settle — are now one continuous pipeline (console section
03, "Flow"). Eight stages: **detect → confirm → quote → submit → mine →
swaps → repay → settle**. Every part of the UI drives one shared `flow`
engine via `flow.mark(stage, state, detail)` instead of poking the DOM
directly, so the whole workflow animates as a single object with per-stage
timing:

- The **signal state machine** drives `detect` and `confirm` — the first
  qualifying tick opens a fresh flow attempt and lights `detect`; each
  confirmation advances the `confirm` counter; a spread that evaporates
  before confirming marks `confirm` failed and the attempt aborts.
- `quote()` drives the `quote` stage and writes the expected profit into the
  flow's meta row, colored by sign.
- `execute()` drives `submit → mine → swaps → repay → settle` with
  **live block-by-block feedback**: while the transaction is pending it
  subscribes to the provider's `block` event and shows "block +N" ticking up
  in the `mine` stage and a "blocks waited" counter, so a slow inclusion is
  visible rather than an opaque spinner. On success it decodes the
  `ArbitrageExecuted` event for realized profit; on revert it marks the
  remaining stages failed and surfaces *why* (Unprofitable / Expired /
  BadPath / rejected-in-wallet, matched from the revert reason).
- A manual Quote/Execute (skipping the finder) seeds the same pipeline with
  a "manual" detect stage, so hand-driven runs animate identically.
- A backend cross-chain signal reflects on the `detect` stage too
  (informational — backend-signal execution still routes through
  `signal_runner.js`, not the browser wallet).

The total-elapsed timer runs from the first `detect` through settle, so you
get an end-to-end latency number for the whole opportunity lifecycle. The
detailed event stream (the old log) remains underneath as the verbose
record; the pipeline is the at-a-glance view.

## ERC-3156 sibling contract (added v1.4): a second, often-cheaper flash source

`src/ARRBYFlash3156.sol` is ARRBY.sol's sibling for the standard EIP-3156
flash loan interface rather than Aave's `flashLoanSimple`. It carries every
v1.1/v1.2 audit fix forward (path validation, safe token calls, reentrancy
lock, zero-address checks, renounceable ownership) — same design, different
lender interface.

Why it exists: MakerDAO/Sky's `DssFlash` (mainnet:
`0x60744434d6339a6B27d73d9Eda62b6F66a0a04FA`) flash-mints DAI at a verified
`flashFee() == 0` — genuinely free flash liquidity, not just cheap. Verified
directly against the deployed contract's source (`flashFee` returns `0`
unconditionally for DAI). Where DAI is the asset you want to borrow, this
contract is strictly better than paying Aave's ~5bps. The interface is the
OpenZeppelin-standardized EIP-3156 shape, so any other compliant lender
works here too, not just DssFlash — `backend/src/flash-sources.js` compares
whichever sources are configured for a chain and picks the cheapest one
live, at call time, rather than assuming.

One design note specific to this contract: EIP-3156's `onFlashLoan`
callback doesn't pass the lender's address as a parameter the way Aave's
callback does — `msg.sender` inside the callback *is* the lender, by spec.
ARRBY3156 decodes which lender the call claims to be from its own params
and requires `msg.sender == arb.lender`, closing the gap without needing
extra state to track "which lender did I just call."

## Backend (added v1.4): multi-chain monitoring service

`backend/` is a standalone Node service — the "definitive backend" the
console previously lacked. It turns the browser-only opportunity finder
into something that runs unattended, across every chain you configure, and
feeds a live WebSocket the console's Cross-Chain Monitor panel connects to.

```
backend/
  src/server.js              REST + WebSocket server, ties everything together
  src/scanner-manager.js      one ArbySignal per chain, merged into a leaderboard
  src/liquidation-scanner.js  Aave V3 health-factor watch, second opportunity type
  src/flash-sources.js        Aave vs ERC-3156 — picks the cheaper source per call
  src/flashbots-relay.js      MEV-protected mainnet submission (verified endpoint)
  src/tenderly-sim.js         pre-flight simulation (stub — needs your own account)
  watch-config.example.json   per-chain scan config template
```

### What "cross-chain arbitrage" means here — precisely

A flash loan's atomicity is a single-chain, single-transaction property.
There is no atomic flash loan spanning two chains on public infrastructure
today — bridging takes real time, during which the "arbitrage" carries
settlement and price-movement risk a same-chain flash loan never has. This
backend does not claim otherwise.

What it actually does: runs ARRBY's existing same-chain signal detector
independently on every configured chain, concurrently, and ranks all of
their outputs together in one leaderboard (`scanner-manager.js`). That
answers a real, useful question — *which of my chains has the best
same-chain opportunity right now* — so execution (still fully atomic, still
single-chain) can be pointed at whichever chain is hottest. A genuine
buy-cheap-here/bridge/sell-there trade is a different, non-atomic strategy
that needs a fast bridge, pre-positioned inventory on both sides, and
explicit acceptance of settlement risk — out of scope by design, not by
oversight.

### Liquidation scanner: a second opportunity type

`liquidation-scanner.js` calls Aave V3's `Pool.getUserAccountData` directly
on-chain for a watchlist of borrower addresses — the protocol's own source
of truth for health factor, deliberately avoiding subgraph staleness (Chaos
Labs has documented real V2→V3 event-semantics bugs in Aave's own subgraph
indexing). This is the same category of tool as deltaloans' forked
`liquidator` (dYdX) and `liquidator-bot` (ARCx) repos: watch health factors,
flag accounts crossing the liquidation threshold.

What it doesn't do: discover the full universe of Aave borrowers for you.
That needs a one-time subgraph crawl — Aave's V3 subgraphs now live on The
Graph's decentralized network (the old hosted service was retired in 2024)
and need your own Graph API key. Two verified subgraph IDs to start from
are documented in the file's header (Ethereum: `HB1Z2EAw4rtPRYVb2Nz8QGFLHCpym6ByBX6vbCViuE9F`,
Base: `GQFbb95cE6d8mV989mL5figjaGaKCQB3xqYrr1bRyXqF`). Feed the resulting
address list in as `watchlist` in `watch-config.json`.

### MEV protection: Flashbots Protect (verified endpoint)

`flashbots-relay.js` sends ARRBY's `initiateArbitrage` transaction through
`https://rpc.flashbots.net/fast` (Flashbots' retail-facing Protect RPC)
instead of the public mempool — Ethereum mainnet only. Two concrete
guarantees this buys, both from Flashbots' own docs: the transaction never
lands on-chain if it would revert (a stale signal costs nothing, not even
gas), and it isn't visible in the public mempool while pending, closing the
sandwich-attack window `practical.md` flagged as out of scope for the
contract itself. The more powerful `eth_sendBundle` path via
`relay.flashbots.net` is documented in the file but not wired — it requires
a separate signing keypair for request authentication and mostly matters
for bundling multiple transactions together, which a single self-contained
ARRBY run doesn't need.

### Simulation: Tenderly (stub — needs your account)

`tenderly-sim.js` is a pre-flight dry-run against a Tenderly fork, catching
a decayed spread or an unrelated router revert before spending real gas.
Left as a stub because Tenderly's simulate API needs an account slug,
project slug, and access key that only exist inside your own dashboard —
nothing to verify from here the way Flashbots' public endpoint was. Fill in
the three env vars and it's live; the request/response shape matches
Tenderly's documented Simulate API.

## Opportunity finder (UI, added v1.2)

The console now has a scanner panel that combines two data sources:

1. **DefiLlama reference prices** — `coins.llama.fi/prices/current/
   {chain}:{address}` (free, CORS-enabled, no key). This gives a
   volume-weighted "fair" price for each token. Its role is *sanity
   checking*, not signal generation: if a router pair shows a 40 bps spread
   but one side is wildly off DefiLlama's reference, you're probably looking
   at a stale or near-empty pool whose quote won't survive contact with a
   real transaction.
2. **Live on-chain router quotes** — `getAmountsOut` called directly on
   every router you list, both directions, at your chosen notional. This is
   the *executable* price, including each pool's depth at that exact size.

Each scan tick, the finder computes the full round trip for every ordered
router pair (buy on X, sell on Y), nets out Aave's 5 bps premium, and
renders two D3 charts: a time series of the best pair's spread in bps (with
a zero line marking breakeven-before-gas), and a horizontal bar chart of
every pair's round-trip profit at the latest scan. "Send best pair to
configuration" prefills the execute panel with the winning routers, paths,
and notional — you still Quote (which uses the contract's own on-chain
math and the *real* premium, not the display assumption) before executing.

Design decisions worth knowing:

- **The finder never signs anything.** It runs off a read-only RPC URL you
  provide (or your wallet's provider, read-only), so you can leave it
  scanning without a connected wallet.
- **The 5 bps premium in the finder is a display assumption.** Aave
  governance can change `FLASHLOAN_PREMIUM_TOTAL`; the contract and the
  Quote button always read the live value on-chain. The finder uses a fixed
  5 bps so it can rank pairs without an extra contract call per tick — the
  ranking is unaffected because the premium is constant across pairs.
- **Scan interval floors at 5 seconds** to stay polite to public RPCs and
  DefiLlama's free tier. Each tick costs `N` quotes one way plus up to
  `N×(N−1)` reverse quotes for pair evaluation, so listing many routers
  multiplies RPC load quadratically.
- **A shown spread is not a guaranteed capture.** The bar chart is what the
  pools quoted at scan time; MEV competition and ordinary trading can close
  a spread between scan and execution. `minProfit` in the execute panel is
  what actually protects you.

## Integration points (stubs — see integrations/)

These three files exist to show the *shape* of wiring ARRBY into your wider
stack. None of them guess at real endpoint paths for your systems beyond
what's documented publicly, because getting that wrong would be worse than
leaving a clearly marked TODO:

- **`chainmap_sync.js`** — merges live chain data from ALLCHAIN
  (agenticplace.pythai.net) into `config/chains.json`. The ALLCHAIN page
  itself renders its 2500+ chain list client-side from its own backend; this
  script is ready to point at that backend's real data endpoint the moment
  you give me the path — right now it no-ops safely if `ALLCHAIN_API_BASE`
  isn't set, rather than writing speculative data into a file a contract
  deployment depends on.
- **`mindx_client.js`** — a pre-flight "should I run this" call to mindX
  before `initiateArbitrage`, and a post-run log call so completed (or
  reverted) runs become part of mindX's memory and get published to
  rage.pythai.net. Two route paths are placeholders (`logPath`,
  `advisePath`) — point them at the real routes out of mindX's 350+ and this
  is a real integration, not a pattern.
- **`x402_gate.js`** — gates the console's Execute action behind an x402
  payment settled via parsec-wallet on Algorand, matching the same
  challenge → pay → retry-with-proof shape your BANKON X402AlgorandGateway
  / PYTHAI x402 rails already implement. The `parsecWallet.pay(...)` call is
  the one stand-in for parsec-wallet's real SDK method.

If you share the real mindX route list, the ALLCHAIN data endpoint, and the
parsec-wallet client interface, the next pass turns all three from stubs
into working integrations — happy to do that as a follow-up rather than
guess now and ship something that silently fails against your live
infrastructure.
