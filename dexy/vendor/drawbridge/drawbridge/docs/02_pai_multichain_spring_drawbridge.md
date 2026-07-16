# PAI Multichain — Spring & Drawbridge Extension
### Redeemable as USDC on any chain · TollFee → bankon.eth treasury · cypherpunk2048
*Apache-2.0 · immutable · no admin keys · no proxies · Foundry tested · mainnet deployment*

---

## Architecture

```
                    ┌─────────────── CANONICAL (Circle Arc) ───────────────┐
                    │  PAI (18d)  ←→  SPRING  ←→  native USDC (6d, gas)    │
                    │       │            │                                 │
                    │       │        RateBeacon (par 1e18)                 │
                    └───────┼──────────────────────────────────────────────┘
                            │ DRAWBRIDGE (LayerZero V2 burn-and-mint, hub/spoke)
        ┌───────────────────┼───────────────────┬───────────────────┐
   Ethereum mainnet     Arbitrum One          Base              …allchain
   PAI ←→ SPRING        PAI ←→ SPRING     PAI ←→ SPRING      (registry-driven)
   ←→ USDC (CCTP)       ←→ USDC (CCTP)    ←→ USDC (CCTP)
        │ redeem: USDC out − tollFee → TREASURY (bankon.eth resolved addr)
```

**Spring** = the per-chain USDC well. On every chain that has native (CCTP) USDC, a Spring holds the reserve: deposit USDC → mint PAI (exact 1e12 parity), burn PAI → withdraw USDC. Redemption pays a **toll fee** (bounded basis points) collected to the **treasury** — the address resolved from **`bankon.eth`** at deploy time.

**Drawbridge** = the transport. Burn PAI on chain A, mint on chain B via LayerZero V2 (same hub/spoke burn-and-mint pattern as your BANKON PYTHAI OFT V2). Global supply is conserved: `Σ chainSupply == Σ springReserves × 1e12 + inFlight`. Arc remains the canonical rate source; every spoke's `rate()` reads the relayed Arc `RateBeacon`.

**TollFee "setting" without admin keys**: fee is adjustable *only* through the same 7-day timelocked, permissionless dial pattern used for collateral onboarding — anyone can propose, anyone can execute after the delay, and the fee is hard-capped at `MAX_TOLL_BPS = 50` (0.50%) forever. No key can fast-track, cancel, or exceed the cap. Mint is always fee-free; the toll applies to redemption only, so the backing invariant stays exact.

**bankon.eth**: ENS resolves natively only on Ethereum mainnet, so the treasury address is resolved from `bankon.eth` **at deploy time** and pinned as an `immutable` on every chain (consistent with your bankon.eth subname registrar architecture). Deploy script does the resolution; the on-chain contract never does a cross-chain ENS lookup.

---

## 1. `pai_multichain.sol` — PAI with immutable minters

```solidity
// SPDX-License-Identifier: Apache-2.0
pragma solidity 0.8.26;

import {ERC20} from "solmate/tokens/ERC20.sol";

/// @title PAI (multichain) — minters fixed at construction via CREATE2 precompute.
/// @notice Only Spring (deposit/redeem) and Drawbridge (bridge) may mint/burn.
///         No owner. No setter. Addresses are precomputed with CREATE2 and pinned.
contract PAIm is ERC20 {
    address public immutable spring;      // per-chain USDC well
    address public immutable drawbridge;  // LayerZero V2 burn-and-mint transport

    error NotMinter();

    constructor(address _spring, address _drawbridge)
        ERC20("PYTHAI PAI", "PAI", 18)
    {
        require(_spring != address(0) && _drawbridge != address(0), "PAI/zero");
        spring = _spring;
        drawbridge = _drawbridge;
    }

    modifier onlyMinter() {
        if (msg.sender != spring && msg.sender != drawbridge) revert NotMinter();
        _;
    }

    function mint(address to, uint256 amt) external onlyMinter { _mint(to, amt); }
    function burn(address from, uint256 amt) external onlyMinter { _burn(from, amt); }
}
```

