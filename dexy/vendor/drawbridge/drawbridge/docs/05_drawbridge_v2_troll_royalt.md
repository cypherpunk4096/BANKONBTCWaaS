# DRAWBRIDGE v2 — Troll Module · RoyalT Module · Complete Closure
### The tollkeeper guards the crossing, the crown takes its royalty, and the bridge never stays open
*Apache-2.0 · cypherpunk2048 · Foundry · mainnet-only · immutable · no admin keys*

---

## Module topology

```
                         ┌──────────────────────────────┐
      crossing request → │  DRAWBRIDGE v2 (transport)   │ → LayerZero V2
                         │  state machine: OPEN →       │
                         │  CONFIRMED → CLOSED          │
                         └──────┬──────────────┬────────┘
                                │              │
                     golden toll│              │royal share (1−1/φ of surcharge)
                                ▼              ▼
                         ┌────────────┐  ┌────────────┐
                         │   TROLL    │  │   RoyalT   │
                         │ tollkeeper │  │  royalty   │
                         │ moat (1/φ) │  │ → bankon.eth│
                         └────────────┘  └────────────┘
```

Three contracts, three duties:

| Module | Duty | Salt |
|---|---|---|
| **Drawbridge v2** | transport + two-phase closure state machine | `bankon.eth/drawbridge/v2` (state machine change → mandatory version bump per convention §Field 3) |
| **Troll** | guardian & tollkeeper: computes the golden toll on every contract call's chain fee, keeps the moat | `pythai.net/tollkeeper/v1` |
| **RoyalT** | royalty module: accrues the crown's share, renders to bankon.eth; ERC-2981-compatible for reuse | `bankon.eth/royalt/v1` |

---

## 1. The toll (restated exactly)

The toll is the golden ratio applied **exactly one decimal position to the right** of the chain's contract-call fee, carried to the 18th decimal:

```
callFee = gasUsed × tx.gasprice          (the contract call's fee on whatever chain)
toll    = callFee × 1.161803398874989485 (1 + φ/10, WAD-exact)

callFee 0.001000000000000000  →  toll 0.001161803398874989
```

Surcharge split (φ on φ, self-similar):

```
surcharge = toll − callFee = callFee × φ/10
├── 1/φ   = 61.8033988749894848 %  → TROLL moat   (overcollateralization + closure fund)
└── 1−1/φ = 38.1966011250105152 %  → RoyalT       (royalty accrual → bankon.eth)
```

---

## 2. `troll.sol` — tollkeeper as a Drawbridge module

Troll is now a standalone module the Drawbridge calls at every gate. One design change from v1, made explicit: **the moat gains exactly one purpose-locked outflow — funding confirmation messages** — because complete closure (§4) requires the destination chain to send an ACK back, and someone must pay that return message. The golden surcharge literally finances the guarantee that the bridge closes. The moat remains unwithdrawable by any person for any other purpose.

