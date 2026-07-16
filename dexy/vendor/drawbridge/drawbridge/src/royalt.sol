// SPDX-License-Identifier: Apache-2.0
pragma solidity 0.8.26;

import {SafeTransferLib} from "solmate/utils/SafeTransferLib.sol";

/// @title RoyalT — golden royalty module for the Drawbridge (and anything ERC-2981).
/// @notice Receives the royal share of every toll; render() sweeps to the crown
///         (bankon.eth-resolved treasury). No admin, no pause, no other path.
///         Salt: bankon.eth/royalt/v1.
/// @author Professor Codephreak — PYTHAI / BANKON
contract RoyalT {
    /// @dev Pin the bankon.eth resolution here BEFORE compile (constant keeps initcode
    ///      identical on every chain). Resolve twice against the subname registrar.
    address public constant CROWN = 0x000000000000000000000000000000000000dEaD;

    /// @dev Golden royalty: phi shifted two places → 1.6180339887498948% of a unit.
    uint256 public constant GOLDEN_ROYALTY_WAD = 16180339887498948;

    uint256 public accrued;
    uint256 public renderedTotal;

    event Tribute(address indexed source, uint256 amount);
    event Rendered(address indexed caller, uint256 amount);

    receive() external payable {
        accrued += msg.value;
        emit Tribute(msg.sender, msg.value);
    }

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
        external
        pure
        returns (address receiver, uint256 royaltyAmount)
    {
        receiver = CROWN;
        royaltyAmount = salePrice * GOLDEN_ROYALTY_WAD / 1e18;
    }
}