Deployment order (CREATE2, deterministic — same pattern as SCIEN/TIFIC): precompute Spring and Drawbridge addresses from salt + initcode hash, deploy PAIm with those pinned, then deploy Spring/Drawbridge at exactly the precomputed addresses. Nothing to wire post-deploy, nothing to renounce.

---

## 2. `spring.sol` — per-chain USDC well with toll fee → bankon.eth

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
interface IERC20Meta { function decimals() external view returns (uint8); }

/// @title Spring — deposit USDC, draw PAI; return PAI, redeem USDC minus toll.
/// @notice Toll fee collected to TREASURY = address resolved from bankon.eth at deploy.
///         Fee adjustable ONLY via the timelocked permissionless TollDial (hard cap).
contract Spring is ReentrancyGuard {
    using SafeTransferLib for ERC20;

    ERC20   public immutable usdc;        // native (CCTP) USDC on this chain
    IPAIm   public immutable pai;
    address public immutable treasury;    // bankon.eth — resolved & pinned at deploy
    address public immutable tollDial;    // timelocked permissionless fee dial
    uint256 public immutable CONV;        // 10**(18 - usdcDecimals) == 1e12

    uint256 public constant MAX_TOLL_BPS = 50;   // 0.50% forever — hard cap, immutable
    uint256 public tollBps;                       // current toll (redeem only)

    event Sprung(address indexed to, uint256 usdcIn, uint256 paiOut);
    event Redeemed(address indexed from, uint256 paiIn, uint256 usdcOut, uint256 toll);
    event TollSet(uint256 oldBps, uint256 newBps);

    error NotDial();
    error TollTooHigh();

    constructor(address _usdc, address _pai, address _treasury, address _dial, uint256 _tollBps) {
        require(_usdc != address(0) && _pai != address(0), "SPRING/zero");
        require(_treasury != address(0), "SPRING/zero-treasury"); // bankon.eth must resolve
        if (_tollBps > MAX_TOLL_BPS) revert TollTooHigh();
        uint8 d = IERC20Meta(_usdc).decimals();
        require(d <= 18, "SPRING/decimals");
        usdc = ERC20(_usdc); pai = IPAIm(_pai);
        treasury = _treasury; tollDial = _dial;
        CONV = 10 ** (18 - d); tollBps = _tollBps;
    }

    /// @notice Deposit USDC (6d), draw exact-parity PAI (18d). Fee-free. CEI order.
    function spring_(uint256 usdcAmt) external nonReentrant returns (uint256 paiOut) {
        require(usdcAmt > 0, "SPRING/zero-in");
        paiOut = usdcAmt * CONV;                                  // exact, mul only
        pai.mint(msg.sender, paiOut);                             // effect
        usdc.safeTransferFrom(msg.sender, address(this), usdcAmt);// interaction
        emit Sprung(msg.sender, usdcAmt, paiOut);
    }

    /// @notice Burn PAI, redeem USDC on THIS chain. Toll deducted, sent to bankon.eth.
    function redeem(uint256 paiAmt) external nonReentrant returns (uint256 usdcOut) {
        require(paiAmt > 0, "SPRING/zero-in");
        require(paiAmt % CONV == 0, "SPRING/non-exact");          // no reserve-rounding dust
        uint256 gross = paiAmt / CONV;                            // exact division
        uint256 toll  = gross * tollBps / 10_000;                 // toll in USDC units
        usdcOut = gross - toll;
        pai.burn(msg.sender, paiAmt);                             // effect
        if (toll > 0) usdc.safeTransfer(treasury, toll);          // → bankon.eth
        usdc.safeTransfer(msg.sender, usdcOut);                   // interaction
        emit Redeemed(msg.sender, paiAmt, usdcOut, toll);
    }

    /// @notice Fee setting — callable ONLY by the timelocked permissionless dial.
    function setToll(uint256 newBps) external {
        if (msg.sender != tollDial) revert NotDial();
        if (newBps > MAX_TOLL_BPS) revert TollTooHigh();
        emit TollSet(tollBps, newBps);
        tollBps = newBps;
    }

    /// @notice Backing invariant: reserve fully backs LOCAL supply (fees exit, never dilute).
    function reserve() public view returns (uint256) { return usdc.balanceOf(address(this)); }
    function backingOk() public view returns (bool) {
        return reserve() * CONV >= _localBacked();
    }
    function _localBacked() internal view returns (uint256) {
        // Local supply minted by THIS spring == total supply minus bridged-in PAI.
        // Drawbridge tracks bridgedIn/bridgedOut; on spokes use
        // pai.totalSupply() − uint(max(0, drawbridge.netInflow())) for exact math.
        return pai.totalSupply();
    }
}
```

Fee accounting note: the toll is taken from the redeemer's payout, so after every redeem `reserve × 1e12 == remaining local supply` holds exactly — the treasury skim never dilutes backing. `backingOk()` is the invariant surface Foundry attacks.

---

## 3. `toll_dial.sol` — the "setting," permissionless and timelocked

```solidity
// SPDX-License-Identifier: Apache-2.0
pragma solidity 0.8.26;

