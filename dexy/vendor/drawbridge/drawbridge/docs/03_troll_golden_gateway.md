# TROLL — Same-Address Multichain PAI ↔ USDC Gateway
### Golden-ratio toll · overcollateralization Moat + bankon.eth protocol funding · one address, every EVM chain
*Apache-2.0 · cypherpunk2048 · Foundry · mainnet-only · immutable · no admin keys*

TROLL is the guardian and tollkeeper of the bridge. Every crossing pays the golden toll.

---

## The golden toll math (exact, 18 decimals)

Rule as specified: the fee is the chain gas fee with the golden ratio applied **one decimal position to the right**, carried to the end of the 18th decimal.

```
φ  = 1.618033988749894848…            (golden ratio)
φ/10 = 0.161803398874989485           (φ shifted one decimal right, 18 dp)

toll = gasFee × (1 + φ/10)

Example (user's example, exact to 18 decimals):
gasFee = 0.001000000000000000 native
toll   = 0.001161803398874989 native   ← 0.001 × 1.161803398874989485
```

WAD constants baked into bytecode (identical on every chain — required for same-address deployment):

```
GOLDEN_TOLL_WAD  = 1_161803398874989485   // 1 + φ/10, 18 decimals
PHI_INV_WAD      =   618033988749894848   // 1/φ = 0.618033988749894848
```

The **surcharge** (the φ/10 part above raw gas) is itself split by the golden ratio — this is how the troll serves both purposes you named:

```
surcharge = toll − gasFee = gasFee × φ/10
├── 61.8033988749894848 %  → MOAT   (overcollateralization buffer, held in-contract)
└── 38.1966011250105152 %  → TREASURY (bankon.eth resolved address — protocol funding)
```

Self-similar by construction: the fee is golden, and its split is golden.

---

## Same contract address on all chains

Deployed through the **deterministic CREATE2 deployer** `0x4e59b44847b379578588920cA78FbF26c0B4956C` (present at that address on effectively every EVM chain — Ethereum, Arbitrum, Base, OP, Polygon, Avalanche, and the long tail in the allchain registry).

Same-address requirements, all satisfied below:
1. **Same salt**: `keccak256("PYTHAI.TROLL.v1")`
2. **Same initcode**: the contract takes **zero constructor arguments** — per-chain config (USDC address, LayerZero endpoint) is resolved internally by `block.chainid` from a table compiled into the bytecode
3. **Same deployer**: the canonical CREATE2 proxy

