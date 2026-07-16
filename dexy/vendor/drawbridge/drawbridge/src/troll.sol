// SPDX-License-Identifier: Apache-2.0
pragma solidity 0.8.26;

import {SafeTransferLib} from "solmate/utils/SafeTransferLib.sol";

/// @title Troll — guardian and tollkeeper of the bridge (Drawbridge toll module).
/// @notice Collects the golden toll (callFee x 1.161803398874989485) in native gas.
///         Moat share (1/phi of surcharge + raw callFee) stays as overcollateralization;
///         royal share (1 - 1/phi) flows to RoyalT. The moat's ONLY outflow is paying
///         LayerZero confirmation (ACK) fees so every crossing closes completely.
///         Salt: pythai.net/tollkeeper/v1 (the code name "troll" is namespace-excluded).
/// @author Professor Codephreak — PYTHAI / BANKON
contract Troll {
    uint256 public constant GOLDEN_TOLL_WAD = 1_161803398874989485; // 1 + phi/10, 18dp
    uint256 public constant PHI_INV_WAD = 618033988749894848; // 1/phi, 18dp
    uint256 public constant WAD = 1e18;

    address public immutable drawbridge; // sole caller (CREATE2-precomputed pairing)
    address payable public immutable royalT;

    uint256 public moat; // overcollateralization buffer; sole outflow = ACK fees

    event TollTaken(address indexed payer, uint256 callFee, uint256 toll, uint256 toMoat, uint256 toRoyalT);
    event AckFunded(bytes32 indexed crossingId, uint256 fee);

    error NotDrawbridge();
    error TollUnpaid();

    constructor(address _drawbridge, address payable _royalT) {
        require(_drawbridge != address(0) && _royalT != address(0), "TROLL/zero");
        drawbridge = _drawbridge;
        royalT = _royalT;
    }

    modifier onlyBridge() {
        if (msg.sender != drawbridge) revert NotDrawbridge();
        _;
    }

    /// @notice Called by Drawbridge with the measured call fee. msg.value prepays;
    ///         Troll splits the surcharge phi-wise and returns the exact refund.
    function collect(address payer, uint256 callFee) external payable onlyBridge returns (uint256 refund) {
        uint256 toll = callFee * GOLDEN_TOLL_WAD / WAD; // ...989485-exact
        if (msg.value < toll) revert TollUnpaid();
        uint256 surcharge = toll - callFee;
        uint256 toMoat = surcharge * PHI_INV_WAD / WAD; // 61.803...%
        uint256 toRoyal = surcharge - toMoat; // 38.196...% (conservation: no dust lost)
        moat += callFee + toMoat;
        SafeTransferLib.safeTransferETH(royalT, toRoyal);
        refund = msg.value - toll;
        emit TollTaken(payer, callFee, toll, callFee + toMoat, toRoyal);
    }

    /// @notice Purpose-locked outflow: pay the LayerZero fee for a CONFIRM (ACK) message.
    ///         Callable only by the Drawbridge, only up to the current moat.
    function fundAck(bytes32 crossingId, uint256 fee) external onlyBridge {
        require(fee <= moat, "TROLL/moat-dry");
        moat -= fee;
        SafeTransferLib.safeTransferETH(payable(drawbridge), fee);
        emit AckFunded(crossingId, fee);
    }

    receive() external payable {
        moat += msg.value; // donations deepen the moat
    }
}