```solidity
// SPDX-License-Identifier: Apache-2.0
pragma solidity 0.8.26;

import {SafeTransferLib} from "solmate/utils/SafeTransferLib.sol";

/// @title Troll — guardian and tollkeeper of the bridge.
/// @notice Collects the golden toll (callFee × 1.161803398874989485) in native gas.
///         Moat share (1/φ of surcharge + raw callFee) stays as overcollateralization;
///         royal share (1−1/φ) flows to RoyalT. The moat's ONLY outflow is paying
///         LayerZero confirmation (ACK) fees so every crossing closes completely.
contract Troll {
    uint256 public constant GOLDEN_TOLL_WAD = 1_161803398874989485; // 1 + φ/10
    uint256 public constant PHI_INV_WAD     =   618033988749894848; // 1/φ
    uint256 public constant WAD             = 1e18;

    address public immutable drawbridge;   // sole caller (CREATE2-precomputed pairing)
    address payable public immutable royalT;

    uint256 public moat;

    event TollTaken(address indexed payer, uint256 callFee, uint256 toll,
                    uint256 toMoat, uint256 toRoyalT);
    event AckFunded(bytes32 indexed crossingId, uint256 fee);

    error NotDrawbridge();
    error TollUnpaid();

    constructor(address _drawbridge, address payable _royalT) {
        drawbridge = _drawbridge; royalT = _royalT;
    }

    modifier onlyBridge() { if (msg.sender != drawbridge) revert NotDrawbridge(); _; }

    /// @notice Called by Drawbridge with the measured call fee. msg.value prepays;
    ///         Troll splits and returns the exact refund amount for the bridge to remit.
    function collect(address payer, uint256 callFee) external payable onlyBridge
        returns (uint256 refund)
    {
        uint256 toll = callFee * GOLDEN_TOLL_WAD / WAD;   // …989485-exact
        if (msg.value < toll) revert TollUnpaid();
        uint256 surcharge = toll - callFee;
        uint256 toMoat    = surcharge * PHI_INV_WAD / WAD;
        uint256 toRoyal   = surcharge - toMoat;           // conservation: no dust lost
        moat += callFee + toMoat;
        SafeTransferLib.safeTransferETH(royalT, toRoyal);
        refund = msg.value - toll;
        emit TollTaken(payer, callFee, toll, callFee + toMoat, toRoyal);
    }

    /// @notice Purpose-locked outflow: pay the LayerZero fee for a CONFIRM (ACK)
    ///         message. Callable only by the Drawbridge, only up to the quoted fee.
    function fundAck(bytes32 crossingId, uint256 fee) external onlyBridge {
        require(fee <= moat, "TROLL/moat-dry");
        moat -= fee;
        SafeTransferLib.safeTransferETH(payable(drawbridge), fee);
        emit AckFunded(crossingId, fee);
    }

    receive() external payable { moat += msg.value; }  // donations deepen the moat
}
```

---

## 3. `royalt.sol` — the royalty module

RoyalT accrues the crown's share and renders it to the bankon.eth-resolved treasury on a permissionless sweep. It also exposes an **ERC-2981** (`royaltyInfo`) view with a golden royalty rate — φ shifted two positions to a percentage: **1.6180339887498948 %** — so the same module drops into any AgenticPlace/NFT/marketplace context that speaks the royalty standard (https://eips.ethereum.org/EIPS/eip-2981).

```solidity
// SPDX-License-Identifier: Apache-2.0
pragma solidity 0.8.26;

import {SafeTransferLib} from "solmate/utils/SafeTransferLib.sol";

/// @title RoyalT — golden royalty module for the Drawbridge (and anything ERC-2981).
/// @notice Receives the royal share of every toll; render() sweeps to the crown
///         (bankon.eth-resolved treasury). No admin, no pause, no other path.
contract RoyalT {
    address public constant CROWN = 0x0000000000000000000000000000000000000000; // ← pin bankon.eth resolution pre-compile (constant keeps initcode identical)
    uint256 public constant GOLDEN_ROYALTY_WAD = 16180339887498948; // 1.6180339887498948% of unit

    uint256 public accrued;
    uint256 public renderedTotal;

    event Tribute(address indexed source, uint256 amount);
    event Rendered(address indexed caller, uint256 amount);

    receive() external payable { accrued += msg.value; emit Tribute(msg.sender, msg.value); }

    /// @notice Permissionless: anyone renders unto the crown what is the crown's.
    function render() external {
        uint256 a = accrued;
        require(a > 0, "ROYALT/empty");
        accrued = 0;
        renderedTotal += a;
        SafeTransferLib.safeTransferETH(payable(CROWN), a);
        emit Rendered(msg.sender, a);
    }

    /// @notice ERC-2981 view: golden royalty on any sale price, receiver = crown.
    function royaltyInfo(uint256, uint256 salePrice)
        external pure returns (address receiver, uint256 royaltyAmount)
    {
        receiver = CROWN;
        royaltyAmount = salePrice * GOLDEN_ROYALTY_WAD / 1e18;
    }
}
```

---

## 4. `drawbridge.sol` v2 — the closure state machine

