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

## ICE transport switches (SPINTRADE ↔ ICE only)

The physical links a swap can ride are **just switches**, present in **both the 🧊 ICE tab and
the ⟲ SPINTRADE tab and nowhere else**. The OS is the single source of truth, so a switch flipped
in one tab shows in the other on next refresh — shared state, no duplicate bookkeeping
(`services/ice_transport.py`):

| Switch | Mechanism | Notes |
|---|---|---|
| **VPN** | NetworkManager vpn/wireguard up/down | the shortest-route exit for the exchange |
| **₿luetooth** | rfkill soft-block | the bluetooth exchange path |
| **Ethernet** | `ip link set <nic> up/down` | wired exchange path |
| **Infrared** | rc-core / IrDA protocols | infrared exchange path |

All mutations escalate through `pkexec` (the same wall as the ICE AIRGAP). AIRGAP overrides the
radios beneath the switches; SPINTRADE suspends quoting while the network is dark. SPINTRADE's
**shortest-route locator** picks the nearest connected peer (via ICE geo) as the natural first hop.

## Ordinal minter + evidence hygiene (🧊 ICE)

- **Ordinal minter** (button + chain chooser, Ordinals first): hashes the `.history`, **measures gas
  in SAT** from the local node, inscribes/anchors the digest, then **follows the transaction on the
  ₿itcoin network from your own node** (mempool → confirmations, no explorer, no external API).
- **`.history`** connectivity evidence rotates at 5 MB × 20 (100 MB ceiling, tunable via
  `BANKON_HISTORY_MB` / `BANKON_HISTORY_KEEP`); **price collection is separate public storage**
  (`.pricehistory`).
- **Secure erase** uses coreutils [shred(1)](https://manpages.debian.org/testing/coreutils/shred.1.en.html):
  'care' toggle (default ON) = 7 overwrite passes + zero + unlink; wipe intensity **casual**
  (niced background, default) · **recommended (93%)** · **immediate (100% CPU)**.
- **Exit is fast**: BANKON recommends wiping the public `.history` before leaving (public history is
  still public), clears the RPC cache completely, and scrubs any key/signature material from memory.