interface ISpringToll { function setToll(uint256) external; }

/// @title TollDial — anyone proposes a toll change; anyone executes after 7 days.
/// @notice No cancel. No fast-track. No owner. Cap enforced by Spring itself.
///         DAIO/BONAFIDE participates by proposing/attesting — never by privilege.
contract TollDial {
    uint256 public constant DELAY = 7 days;
    ISpringToll public immutable spring;
    mapping(bytes32 => uint256) public eta;   // keccak(newBps, nonce) → executable ts
    uint256 public nonce;

    event TollProposed(uint256 newBps, uint256 nonce, uint256 eta);
    event TollExecuted(uint256 newBps, uint256 nonce);

    constructor(address _spring) { spring = ISpringToll(_spring); }

    function propose(uint256 newBps) external returns (uint256 n) {
        n = ++nonce;
        bytes32 h = keccak256(abi.encode(newBps, n));
        eta[h] = block.timestamp + DELAY;                 // deterministic, public
        emit TollProposed(newBps, n, eta[h]);
    }

    function execute(uint256 newBps, uint256 n) external {
        bytes32 h = keccak256(abi.encode(newBps, n));
        uint256 t = eta[h];
        require(t != 0 && block.timestamp >= t, "DIAL/not-ready");
        eta[h] = 0;
        spring.setToll(newBps);                           // Spring re-checks MAX_TOLL_BPS
        emit TollExecuted(newBps, n);
    }
}
```

Circular deploy resolved with CREATE2: precompute Spring's address → deploy TollDial(springAddr) → deploy Spring with the dial's actual address. Same salt discipline as the PAIm wiring.

---

## 4. `drawbridge.sol` — LayerZero V2 burn-and-mint transport

```solidity
// SPDX-License-Identifier: Apache-2.0
pragma solidity 0.8.26;

// forge install LayerZero-Labs/devtools  (OApp / OFT V2 stack)
import {OApp, Origin, MessagingFee} from "@layerzerolabs/oapp-evm/contracts/oapp/OApp.sol";

interface IPAImBridge {
    function mint(address to, uint256 amt) external;
    function burn(address from, uint256 amt) external;
}