The requirement: a **db trigger from confirmation** ensuring the drawbridge **closes, and closes completely**. A crossing is not finished when the message is sent — it is finished when the source chain has *proof* the destination minted, and the pending record is destroyed. Three states, one arrow forward, one emergency arrow back:

```
            raise()                    ACK (CONFIRM msg)
  NONE ───────────────→ OPEN ───────────────────────────→ CONFIRMED ─┐
                          │                                          │ _close()
                          │ RECLAIM_DELAY elapsed, no ACK            ▼ (same tx)
                          └──────────── slam() ────────→ refund    CLOSED
                                                                 (record deleted,
                                                                  inFlight settled)
```

```solidity
// SPDX-License-Identifier: Apache-2.0
pragma solidity 0.8.26;

import {OApp, Origin, MessagingFee, MessagingReceipt}
    from "@layerzerolabs/oapp-evm/contracts/oapp/OApp.sol";

interface IPAImBridge {
    function mint(address to, uint256 amt) external;
    function burn(address from, uint256 amt) external;
}
interface ITroll {
    function collect(address payer, uint256 callFee) external payable returns (uint256);
    function fundAck(bytes32 crossingId, uint256 fee) external;
}

/// @title Drawbridge v2 — burn-and-mint transport with two-phase complete closure.
/// @notice Every crossing: OPEN (burn+send) → CONFIRMED (ACK received) → CLOSED
///         (record deleted, in-flight settled, gate down). Unconfirmed crossings
///         are reclaimable by anyone after RECLAIM_DELAY; destinations refuse
///         stale transits so reclaim can never double-mint.
contract DrawbridgeV2 is OApp {
    uint8 constant MSG_TRANSIT = 1;
    uint8 constant MSG_CONFIRM = 2;   // the db trigger: ACK from destination
    uint8 constant MSG_BOUNCE  = 3;   // destination refused a stale transit

    enum Gate { NONE, OPEN, CONFIRMED }
    struct Crossing {
        address from; address to;
        uint32 dstEid; uint128 amt; uint64 openedAt; Gate gate;
    }

    IPAImBridge public immutable pai;
    ITroll      public immutable troll;
    uint256 public constant RECLAIM_DELAY = 30 days;

    mapping(bytes32 => Crossing) public crossings;   // pending only; closed = deleted
    uint256 public inFlight;                          // PAI burned here, not yet closed
    uint256 public nonce;

    event GateOpened(bytes32 indexed id, address from, uint32 dstEid, address to, uint256 amt);
    event GateConfirmed(bytes32 indexed id);
    event GateClosed(bytes32 indexed id, uint256 inFlightAfter);   // closes COMPLETELY
    event Slammed(bytes32 indexed id, address to, uint256 amt);
    event Bounced(bytes32 indexed id);
    event Lowered(uint32 srcEid, address indexed to, uint256 amt);

    constructor(address _endpoint, address _pai, address _troll, address _deployer)
        OApp(_endpoint, _deployer)   // deployer sets peer matrix, then renounces
    { pai = IPAImBridge(_pai); troll = ITroll(_troll); }

    // ── OPEN ─────────────────────────────────────────────────────────────────
    function raise(uint32 dstEid, address to, uint128 amt, bytes calldata options)
        external payable returns (bytes32 id)
    {
        uint256 g0 = gasleft();
        require(amt > 0, "DB/zero");
        id = keccak256(abi.encode(block.chainid, address(this), ++nonce, msg.sender, to, amt));

        pai.burn(msg.sender, amt);                       // effect first
        crossings[id] = Crossing(msg.sender, to, dstEid, amt, uint64(block.timestamp), Gate.OPEN);
        inFlight += amt;

        bytes memory payload = abi.encode(MSG_TRANSIT, id, to, amt, uint64(block.timestamp));
        MessagingReceipt memory r = _lzSend(dstEid, payload, options,
            MessagingFee(_lzFee(dstEid, payload, options), 0), payable(address(this)));

        // golden toll on this call's chain fee, via the tollkeeper
        uint256 callFee = (g0 - gasleft() + 42_000) * tx.gasprice;
        uint256 lzFee   = r.fee.nativeFee;
        uint256 refund  = troll.collect{value: msg.value - lzFee}(msg.sender, callFee);
        if (refund > 0) SafeTransferLib.safeTransferETH(payable(msg.sender), refund);

        emit GateOpened(id, msg.sender, dstEid, to, amt);
    }

    // ── DESTINATION: mint, then trigger the confirmation ─────────────────────
    function _lzReceive(Origin calldata origin, bytes32, bytes calldata payload,
                        address, bytes calldata) internal override
    {
        uint8 kind = abi.decode(payload, (uint8));

        if (kind == MSG_TRANSIT) {
            (, bytes32 id, address to, uint128 amt, uint64 openedAt) =
                abi.decode(payload, (uint8, bytes32, address, uint128, uint64));

            // staleness gate: a transit older than RECLAIM_DELAY is refused —
            // this is what makes slam() on the source safe (no double-mint race).
            if (block.timestamp > openedAt + RECLAIM_DELAY) {
                _ackBack(origin.srcEid, MSG_BOUNCE, id);
                emit Bounced(id);
                return;
            }
            pai.mint(to, amt);
            emit Lowered(origin.srcEid, to, amt);
            _ackBack(origin.srcEid, MSG_CONFIRM, id);     // ← the db trigger

        } else if (kind == MSG_CONFIRM) {
            (, bytes32 id) = abi.decode(payload, (uint8, bytes32));
            Crossing storage c = crossings[id];
            require(c.gate == Gate.OPEN, "DB/not-open");
            c.gate = Gate.CONFIRMED;
            emit GateConfirmed(id);
            _close(id);                                   // confirm ⇒ close, same tx

        } else if (kind == MSG_BOUNCE) {
            (, bytes32 id) = abi.decode(payload, (uint8, bytes32));
            Crossing memory c = crossings[id];
            if (c.gate == Gate.OPEN) {                    // refund the stale transit
                pai.mint(c.from, c.amt);
                inFlight -= c.amt;
                delete crossings[id];
                emit Slammed(id, c.from, c.amt);
            }
        }
    }

    // ── CLOSED — completely ───────────────────────────────────────────────────
    /// @dev Complete closure: record deleted (not flagged — DELETED), in-flight
    ///      settled, gate-down event emitted. Nothing pending survives closure.
    function _close(bytes32 id) internal {
        Crossing memory c = crossings[id];
        require(c.gate == Gate.CONFIRMED, "DB/not-confirmed");
        inFlight -= c.amt;
        delete crossings[id];                             // closes COMPLETELY
        emit GateClosed(id, inFlight);
    }

    // ── failsafe: no crossing may hang open forever ───────────────────────────
    /// @notice Permissionless. After RECLAIM_DELAY with no confirmation, anyone
    ///         slams the gate: sender refunded, record destroyed. Safe because the
    ///         destination refuses (bounces) transits older than the same delay.
    function slam(bytes32 id) external {
        Crossing memory c = crossings[id];
        require(c.gate == Gate.OPEN, "DB/not-open");
        require(block.timestamp > c.openedAt + RECLAIM_DELAY, "DB/too-soon");
        pai.mint(c.from, c.amt);
        inFlight -= c.amt;
        delete crossings[id];
        emit Slammed(id, c.from, c.amt);
    }

    // ── ACK plumbing: return messages paid from the tollkeeper's moat ─────────
    function _ackBack(uint32 dstEid, uint8 kind, bytes32 id) internal {
        bytes memory payload = abi.encode(kind, id);
        bytes memory options = _defaultAckOptions();
        uint256 fee = _lzFee(dstEid, payload, options);
        troll.fundAck(id, fee);                           // moat's only outflow
        _lzSend(dstEid, payload, options, MessagingFee(fee, 0), payable(address(this)));
    }

    function _lzFee(uint32 eid, bytes memory p, bytes memory o) internal view returns (uint256) {
        return _quote(eid, p, o, false).nativeFee;
    }
    function _defaultAckOptions() internal pure returns (bytes memory) {
        return hex"0003010011010000000000000000000000000000c350"; // 50k gas executor opt
    }
    receive() external payable {}                          // LZ refunds + moat ACK funding
}
```