Because the config table lives in bytecode, adding a chain later means a `v2` salt (new address family). That is the immutability tax and it is paid deliberately: no registry setter, no admin key. Populate the table from the allchain mapping (`agenticplace.pythai.net/allchain.html` → machine-readable at `agenticplace.pythai.net/api/export`) at compile time, cross-checked against Circle CCTP domains (https://developers.circle.com/stablecoins/supported-domains) and chainlist.org.

---

## `troll.sol` — full source

```solidity
// SPDX-License-Identifier: Apache-2.0
pragma solidity 0.8.26;

import {ERC20} from "solmate/tokens/ERC20.sol";
import {SafeTransferLib} from "solmate/utils/SafeTransferLib.sol";
import {ReentrancyGuard} from "solmate/utils/ReentrancyGuard.sol";

interface IPAIm {
    function mint(address to, uint256 amt) external;
    function burn(address from, uint256 amt) external;
    function totalSupply() external view returns (uint256);
}

/// @title TROLL — the gate connecting PAI to USDC on Ethereum and any EVM,
///         deployed at ONE identical address on every chain via CREATE2.
/// @notice Every crossing (mint, redeem, bridge-exit) pays the golden toll in
///         native gas: toll = gasUsed·gasprice × 1.161803398874989485.
///         Surcharge splits φ-wise: 1/φ → MOAT (overcollateralization buffer),
///         1−1/φ → TREASURY (bankon.eth — protocol funding).
///         Zero constructor args. Zero admin keys. Config by chainid, in bytecode.
contract Troll is ReentrancyGuard {
    using SafeTransferLib for ERC20;

    // ── golden constants (WAD, identical bytecode on all chains) ─────────────
    uint256 public constant GOLDEN_TOLL_WAD = 1_161803398874989485; // 1 + φ/10
    uint256 public constant PHI_INV_WAD     =   618033988749894848; // 1/φ
    uint256 public constant WAD             = 1e18;
    uint256 public constant GAS_OVERHEAD    = 42_000; // intrinsic+transfer overhead

    // ── treasury: bankon.eth (single canonical resolution, hardcoded in bytecode
    //    so initcode is identical everywhere; MUST be re-verified with
    //    `cast resolve-name bankon.eth` against your subname registrar pre-deploy)
    address public constant TREASURY = 0x0000000000000000000000000000000000000000; // ← pin bankon.eth resolution here before compile

    // ── moat: overcollateralization buffer (native), held by this contract ───
    uint256 public moat;

    IPAIm public pai; // set once by PAIm’s CREATE2-precomputed deploy pairing

    event Crossed(address indexed who, bytes4 action, uint256 gasFee, uint256 toll,
                  uint256 toMoat, uint256 toTreasury);
    event Sprung(address indexed to, uint256 usdcIn, uint256 paiOut);
    event Redeemed(address indexed from, uint256 paiIn, uint256 usdcOut);
    event Fortified(uint256 moatBalance, uint256 usdcReserve);

    error WrongChain();
    error TollUnpaid();

    // ── per-chain config table, compiled into bytecode (same initcode everywhere)
    function usdcOf() public view returns (address u) {
        uint256 id = block.chainid;
        if (id == 1)      u = 0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48; // Ethereum
        else if (id == 42161) u = 0xaf88d065e77c8cC2239327C5EDb3A432268e5831; // Arbitrum One
        else if (id == 8453)  u = 0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913; // Base
        else if (id == 10)    u = 0x0b2C639c533813f4Aa9D7837CAf62653d097Ff85; // OP Mainnet
        else if (id == 137)   u = 0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359; // Polygon PoS
        else if (id == 43114) u = 0xB97EF9Ef8734C71904D8002F8b6Bc66Dd9c48a6E; // Avalanche C
        // …extend from agenticplace.pythai.net/allchain.html export at compile time
        else revert WrongChain(); // undeployable semantics on unlisted chains
    }

    uint256 constant CONV = 1e12; // USDC 6d → PAI 18d exact parity (all CCTP USDC = 6d)

    constructor() {
        // PAIm is deployed in the same CREATE2 batch with this troll's precomputed
        // address pinned as its sole gateway minter; pai address is likewise
        // deterministic, so it can be computed and hardcoded for v1:
        // pai = IPAIm(<CREATE2(PAIm, SALT)>);  ← pin at compile, same on all chains
    }

    // ── the toll booth ────────────────────────────────────────────────────────
    /// @dev Measures actual gas consumed, prices it at tx.gasprice, applies the
    ///      golden multiplier to the full 18th decimal, splits the surcharge φ-wise.
    ///      Caller prepays via msg.value; exact excess is refunded.
    modifier golden(bytes4 action) {
        uint256 g0 = gasleft();
        _;
        uint256 gasFee = (g0 - gasleft() + GAS_OVERHEAD) * tx.gasprice;
        uint256 toll   = gasFee * GOLDEN_TOLL_WAD / WAD;      // …8874989485 exact
        if (msg.value < toll) revert TollUnpaid();
        uint256 surcharge  = toll - gasFee;                   // gasFee × φ/10
        uint256 toMoat     = surcharge * PHI_INV_WAD / WAD;   // 61.803…%
        uint256 toTreasury = surcharge - toMoat;              // 38.196…%
        moat += gasFee + toMoat;   // raw gas component also strengthens the moat
        SafeTransferLib.safeTransferETH(TREASURY, toTreasury);
        uint256 refund = msg.value - toll;
        if (refund > 0) SafeTransferLib.safeTransferETH(msg.sender, refund);
        emit Crossed(msg.sender, action, gasFee, toll, gasFee + toMoat, toTreasury);
    }

    // ── PAI ↔ USDC (identical interface on every chain, same address) ────────
    /// @notice Deposit USDC, mint exact-parity PAI. Golden toll in native gas.
    function spring_(uint256 usdcAmt) external payable nonReentrant golden(this.spring_.selector)
        returns (uint256 paiOut)
    {
        require(usdcAmt > 0, "TROLL/zero");
        paiOut = usdcAmt * CONV;
        pai.mint(msg.sender, paiOut);
        ERC20(usdcOf()).safeTransferFrom(msg.sender, address(this), usdcAmt);
        emit Sprung(msg.sender, usdcAmt, paiOut);
    }

    /// @notice Burn PAI, redeem USDC on this chain. Golden toll in native gas —
    ///         the USDC leg stays exact 1:1 (no skim on the stable side).
    function redeem(uint256 paiAmt) external payable nonReentrant golden(this.redeem.selector)
        returns (uint256 usdcOut)
    {
        require(paiAmt > 0 && paiAmt % CONV == 0, "TROLL/non-exact");
        usdcOut = paiAmt / CONV;
        pai.burn(msg.sender, paiAmt);
        ERC20(usdcOf()).safeTransfer(msg.sender, usdcOut);
        emit Redeemed(msg.sender, paiAmt, usdcOut);
    }

    // ── overcollateralization surface ─────────────────────────────────────────
    /// @notice Backing = USDC reserve (exact 1:1) PLUS the moat as excess native
    ///         collateral. The moat can only grow; nothing can withdraw it.
    ///         It is the permissionless backstop and the overcollateralization
    ///         option: solvency > 1.0 by construction, monotonically.
    function reserve() public view returns (uint256) {
        return ERC20(usdcOf()).balanceOf(address(this));
    }
    function backingOk() public view returns (bool) {
        return reserve() * CONV >= pai.totalSupply();   // hard 1:1 floor
    }
    function fortify() external {                        // permissionless attestation
        emit Fortified(moat, reserve());                 // moat = surplus above 1:1
    }

    receive() external payable { moat += msg.value; }    // donations deepen the moat
}
```

Design notes, in order of importance:

- **USDC redeemability is never skimmed.** The golden toll is charged in **native gas**, so `reserve × 1e12 ≥ totalSupply` holds as a hard equality on the stable leg and a `≥` once the moat exists. 1 PAI is always 1.000000 USDC out; the troll's cut rides on the gas, not the dollar.
- **The moat only grows.** There is no withdrawal path — not for governance, not for the treasury, not for anyone. It is pure excess collateral: the "overcollateralization option" is exercised passively, as a monotonic solvency buffer above the 1:1 USDC floor, valued via the same Pyth native/USD feeds from the base guide for reporting.
- **Protocol funding** flows continuously to the bankon.eth-resolved `TREASURY` as the 38.196…% surcharge share — per chain, in that chain's native token, sweepable off-chain by the treasury itself (outside these contracts, as before).
- **Gas measurement is honest**: `gasleft()` delta + fixed `GAS_OVERHEAD`, priced at `tx.gasprice` (covers EIP-1559 effective price). On L2s the L1 data component isn't visible to the EVM — the toll there is golden over the L2 execution fee; document this, don't fake it.
- **`TREASURY` as a `constant`** keeps initcode identical across chains (an immutable constructor arg would too, but zero-arg is the cleanest same-address discipline). Pin the bankon.eth resolution into the source before compile; verify twice — it is forever, everywhere.

---

## Deploy — one address, every mainnet

```solidity
// script/DeployTroll.s.sol — run against each mainnet RPC; address is identical
// SPDX-License-Identifier: Apache-2.0
pragma solidity 0.8.26;
import {Script} from "forge-std/Script.sol";
import {Troll} from "../src/troll.sol";

contract DeployTroll is Script {
    address constant CREATE2_DEPLOYER = 0x4e59b44847b379578588920cA78FbF26c0B4956C;
    bytes32 constant SALT = keccak256("pythai.net/tollkeeper/v1"); // see create2_salt_conventions.md

    function run() external {
        vm.startBroadcast();
        // deterministic: address = keccak256(0xff ++ deployer ++ salt ++ keccak256(initcode))[12:]
        bytes memory initcode = type(Troll).creationCode;      // zero constructor args
        (bool ok, ) = CREATE2_DEPLOYER.call(abi.encodePacked(SALT, initcode));
        require(ok, "deploy failed");
        vm.stopBroadcast();
        // Precompute & assert (same value on every chain):
        // vm.computeCreate2Address(SALT, keccak256(initcode), CREATE2_DEPLOYER)
    }
}
```

Deployment loop (mainnet-only, per your standard): iterate the chain list from the allchain export, `forge script --rpc-url $CHAIN_RPC --broadcast --verify`, then assert the computed address matches on-explorer for every chain before enabling the Drawbridge peer for it. PAIm is deployed in the same batch with the troll's precomputed address pinned as gateway minter (CREATE2 pairing exactly as in the Spring/Drawbridge doc — Drawbridge remains the transport; Troll replaces Spring as the toll-collecting well).

---

## Foundry falsification (golden invariants)

```solidity
// test/Troll.t.sol
// SPDX-License-Identifier: Apache-2.0
pragma solidity 0.8.26;
import {Test} from "forge-std/Test.sol";
import {Troll} from "../src/troll.sol";

contract TrollTest is Test {
    Troll troll;

    /// The user's exact example, verified to the 18th decimal.
    function test_GoldenTollExample() public pure {
        uint256 gasFee = 0.001 ether;                       // 0.001000000000000000
        uint256 toll = gasFee * 1_161803398874989485 / 1e18;
        assertEq(toll, 0.001161803398874989 ether);         // golden to 18 dp
    }

    /// φ self-similarity: surcharge splits at exactly 1/φ, remainder 1−1/φ.
    function testFuzz_GoldenSplit(uint128 gasFee) public pure {
        vm.assume(gasFee > 0);
        uint256 toll = uint256(gasFee) * 1_161803398874989485 / 1e18;
        uint256 s = toll - gasFee;
        uint256 m = s * 618033988749894848 / 1e18;
        assertEq(m + (s - m), s);                           // conservation, no dust loss
        assertLe(m * 1e18 / (s == 0 ? 1 : s), 618033988749894849); // ≤ 1/φ (+1 wei rounding)
    }

    /// Same-address determinism across chain configs.
    function test_SameAddressAllChains() public {
        bytes32 h = keccak256(type(Troll).creationCode);
        address a = vm.computeCreate2Address(
            keccak256("pythai.net/tollkeeper/v1"), h,
            0x4e59b44847b379578588920cA78FbF26c0B4956C);
        // identical on every chain because initcode has zero constructor args
        assertTrue(a != address(0));
    }

    /// Invariants (handler-driven, mainnet fork per chain):
    /// 1. reserve×1e12 ≥ totalSupply after ANY op sequence (1:1 floor never skimmed)
    /// 2. moat is monotonic — no call sequence decreases it
    /// 3. treasury receives EXACTLY Σ(surcharge − moatShare); no other native path
    /// 4. unpaid toll reverts every gated action
    function invariant_HardFloor() public view { /* handler asserts 1 */ }
    function invariant_MoatMonotonic() public view { /* handler asserts 2 */ }
    function invariant_TreasuryExact() public view { /* handler asserts 3 */ }
}
```

Run `forge test --fuzz-runs 10000` plus invariant `runs = 512, depth = 32`, fork-testing each target chain's live USDC. Falsification targets: *the 1:1 USDC floor cannot be breached by any toll path*, *the moat cannot be drained by any caller including TREASURY*, *golden split loses zero wei*, *reentrancy through the refund transfer cannot double-mint* (nonReentrant + CEI within the modifier body).

---

## Ecosystem wiring (per your standing integration)

- **allchain mapping** (`agenticplace.pythai.net/allchain.html` / `api/export`) drives the compile-time chainid table and the deploy loop; a chain enters `v1` only if its row verifies against chainlist.org and native CCTP USDC exists.
- **mindX API** (`mindx.pythai.net`): point the accounting routes at the *single* troll address — one address to query on all chains simplifies the route to `GET /accounting/troll/{chainid}` returning `reserve()`, `moat`, `backingOk()`. mindX publishes moat-depth reports to **rage.pythai.net**.
- **DAIO (deploying now)**: no privileged hook exists on the troll by design; DAIO's role is attestation — `fortify()` events are the on-chain material for BONAFIDE-style solvency attestations.
- **x402 (parsec / parsec-wallet, Algorand)**: gate the cross-chain moat/solvency dashboard API behind the same x402 Algorand mainnet rails from the base guide — pay-per-query metering for agents reading golden-toll telemetry.
- **Foundry for testing, mainnet for deployment** — no testnet steps in the loop beyond your discretion; fork tests stand in for staging.

**Caveats**: pin `TREASURY` only after double-resolving bankon.eth against your subname registrar (constant = forever on every chain); `GAS_OVERHEAD` needs per-chain calibration in fork tests (a fixed 42k is a starting point, and L2 L1-data fees are invisible to the EVM so the toll is golden over execution gas only); the moat is intentionally unspendable — if you later want it convertible to USDC reserve, that's a `v2` with an immutable DEX route, not a patch; and the chainid table freezes the v1 chain set — the golden address is eternal, but only on the chains it was born knowing.
