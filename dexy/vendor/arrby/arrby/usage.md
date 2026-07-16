# ARRBY — usage.md

Step-by-step, start to finish. No prior context assumed beyond having
Foundry and a browser wallet installed.

## 0. Install Foundry (one time)

```bash
curl -L https://foundry.paradigm.xyz | bash
foundryup
```

## 1. Unzip and enter the project

```bash
unzip arrby.zip && cd arrby
```

`lib/forge-std` is already vendored, so you don't need to run
`forge install` unless you want to update it.

## 2. Run the tests

```bash
forge test -vv
```

You should see the full suite pass: profitable trade + withdraw, revert on
unprofitable trade, revert on non-owner calls, revert on a spoofed Aave
callback, revert on mismatched swap paths, revert on a zero-address
constructor, and owner renouncement. All of this runs offline against
mocks — no RPC, no real money, no real DEX.

## 3. Pick a chain and get its Aave V3 Pool address

Open `config/chains.json`. Pick a chain (say, Arbitrum) and copy its
`aavePool` address. If you maintain a broader chain registry (e.g.
AgenticPlace's ALLCHAIN), `integrations/chainmap_sync.js` can pull from it
instead of the static file — see technical.md.

## 4. Deploy

```bash
export AAVE_POOL=0x794a61358D6845594F94dc1DB02A252b5b4814aD   # Arbitrum's Aave V3 Pool
export OWNER=0xYourWalletAddress
export ARBITRUM_RPC_URL=https://your-rpc-provider/...
export DEPLOYER_KEY=0xyourdeployerprivatekey

forge script script/Deploy.s.sol:Deploy \
  --rpc-url $ARBITRUM_RPC_URL \
  --private-key $DEPLOYER_KEY \
  --broadcast --verify
```

Copy the deployed contract address from the console output. This is a
mainnet deployment the moment you point `--rpc-url` at a mainnet RPC —
there's no separate "prod mode," so double check `AAVE_POOL` and `OWNER`
before you run this.

To deploy the same contract to a second chain, just change `AAVE_POOL` and
`--rpc-url` and run the same command again.

## 5. Open the console

```bash
cd ui && python3 -m http.server 8080
```

Visit `http://localhost:8080` (or just double-click `ui/index.html` — it
has no build step and works as a plain file).

## 5b. (Optional) Scan for opportunities before configuring anything

The top panel of the console is an opportunity finder. Give it:

- a **read-only RPC URL** for the chain (no wallet needed — it only reads),
- the **DefiLlama chain slug** (ethereum / arbitrum / optimism / polygon /
  base / avax),
- **Token A** (what you'd borrow) and **Token B** (the intermediate), with
  decimals,
- two or more **routers**, one per line as `label,address`,
- a **notional** size and a **scan interval** (min 5s).

Click **Start scanning**. Every tick it pulls DefiLlama's reference prices
for both tokens, quotes every router in both directions on-chain at your
notional, and evaluates every buy-on-X/sell-on-Y pair net of Aave's 5 bps
premium. Two live D3 charts show the best pair's spread over time (gold
line vs. a zero/breakeven reference) and each pair's round-trip profit as
bars (green profitable, red not). When something looks good, **Send best
pair to configuration** prefills the execute panel below — then Quote (which
re-checks with the contract's own on-chain math) and Execute as usual.

The finder is informational: a spread on the chart is what the pools quoted
at scan time, not a guarantee it'll still exist when your transaction mines.
That's what `minProfit` is for.

### Turning the finder into a trigger

Below the scan settings is a **Trigger** block. Set a threshold in bps, how
many consecutive scans must confirm it, and a lower "clear" level (hysteresis
so one signal doesn't re-fire every tick). The badge shows the state:
idle → watching n/N → **SIGNAL**. The threshold appears as a red dashed line
on the spread chart so you can see exactly how close the market is to firing.

"On signal" decides what happens:

- **Notify only** — banner, badge, log line. You act manually.
- **Prefill + Quote** — the winning pair is written into the configuration
  panel, minProfit is set to half the threshold, and an on-chain Quote runs
  automatically so you see the contract's own math before deciding.
- **Auto-execute** — does all of the above and then submits the transaction,
  but only if the "Arm auto-execute" box is ticked this session, a wallet is
  connected, and the contract address is set. Un-armed or wallet-less
  signals downgrade to a Quote. Even armed, the contract's minProfit revert
  is the real safety: worst case is a reverted tx.

For headless operation (server, cron, mindX-driven), the same state machine
lives in `integrations/arby_signal.js` with a ready runner in
`integrations/signal_runner.js`:

```bash
cd integrations && npm init -y && npm install ethers
RPC_URL=… TOKEN_A=0x… TOKEN_B=0x… DEC_A=6 \
ROUTERS="sushiswap,0x…;camelot,0x…" \
THRESHOLD_BPS=12 CONFIRMATIONS=2 TRIGGER=quote ARRBY_ADDRESS=0x… \
node signal_runner.js
```

Escalate to `TRIGGER=execute EXECUTE_ARMED=yes EXECUTOR_KEY=0x…` only once
you've watched it behave at `log` and `quote` levels.

## Running the backend (multi-chain monitoring)

For unattended, multi-chain monitoring instead of one browser tab per
chain:

```bash
cd backend
npm install
cp watch-config.example.json watch-config.json
# edit watch-config.json: real RPC URLs, token addresses, routers per chain
npm start
```

This starts a scanner per chain in `watch-config.json` plus a WebSocket
server on `ws://localhost:8787/ws` (REST snapshots at `/health`,
`/leaderboard`, `/liquidations`). Open the console, go to the **Cross-chain
monitor** panel, and click **Connect** (default URL already points at
`ws://localhost:8787/ws`). You'll see a live leaderboard — every configured
chain's best current spread, ranked together — and a D3 bar chart of the
same. Add a `watchlist` of Aave borrower addresses to any chain's config
block to also get liquidation-opportunity flags in the same panel.

Add or remove a chain at runtime without restarting:

```bash
curl -X POST http://localhost:8787/chains -H 'content-type: application/json' -d '{
  "chainKey": "base", "rpcUrl": "https://mainnet.base.org",
  "tokenA": "0x...", "tokenB": "0x...", "decA": 6, "decB": 18,
  "routers": [{"label":"aerodrome","addr":"0x..."}],
  "notional": "3000", "thresholdBps": 12
}'
```

The backend never signs anything by default — it's read-only monitoring.
Execution still goes through the console's wallet flow or
`signal_runner.js`, same as v1.3.

## 6. Fill in the configuration panel

| Field | What goes here |
|---|---|
| Network | The chain you deployed to |
| Contract address | What Step 4 printed |
| Borrowed asset | The ERC-20 you're flash-borrowing (e.g. USDC's address on this chain) |
| Amount | How much to borrow, in human units (e.g. `3000`) |
| Asset decimals | That token's decimals (USDC is usually 6, most others 18) |
| Router A / Path A→intermediate | The DEX + path you're buying on |
| Router B / Path intermediate→A | The DEX + path you're selling back on |
| Minimum profit | The smallest profit you'll accept — anything less and the whole trade reverts |
| Deadline | How many minutes the swaps have to settle before they'd time out |

## Running autoearn (unattended earning)

The autonomous loop with safety rails. First set the on-chain guardrail so
the loop physically can't over-borrow even if misconfigured:

```solidity
arrby.setMaxNotional(5000e6);        // e.g. cap any run at 5000 USDC
arrby.setTreasury(0xYourTreasury, 2500);  // optional: route 25% of sweeps to a treasury
```

Then run the loop — start unarmed to watch it decide before it can spend:

```bash
cd integrations && npm install ethers
RPC_URL=… ARRBY_ADDRESS=0x… EXECUTOR_KEY=0x… \
TOKEN_A=0x… TOKEN_B=0x… DEC_A=6 \
ROUTERS="sushi,0x…;camelot,0x…" \
THRESHOLD_BPS=12 NOTIONAL=3000 \
node integrations/autoearn.js
```

You'll see `(unarmed) would execute …` lines — the loop is detecting,
confirming against the contract's own quote, and standing at the edge of
execution without signing. When you're satisfied, add the rails and arm it:

```bash
AUTOEARN_ARMED=yes COMPOUNDING=yes \
MAX_CONSEC_FAILS=3 COOLDOWN_MS=20000 \
DAILY_GAS_CAP_ETH=0.1 MAX_RUNTIME_HOURS=8 AUTO_SWEEP_EVERY_N=5 \
RPC_URL=… ARRBY_ADDRESS=0x… EXECUTOR_KEY=0x… \
TOKEN_A=0x… TOKEN_B=0x… DEC_A=6 ROUTERS="sushi,0x…;camelot,0x…" \
node integrations/autoearn.js
```

It halts itself on any breaker (too many consecutive fails, gas budget
exhausted, runtime elapsed) and on Ctrl-C, printing session profit and a
count of successful runs. Sweeps move profit out to your treasury/owner
split every N wins.

The console's **Autoearn & treasury** panel (section 06) mirrors these
controls for interactive use — a live cumulative-profit chart, the on-chain
treasury readout, a sweep-now button, and the circuit-breaker state — but
for genuinely unattended operation use the Node runner above, not a browser
tab that can be closed.

## 7. Quote before you execute

Click **Quote (read-only)**. This costs no gas — it calls the contract's
`quoteArbitrage` view function and shows you gross returned, amount owed
(principal + Aave's fee), and expected profit. If expected profit is
negative or under your minimum, don't execute — adjust the routers, pair,
or size.

## 8. Execute

Click **Connect Wallet** first if you haven't, then **Execute Flash Loan**.
Watch the live ledger on the right:

1. Wallet connected
2. Flash loan requested
3. Leg A executed
4. Leg B executed
5. Principal + premium repaid
6. Transaction confirmed

If the trade held up, the profit card turns green with the realized profit
in the asset's own units, decoded straight from the contract's
`ArbitrageExecuted` event. If it didn't hold up by the time your transaction
actually mined, every step after submission shows **reverted** and the
profit card reads **REVERTED** — you lost gas, not principal.

## 9. Withdraw profit

Profit accumulates in the contract's own token balance after each
successful run. Call `withdraw(token, to, amount)` (via a block explorer's
"Write Contract" tab, or a small script) whenever you want it moved to your
own wallet.

## 10. When you're done operating it

```solidity
arrby.renounceOwnership();
```

This is permanent. After this, nobody — including you — can call
`initiateArbitrage` or `withdraw` again. Only do this once you've pulled out
any profit sitting in the contract.
