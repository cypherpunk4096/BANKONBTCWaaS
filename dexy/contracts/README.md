# BANKON toll contracts — the golden-ratio tollkeeper

The on-chain toll leg for **BANKON bridge services and any contract facilitation** (including
minters and minter factories). One rule, applied everywhere:

> **BANKON toll = the golden ratio of the transaction's own gas fee, to 18-decimal precision,
> charged on top of gas and held in the BANKON treasury (bankon.eth).**

```
toll = gasFee × φ/10
     = gasFee × 1.618033988749894848 / 10        (φ = golden ratio, exact to 18 dp)
     = gasFee × 1_618_033_988_749_894_848 / 1e19
```

Worked example (the spec): a **0.0001 ETH** gas fee tolls
`0.0001 × 0.1618033988749894848 = 0.000016180339887498 ETH` (`16180339887498` wei) → treasury.

The toll is **native** value, **pegged to gas**, **added on top of** the gas the network already
charges. It never touches the asset being moved and never touches a private key.

## Contracts

| File | What it is |
|------|-----------|
| `BankonToll.sol` | Abstract tollkeeper base. `PHI_E18`/`PHI_DIV` constants, `tollOnGasFee()` (pure), `previewToll()` (view), the `tolled` modifier, and `_collectToll()` (forwards toll → treasury, refunds excess). Immutable `bankonTreasury` + `gasOverhead`. Inherit it to make any action tolled. |
| `BankonFacilitator.sol` | Escrow that **holds the client's asset** (native or ERC-20) through a transfer and tolls the treasury. `facilitateNative(to, amount)` / `facilitateToken(token, to, amount)`. `nonReentrant`. |
| `BankonMinter.sol` | Example ERC-20 minter — `mint()` is `tolled`. Plus `BankonMinterFactory` whose `deployMinter()` is itself `tolled`, so both the factory action and each minted token carry the toll. |

## Adopt the toll in your own contract

```solidity
import { BankonToll } from "./BankonToll.sol";

contract MyBridge is BankonToll {
    constructor(address payable treasury, uint256 overhead) BankonToll(treasury, overhead) {}

    function bridgeOut(...) external payable tolled {   // ← one word
        // ... your facilitation; the golden toll is taken on top of gas, sent to bankon.eth
    }
}
```

Off-chain, mirror the math with `dexy/facilitator.mjs` or quote it live at
`GET /api/dexy/facilitator/quote?gasFeeWei=…` (or `?gasUnits=&gasPriceWei=`).

## Build & test

```bash
cd dexy/contracts && forge test -vv        # self-contained, no forge-std / no external deps
```

## Deploy notes

- `bankonTreasury` — set to the resolved address of **bankon.eth** at construction; immutable
  thereafter (no key can redirect the toll).
- `gasOverhead` — a fixed gas amount added to the metered in-call gas so the toll reflects the
  whole gas transaction (intrinsic 21000 + calldata + the toll transfer). Calibrate per chain;
  the value only scales the toll, never the moved asset.
- Solidity 0.8.26, Apache-2.0, no proxy / no admin key post-deploy (cypherpunk2048 standard).

© 2026 ₿ANKON — all rights preserved. Salt namespace: `bankon.eth/toll/v1`.