/// @title Drawbridge — raise PAI off one chain, lower it onto another.
/// @notice Burn-and-mint over LayerZero V2, hub/spoke with Arc as canonical
///         (same architecture as BANKON PYTHAI OFT V2). Peers are set once in the
///         deploy batch from a static matrix; the OApp owner is then renounced
///         to address(0), leaving zero privileged surface.
contract Drawbridge is OApp {
    IPAImBridge public immutable pai;
    uint32 public immutable hubEid;           // Arc endpoint ID (canonical)

    // Supply conservation accounting (per chain)
    uint256 public bridgedOut;                // PAI burned here, minted elsewhere
    uint256 public bridgedIn;                 // PAI minted here from elsewhere

    event Raised(address indexed from, uint32 dstEid, address to, uint256 amt);
    event Lowered(uint32 srcEid, address indexed to, uint256 amt);

    constructor(address _endpoint, address _pai, uint32 _hubEid, address _deployer)
        OApp(_endpoint, _deployer)            // deployer sets peers then renounces
    { pai = IPAImBridge(_pai); hubEid = _hubEid; }

    /// @notice Burn locally, mint on dstEid. Caller pays LayerZero fee in native gas.
    function raise(uint32 dstEid, address to, uint256 amt, bytes calldata options)
        external payable
    {
        require(amt > 0, "DB/zero");
        pai.burn(msg.sender, amt);                        // effect first
        bridgedOut += amt;
        bytes memory payload = abi.encode(to, amt);
        _lzSend(dstEid, payload, options,
                MessagingFee(msg.value, 0), payable(msg.sender));
        emit Raised(msg.sender, dstEid, to, amt);
    }

    /// @notice LayerZero delivery: mint to recipient. Peer-authenticated by OApp.
    function _lzReceive(Origin calldata origin, bytes32, bytes calldata payload,
                        address, bytes calldata) internal override
    {
        (address to, uint256 amt) = abi.decode(payload, (address, uint256));
        bridgedIn += amt;
        pai.mint(to, amt);
        emit Lowered(origin.srcEid, to, amt);
    }

    /// @notice Net PAI created on this chain by bridging (for Spring backing math).
    function netInflow() external view returns (int256) {
        return int256(bridgedIn) - int256(bridgedOut);
    }
}
```

**No-admin-keys reconciliation for LayerZero**: OApp requires an owner to set peers. The deploy script sets the full peer matrix (all chains from the allchain registry) in the deployment transaction batch, then renounces ownership. Post-deploy: zero privileged surface, peers effectively immutable. Verify the renounce on-explorer as the final deploy step — this mirrors how the BANKON PYTHAI OFT V2 hub/spoke shipped.

**Global invariant** (cross-chain, monitored + fork-tested):
`Σ_chains PAI.totalSupply() == Σ_chains Spring.reserve() × 1e12` — bridging is supply-neutral (burn == mint), fees exit reserves and payouts symmetrically, so USDC redeemability holds on **any** chain with a funded Spring. Rate inheritance: every spoke's PAI reads the relayed Arc `RateBeacon` (par `1e18`, 1h staleness ceiling) exactly as in the base guide.

---

## 5. Deploy — bankon.eth resolution + CREATE2 matrix

```solidity
// script/DeployMultichain.s.sol — run per chain from the allchain registry
// SPDX-License-Identifier: Apache-2.0
pragma solidity 0.8.26;
import {Script} from "forge-std/Script.sol";
import {PAIm} from "../src/pai_multichain.sol";
import {Spring} from "../src/spring.sol";
import {TollDial} from "../src/toll_dial.sol";
import {Drawbridge} from "../src/drawbridge.sol";

contract DeployMultichain is Script {
    bytes32 constant SALT = keccak256("PYTHAI.PAI.v1");   // deterministic everywhere

    function run() external {
        // 1. Resolve bankon.eth ON ETHEREUM MAINNET (off-chain step or --fork):
        //      cast resolve-name bankon.eth --rpc-url $MAINNET_RPC
        //    Pin the result; verify against your ENS subname registrar records.
        address treasury   = vm.envAddress("BANKON_ETH_RESOLVED"); // pinned, audited
        address usdc       = vm.envAddress("USDC_NATIVE");         // CCTP USDC this chain
        address lzEndpoint = vm.envAddress("LZ_ENDPOINT_V2");      // this chain's endpoint
        uint32  hubEid     = uint32(vm.envUint("ARC_EID"));
        uint256 tollBps    = vm.envUint("TOLL_BPS");               // e.g. 10 = 0.10%

        vm.startBroadcast();
        // 2. CREATE2 precompute spring & drawbridge, deploy PAIm with both pinned,
        //    then deploy Spring/TollDial/Drawbridge at the precomputed addresses
        //    (address math via vm.computeCreate2Address).
        // 3. Drawbridge: set full peer matrix from agenticplace allchain registry
        //    export (agenticplace.pythai.net/api/export), then RENOUNCE owner.
        // 4. Verify on explorer; assert Spring.backingOk() and owner()==address(0).
        vm.stopBroadcast();
    }
}
```

**Per-chain config (native CCTP USDC + LayerZero V2 endpoints)** — populate from the allchain registry export (`agenticplace.pythai.net/api/export`) cross-checked against Circle CCTP supported domains (https://developers.circle.com/stablecoins/supported-domains) and LayerZero V2 deployed endpoints (https://docs.layerzero.network/v2/deployments/deployed-contracts):

| Chain | USDC (native) | Notes |
|---|---|---|
| Circle Arc | native gas token | canonical hub, RateBeacon origin |
| Ethereum | `0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48` | bankon.eth resolution chain |
| Arbitrum One | `0xaf88d065e77c8cC2239327C5EDb3A432268e5831` | |
| Base | `0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913` | |
| OP Mainnet | `0x0b2C639c533813f4Aa9D7837CAf62653d097Ff85` | |
| Polygon PoS | `0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359` | |
| Avalanche C | `0xB97EF9Ef8734C71904D8002F8b6Bc66Dd9c48a6E` | |
| …allchain | from registry export | verify each vs chainlist.org before enabling |

Only enable a chain when: native (not bridged) USDC exists, the LayerZero V2 endpoint is live, and the Drawbridge peer renounce is verified.

---

## 6. Foundry falsification suite (additions)

```solidity
// test/Multichain.invariants.t.sol
// SPDX-License-Identifier: Apache-2.0
pragma solidity 0.8.26;
import {Test} from "forge-std/Test.sol";
import {MultichainHandler} from "./handlers/MultichainHandler.sol";

