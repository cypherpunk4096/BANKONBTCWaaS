# ARRBY — agnostic EVM flash-loan arbitrage kit

Two Solidity contracts (Aave V3 and ERC-3156/DssFlash flash sources), a
Foundry test suite, a deploy script, a monitoring backend, and a console UI.
Same bytecode on every EVM chain that has a compatible flash-loan provider —
only a config value changes per chain.

```
flashloan-kit/
├── src/
│   ├── ARRBYCore.sol               # shared base: ownership, reentrancy, safe token calls, treasury, guardrails
│   ├── ARRBY.sol                   # Aave V3 flashLoanSimple arbitrage executor
│   ├── ARRBYFlash3156.sol          # ERC-3156 sibling — 0%-fee DAI via DssFlash, or any ERC-3156 lender
│   └── interfaces/
│       ├── IPool.sol               # Aave V3 flashLoanSimple + callback
│       ├── IERC3156.sol            # EIP-3156 flash loan standard + EIP-165
│       └── IV2Router.sol           # any Uniswap-V2-shaped router
├── test/
│   ├── ARRBY.t.sol                 # Foundry unit tests (offline, no fork needed)
│   ├── ARRBY3156.t.sol             # tests for the ERC-3156 sibling
│   └── mocks/Mocks.sol             # mock Pool/lender/router for deterministic tests
├── script/Deploy.s.sol             # one script deploys to any chain via env vars
├── config/chains.json              # Aave Pool + ERC-3156 lender + router addresses, per chain
├── backend/                        # multi-chain monitoring service (REST + WebSocket + /treasury)
│   ├── src/server.js               # scanner-manager + liquidation-scanner + treasury reader
│   ├── src/scanner-manager.js      # one signal scanner per chain → cross-chain leaderboard
│   ├── src/liquidation-scanner.js  # Aave health-factor watch, second opportunity type
│   ├── src/flash-sources.js        # picks the cheaper of Aave vs ERC-3156 per call
│   ├── src/flashbots-relay.js      # MEV-protected mainnet submission (verified endpoint)
│   └── src/tenderly-sim.js         # pre-flight simulation (stub — needs your own account)
├── integrations/
│   ├── arby_signal.js              # isolated detection state machine (signal source)
│   ├── signal_runner.js            # headless signal → quote/execute
│   ├── autoearn.js                 # autonomous earning loop: breakers, compounding, auto-sweep
│   └── … mindx / chainmap / x402 adapters (stubs, see technical.md)
├── ui/index.html                   # console: finder + cross-chain + flow + execute + autoearn/treasury
├── explanation.md · usage.md · technical.md · practical.md
├── foundry.toml
└── remappings.txt
```

Read `explanation.md` first if you want the concept before the commands.
Read `usage.md` if you just want to run it. Read `technical.md` for the
security model, audit notes, and backend architecture. Read `practical.md`
for a worked example with real numbers and a pre-deploy checklist.

## Sources mapped into this build

