// SPDX-License-Identifier: Apache-2.0
pragma solidity 0.8.26;

import { BankonToll } from "./BankonToll.sol";

interface IERC20 {
    function transferFrom(address from, address to, uint256 amount) external returns (bool);
    function transfer(address to, uint256 amount) external returns (bool);
}

/// @title BankonFacilitator — escrow that HOLDS the client's asset for a transfer and charges the
///        BANKON golden-ratio toll (BankonToll) to the treasury on every facilitation.
/// @notice The contract is the escrow point: the asset passes THROUGH it (native value held as msg.value,
///         ERC-20 pulled to the contract) before release to the recipient — so no transfer settles without
///         the toll being taken. The toll is native, pegged to this transaction's gas, held in bankon.eth.
///         DEXY never holds a key; the client signs and sends. This is the on-chain toll leg for the BANKON
///         bridge services and any contract facilitation.
/// @author Professor Codephreak — PYTHAI / BANKON. Salt: bankon.eth/facilitator/v1.
contract BankonFacilitator is BankonToll {
    uint256 private _lock = 1;

    event Facilitated(
        address indexed from, address indexed to, address indexed asset, uint256 amount, uint256 toll
    );

    error Reentrancy();
    error ZeroRecipient();
    error AssetHeldFailed();
    error AssetReleaseFailed();

    modifier nonReentrant() {
        if (_lock != 1) revert Reentrancy();
        _lock = 2;
        _;
        _lock = 1;
    }

    constructor(address payable _treasury, uint256 _gasOverhead) BankonToll(_treasury, _gasOverhead) {}

    /// @notice Facilitate a NATIVE (ETH) transfer of `amount` to `to`.
    ///         Send msg.value = amount + (toll headroom); the golden toll is deducted and the rest refunded.
    ///         The contract holds `amount` (as msg.value) and releases it to `to`, then tolls the treasury.
    function facilitateNative(address payable to, uint256 amount) external payable nonReentrant {
        uint256 g0 = gasleft();
        if (to == address(0)) revert ZeroRecipient();

        // Release the escrowed asset to the recipient (the contract held it as msg.value).
        (bool okA, ) = to.call{ value: amount }("");
        if (!okA) revert AssetReleaseFailed();

        // Toll = golden ratio of this facilitation's gas fee → treasury; refund msg.value - amount - toll.
        uint256 toll = _collectToll(g0, amount);
        emit Facilitated(msg.sender, to, address(0), amount, toll);
    }

    /// @notice Facilitate an ERC-20 transfer of `amount` to `to`. The toll is paid in NATIVE value:
    ///         send msg.value = (toll headroom). The contract HOLDS the token (pulled to itself) then
    ///         releases it to `to`, and tolls the treasury natively.
    function facilitateToken(IERC20 token, address to, uint256 amount) external payable nonReentrant {
        uint256 g0 = gasleft();
        if (to == address(0)) revert ZeroRecipient();

        // Hold the client's asset in escrow (contract custody), then release to recipient.
        if (!token.transferFrom(msg.sender, address(this), amount)) revert AssetHeldFailed();
        if (!token.transfer(to, amount)) revert AssetReleaseFailed();

        uint256 toll = _collectToll(g0, 0); // no native asset spent; msg.value covers toll only
        emit Facilitated(msg.sender, to, address(token), amount, toll);
    }
}
