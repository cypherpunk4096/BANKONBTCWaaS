# PROPOSAL — ₿ PAIRS + ⟲ SPINTRADE (modular inclusion, nothing overwritten)

The ₿itcoin blockchain expressed as **chain-native trading pairs, priced in SAT** —
the venue is the blockchain itself: the mempool is the order book, the last block is
the last fill. **No external feed, no fiat** (consistent with the BANKON policy that
the oracle stays chain-native). Context: this software is used to **swap
cryptocurrency for other assets** — SPINTRADE is the venue view of that fact, and the
client opts into it explicitly.

## Modular inclusion — what was ADDED (no existing file overwritten)

| Piece | File | Attach | Detach |
|---|---|---|---|
| Pairs engine + API | `bankon-waas/pairs.mjs` (new) | `node pairs.mjs` → `:8089/api/pairs`, **or** one additive line in server.mjs: `app.use(pairsRouter())` | don't run it / remove the one line |
| SPINTRADE tab | `SpintradeTab` in bankon-qt (new class) | toolbar **⟲ SPINTRADE** checkbox | uncheck — the tab is **destroyed**, not hidden |

`server.mjs` itself is untouched — mounting is the operator's opt-in.

## The pairs (all exact, from the node's RPC alone)

- **SATPAY** — the headline price in SAT: what an on-chain payment costs *right now*
  (typical 140 vB tx — 1-in/2-out P2WPKH — × the next-block SAT/vB ask), with the
  full confirmation-depth ladder (next / 3blk / 6blk / 1day).
- **SAT/vB** — the blockspace market: ask ladder from `estimatesmartfee`,
  floor from `mempoolminfee`, last fill from the tip block's feerate percentiles.
- **₿TC/BLOCK** — reward last block: subsidy + fees, integer satoshis rendered to 18 dp.
- **SAT/HASH** — what the last block paid per expected hash (fees ÷ exact work from
  `bits`, BigInt/Decimal — ~4e-18 sat today: this is why the 18-dp standard exists).
- **₿TC/DAY** — issuance flow (subsidy × 144).
- **vB/BLOCK** — blockspace supply vs. last block's usage.

## SPINTRADE UX law (client sovereignty)

1. **Default OFF, every session** — never persisted ON; the client chooses to open it.
2. **The innerstand gate** — enabling asks consent first, stating plainly that this
   software is used to swap cryptocurrency for other assets; declining reverts the
   toggle untouched.
3. **State obvious at all times** — the toolbar control reads **⟲ SPINTRADE ON in
   candle green (#16C784)** while attached, **⟲ SPINTRADE OFF in red** otherwise.
4. **Absolute attach / absolute detach** — attach constructs the tab; detach
   `deleteLater()`s it. No timers, no polling, zero footprint when off.
5. **🧊 ICE compatibility** — under AIRGAP (`setnetworkactive=false`) SPINTRADE
   suspends honestly ("no fresh mempool, no honest quotes") instead of quoting a dark
   network; attach/detach events are recorded in the `.history` evidence trail.

## Data path

Qt tab → WaaS `/api/pairs` when up (shared engine) → falls back to computing the same
pair definitions directly from node RPC (works with only `bitcoind` running). Both
paths use exact integer arithmetic (BigInt in JS, int-satoshi + Decimal in Python).