The Aave V3 and ERC-3156 contract designs, the liquidation scanner, and the
Flashbots adapter were built against reference implementations from
[github.com/deltaloans](https://github.com/deltaloans) — a 47-repo research
library covering flash loan protocols (Aave, MakerDAO's `dss-flash`,
Flashbots' `simple-arbitrage`, marble's `flash-lending`), liquidation bots
(dYdX's `liquidator`, ARCx's `liquidator-bot`), and lending infrastructure —
plus MakerDAO/Sky's `dss-flash` and Flashbots' own docs directly, for the
specific verified addresses and endpoints cited throughout technical.md.

## Why it's agnostic

Aave V3's `Pool.flashLoanSimple` has the **identical ABI on every chain** it's
deployed to — Ethereum, Arbitrum, Optimism, Base, Polygon, Avalanche, and
more. `ARRBY.sol` only takes the Pool address in its constructor,
so the exact same compiled bytecode is valid everywhere. The arbitrage legs
call `IV2Router`, matched by any Uniswap-V2 fork (Sushiswap, Quickswap,
Camelot, PancakeSwap V2, Trader Joe...), so the DEX pair is a runtime
parameter, not a hardcoded dependency. `ARRBYFlash3156.sol` does the same
against any EIP-3156 lender instead of Aave specifically — see technical.md
for why that matters (DssFlash's verified 0% fee on DAI).

`config/chains.json` is the single place per-chain addresses live. Extend it
against your own chain registry (e.g. `agenticplace.pythai.net/allchain.html`)
to add more chains without touching the contract.

> Chains without Aave V3 or an ERC-3156 lender (very new L2s): the same
> contract shape works against Balancer V2's zero-fee flash loans too —
> different interface (`flashLoan(recipient, tokens[], amounts[], data)`,
> no allowance-based repay), not wired in yet. Ask if you want that third
> sibling generated.

## 1. Install Foundry & dependencies

```bash
curl -L https://foundry.paradigm.xyz | bash
foundryup
cd flashloan-kit
forge install foundry-rs/forge-std --no-commit   # already vendored in lib/ here
```

## 2. Test (offline — no RPC required)

```bash
forge test -vv
```

Covers, across both contracts: a profitable arbitrage that repays the loan
and lets the owner withdraw profit (including a 0%-fee ERC-3156 run and a
nonzero-fee one), an unprofitable trade that reverts the whole transaction,
non-owner calls being rejected, a spoofed callback being rejected, path
validation, an already-expired deadline being rejected (`Expired`, v1.5),
EIP-165 `supportsInterface` conformance (v1.5), on-chain cumulative-profit
accounting and the treasury sweep split, the per-run notional cap
(`NotionalTooLarge`), and owner renouncement (v1.6). The backend's
leaderboard-sorting logic, the console's flow-pipeline state machine, and
autoearn's circuit breakers + compounding were also exercised directly in
this session (see technical.md for what was and wasn't machine-verified).

> This environment couldn't reach `binaries.soliditylang.org` to fetch a
> solc binary for a live compile, so `forge build`/`forge test` haven't been
> executed in-session — run them locally before deploying. The Solidity has
> been manually traced against the mock math (see test file comments) and
> follows each lender's real repay pattern (Aave: approve + pull; ERC-3156:
> approve + pull via allowance, per spec).

## 3. Deploy — same script, any chain

```bash
export AAVE_POOL=0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2   # from config/chains.json
export OWNER=0xYourAddress
export ETH_RPC_URL=https://...
export DEPLOYER_KEY=0x...

forge script script/Deploy.s.sol:Deploy \
  --rpc-url $ETH_RPC_URL \
  --private-key $DEPLOYER_KEY \
  --broadcast --verify
```

Mainnet-only, no upgradeable proxy, no admin key retained beyond the `owner`
you pass in — `owner` can call `renounceOwnership()` at any time to go fully
immutable (cypherpunk2048 standard). Deploying `ARRBYFlash3156.sol` follows
the same pattern; it takes no lender address at construction (the lender is
a per-call parameter), just an owner.

Repeat for any other chain in `config/chains.json` by swapping `AAVE_POOL`
and `--rpc-url`.

## 4. Run the backend (multi-chain monitoring)

```bash
cd backend && npm install
cp watch-config.example.json watch-config.json   # edit with real RPCs/tokens/routers
npm start
```

Runs one signal scanner per configured chain concurrently, an Aave
liquidation watch over any addresses you list, and serves both over
WebSocket (`ws://localhost:8787/ws`) and REST (`/leaderboard`,
`/liquidations`, `/health`). See usage.md for the full walkthrough and
technical.md for exactly what "cross-chain" does and doesn't mean here.

## 5. Run the console

`ui/index.html` is a single file — no build step. Open it directly in a
browser, or serve it:

```bash
cd ui && python3 -m http.server 8080
```

Then:
1. **Connect Wallet** — prompts MetaMask (or any injected wallet), switches
   to the chain you picked.
2. **Cross-chain monitor** — click Connect with the backend running to see
   every configured chain's best spread ranked together, live.
3. **Flow** (section 03) — the whole opportunity lifecycle as one animated
   pipeline (detect → confirm → quote → submit → mine → swaps → repay →
   settle), with per-stage timing and live block-by-block feedback while a
   transaction is pending. The finder drives its left edge; execution drives
   its right.
4. Paste the deployed contract address, the borrowed asset, the two routers
   and swap paths, your minimum acceptable profit, and a deadline window.
5. **Quote (read-only)** calls `quoteArbitrage` — zero gas, tells you the
   expected profit before you risk anything, and lights the quote stage.
6. **Execute Flash Loan** submits the real transaction. The pipeline and the
   detailed ledger both update through every step as the transaction is
   mined, decode the `ArbitrageExecuted` event for realized profit, and — on
   a revert — mark the remaining stages failed and name the reason
   (Unprofitable / Expired / BadPath). No funds beyond gas are ever at risk.

## Extending

- **More chains**: add an entry to `config/chains.json`, deploy with that
  chain's `AAVE_POOL` (and/or ERC-3156 `flashMint.lender`) env var. No
  contract changes.
- **More DEXes**: any router matching `IV2Router` (`swapExactTokensForTokens`
  + `getAmountsOut`) works as `routerA`/`routerB` — pass its address at
  call time, nothing to redeploy.
- **MEV protection**: `backend/src/flashbots-relay.js` routes mainnet
  execution through Flashbots Protect instead of the public mempool —
  wire it into `signal_runner.js`'s execute path when you're ready.
- **x402-metered execution**: if you want the UI to gate `Execute Flash Loan`
  behind an x402 payment (e.g. via parsec/parsec-wallet on Algorand for the
  metering leg while the loan itself runs on an EVM chain), the quote step is
  the natural place to insert a 402 challenge before the real tx is signed —
  happy to wire that in as a follow-up.