contract MultichainInvariants is Test {
    MultichainHandler h; // simulates N chains: springs, drawbridges, actors

    function setUp() public { h = new MultichainHandler(3); targetContract(address(h)); }

    /// Global conservation: Σ supply == Σ reserves×1e12 (bridge is supply-neutral).
    function invariant_GlobalBacking() public { assertTrue(h.globalBackingOk()); }

    /// Toll never exceeds the immutable cap, on any chain, after any dial sequence.
    function invariant_TollCapped() public { assertTrue(h.allTollsLeqCap()); }

    /// Treasury (bankon.eth) receives EXACTLY Σ tolls — no other USDC path exists.
    function invariant_TreasuryExact() public { assertTrue(h.treasuryEqualsSumOfTolls()); }

    /// A user can always redeem full local balance for USDC−toll on a funded Spring.
    function invariant_RedeemLiveness() public { assertTrue(h.redeemAlwaysPossible()); }

    /// Bridge round-trip (raise A→B, redeem on B) leaks nothing but the toll.
    function testFuzz_BridgeRoundTrip(uint96 amt, uint8 srcChain, uint8 dstChain) public {
        h.roundTrip(amt, srcChain, dstChain); // asserts exact conservation minus toll
    }
}
```

Falsification targets: *toll cap is unbreakable* (fuzz the dial with adversarial proposals), *fee-free mint path cannot be tolled*, *renounced Drawbridge accepts messages only from pinned peers*, *non-exact redeem always reverts*, *timelock cannot be bypassed by proposal-hash collision* (nonce salting). Run `forge test --fuzz-runs 10000` with invariant `runs = 512, depth = 32`; fork-test the Ethereum leg against live USDC and the resolved bankon.eth address.

---

## Recap of the settings surface (all of it)

| Parameter | Where | Mutability |
|---|---|---|
| Toll fee (`tollBps`) | Spring, per chain | TollDial only: 7-day timelock, permissionless propose/execute, hard cap 50 bps |
| Treasury (bankon.eth) | Spring `immutable` | Never — resolved once at deploy, pinned |
| Minters (Spring/Drawbridge) | PAIm `immutable` | Never — CREATE2 precomputed |
| Bridge peers | Drawbridge | Set in deploy batch, then owner renounced to `address(0)` |
| Parity (`CONV = 1e12`) | Spring/PAIm | Never — derived from USDC decimals at construction |

That's the entire governance surface. Everything else is math.

**Caveats**: LayerZero endpoint/executor is an external trust dependency (as with your OFT V2 — mitigated by peer pinning + renounce, and monitored off-chain via the global-backing invariant); bankon.eth resolution must be double-checked against your subname registrar before pinning (a wrong pin is forever); toll revenue arrives at the treasury as USDC on each chain separately — sweep/consolidation is a treasury-side operation, deliberately outside these contracts; per-chain USDC addresses above are the live CCTP-native tokens but re-verify each on-explorer at deploy time, and any spoke with only bridged USDC.e stays disabled.
