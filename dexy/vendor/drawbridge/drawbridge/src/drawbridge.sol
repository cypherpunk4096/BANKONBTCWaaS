// SPDX-License-Identifier: Apache-2.0
pragma solidity 0.8.26;

import {OApp, Origin, MessagingFee, MessagingReceipt} from "@layerzerolabs/oapp-evm/contracts/oapp/OApp.sol";
import {SafeTransferLib} from "solmate/utils/SafeTransferLib.sol";

interface IPAImBridge {
    function mint(address to, uint256 amt) external;
    function burn(address from, uint256 amt) external;
}

interface ITroll {
    function collect(address payer, uint256 callFee) external payable returns (uint256);
    function fundAck(bytes32 crossingId, uint256 fee) external;
}

/// @title Drawbridge v2 — burn-and-mint transport with two-phase complete closure.
/// @notice Every crossing: OPEN (burn+send) -> CONFIRMED (ACK received) -> CLOSED
///         (record deleted, in-flight settled, gate down). Unconfirmed crossings are
///         reclaimable by anyone after RECLAIM_DELAY; destinations refuse stale transits
///         so reclaim can never double-mint. Salt: bankon.eth/drawbridge/v2.
/// @author Professor Codephreak — PYTHAI / BANKON
contract DrawbridgeV2 is OApp {
    uint8 constant MSG_TRANSIT = 1;
    uint8 constant MSG_CONFIRM = 2; // the db trigger: ACK from destination
    uint8 constant MSG_BOUNCE = 3; // destination refused a stale transit

    enum Gate {
        NONE,
        OPEN,
        CONFIRMED
    }

    struct Crossing {
        address from;
        address to;
        uint32 dstEid;
        uint128 amt;
        uint64 openedAt;
        Gate gate;
    }

    IPAImBridge public immutable pai;
    ITroll public immutable troll;
    uint256 public constant RECLAIM_DELAY = 30 days;

    mapping(bytes32 => Crossing) public crossings; // pending only; closed = deleted
    uint256 public inFlight; // PAI burned here, not yet closed
    uint256 public nonce;

    event GateOpened(bytes32 indexed id, address from, uint32 dstEid, address to, uint256 amt);
    event GateConfirmed(bytes32 indexed id);
    event GateClosed(bytes32 indexed id, uint256 inFlightAfter); // closes COMPLETELY
    event Slammed(bytes32 indexed id, address to, uint256 amt);
    event Bounced(bytes32 indexed id);
    event Lowered(uint32 srcEid, address indexed to, uint256 amt);

    constructor(address _endpoint, address _pai, address _troll, address _deployer)
        OApp(_endpoint, _deployer) // deployer sets peer matrix in the deploy batch, then renounces
    {
        pai = IPAImBridge(_pai);
        troll = ITroll(_troll);
    }

    // ── OPEN ────────────────────────────────────────────────────────────────
    function raise(uint32 dstEid, address to, uint128 amt, bytes calldata options)
        external
        payable
        returns (bytes32 id)
    {
        uint256 g0 = gasleft();
        require(amt > 0, "DB/zero");
        id = keccak256(abi.encode(block.chainid, address(this), ++nonce, msg.sender, to, amt));

        pai.burn(msg.sender, amt); // effect first
        crossings[id] = Crossing(msg.sender, to, dstEid, amt, uint64(block.timestamp), Gate.OPEN);
        inFlight += amt;

        bytes memory payload = abi.encode(MSG_TRANSIT, id, to, amt, uint64(block.timestamp));
        uint256 lzFee = _lzFee(dstEid, payload, options);
        _lzSend(dstEid, payload, options, MessagingFee(lzFee, 0), payable(address(this)));

        // golden toll on THIS call's chain fee, via the tollkeeper module
        uint256 callFee = (g0 - gasleft() + 42_000) * tx.gasprice;
        uint256 refund = troll.collect{value: msg.value - lzFee}(msg.sender, callFee);
        if (refund > 0) SafeTransferLib.safeTransferETH(payable(msg.sender), refund);

        emit GateOpened(id, msg.sender, dstEid, to, amt);
    }

    // ── DESTINATION: mint, then trigger the confirmation ─────────────────────
    function _lzReceive(Origin calldata origin, bytes32, bytes calldata payload, address, bytes calldata)
        internal
        override
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
            _ackBack(origin.srcEid, MSG_CONFIRM, id); // ← the db trigger
        } else if (kind == MSG_CONFIRM) {
            (, bytes32 id) = abi.decode(payload, (uint8, bytes32));
            Crossing storage c = crossings[id];
            require(c.gate == Gate.OPEN, "DB/not-open");
            c.gate = Gate.CONFIRMED;
            emit GateConfirmed(id);
            _close(id); // confirm ⇒ close, same tx
        } else if (kind == MSG_BOUNCE) {
            (, bytes32 id) = abi.decode(payload, (uint8, bytes32));
            Crossing memory c = crossings[id];
            if (c.gate == Gate.OPEN) {
                // refund the stale transit
                pai.mint(c.from, c.amt);
                inFlight -= c.amt;
                delete crossings[id];
                emit Slammed(id, c.from, c.amt);
            }
        }
    }

    // ── CLOSED — completely ──────────────────────────────────────────────────
    /// @dev Complete closure: record deleted (not flagged — DELETED), in-flight
    ///      settled, gate-down event emitted. Nothing pending survives closure.
    function _close(bytes32 id) internal {
        Crossing memory c = crossings[id];
        require(c.gate == Gate.CONFIRMED, "DB/not-confirmed");
        inFlight -= c.amt;
        delete crossings[id]; // closes COMPLETELY
        emit GateClosed(id, inFlight);
    }

    // ── failsafe: no crossing may hang open forever ──────────────────────────
    /// @notice Permissionless. After RECLAIM_DELAY with no confirmation, anyone slams
    ///         the gate: sender refunded, record destroyed. Safe because the destination
    ///         refuses (bounces) transits older than the same delay.
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
        troll.fundAck(id, fee); // moat's only outflow
        _lzSend(dstEid, payload, options, MessagingFee(fee, 0), payable(address(this)));
    }

    function _lzFee(uint32 eid, bytes memory p, bytes memory o) internal view returns (uint256) {
        return _quote(eid, p, o, false).nativeFee;
    }

    function _defaultAckOptions() internal pure returns (bytes memory) {
        // LayerZero V2 executor lzReceive option: 50_000 gas (0xc350). Calibrate per chain.
        return hex"0003010011010000000000000000000000000000c350";
    }

    receive() external payable {} // LZ refunds + moat ACK funding land here
}