---

## 5. Foundry falsification — closure invariants

```solidity
// test/DrawbridgeV2.invariants.t.sol
// SPDX-License-Identifier: Apache-2.0
pragma solidity 0.8.26;
import {Test} from "forge-std/Test.sol";
import {BridgeHandler} from "./handlers/BridgeHandler.sol";

contract ClosureInvariants is Test {
    BridgeHandler h; // simulates src+dst chains, LZ delivery, delays, adversaries

    function setUp() public { h = new BridgeHandler(); targetContract(address(h)); }

    /// COMPLETE CLOSURE: every crossing terminates — confirmed+closed, bounced, or
    /// slammed. After the handler drains all queues + warps past RECLAIM_DELAY,
    /// zero OPEN records survive and inFlight == 0.
    function invariant_BridgeAlwaysCloses() public { assertTrue(h.noEternalOpenGates()); }

    /// No double-mint: for every id, (dst minted) XOR (src refunded), never both.
    /// The staleness bounce makes slam() race-free.
    function invariant_ExactlyOnce() public { assertTrue(h.exactlyOnceDelivery()); }

    /// Supply conservation including flight: Σ supply + Σ inFlight is constant
    /// across any interleaving of raise/deliver/confirm/bounce/slam.
    function invariant_ConservationWithFlight() public { assertTrue(h.conserved()); }

    /// Moat outflow ≤ Σ quoted ACK fees; every other moat path is inflow-only.
    function invariant_MoatPurposeLocked() public { assertTrue(h.moatOnlyPaysAcks()); }

    /// RoyalT: accrued + renderedTotal == Σ royal shares; render() reaches CROWN exactly.
    function invariant_RoyalExact() public { assertTrue(h.royaltyConserved()); }

    /// Golden toll to the 18th decimal on every raise (0.001 → 0.001161803398874989).
    function testFuzz_GoldenToll(uint128 callFee) public { h.assertGolden(callFee); }
}
```

Falsification targets: *an adversary who censors ACKs can delay closure but never prevent it* (slam path), *a replayed CONFIRM cannot close twice* (state check + delete), *a bounced transit and a slam on the same id cannot both refund* (both branches require `gate == OPEN` and both delete), *ACK funding cannot drain the moat below zero or be redirected* (onlyBridge + fee-capped), *toll conservation loses zero wei across the φ split*.

---

## 6. Salt registry additions

| salt preimage | contract | status |
|---|---|---|
| `bankon.eth/drawbridge/v2` | DrawbridgeV2 (closure state machine) | reserved — v1 retired on v2 go-live |
| `pythai.net/tollkeeper/v1` | Troll module | reserved |
| `bankon.eth/royalt/v1` | RoyalT module | reserved |

Deploy order (CREATE2, all precomputed, zero post-deploy wiring): RoyalT → Troll(drawbridgePredicted, royalT) → DrawbridgeV2(endpoint, pai, troll, deployer) → peer matrix from the allchain export → renounce → assert same address on every chain.

---

**Caveats, honestly stated**: (1) the moat is no longer strictly monotonic — it has exactly one purpose-locked outflow (ACK fees); if ACK costs ever exceed moat inflow on a low-traffic chain, confirmations stall until the moat is topped up via `receive()` — monitor moat depth per chain through the mindX accounting route and publish to rage.pythai.net. (2) `MessagingReceipt`/`_quote` usage must be validated against the exact LayerZero V2 OApp version you pin — fork-test the fee quoting on every target chain before mainnet. (3) `RECLAIM_DELAY = 30 days` is the safety wall between slam and bounce; both sides share the same constant compiled into identical bytecode, which is what makes the race mathematically closed — never change one side without a full v3. (4) The hardcoded ACK executor option (50k gas) needs per-chain calibration in fork tests. (5) RoyalT's `CROWN` constant is the bankon.eth resolution pinned pre-compile — same eternal-address discipline as before: resolve twice, then freeze.
