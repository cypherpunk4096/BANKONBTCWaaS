// SPDX-License-Identifier: Apache-2.0
pragma solidity 0.8.26;

/// @title Abacus — StairstepExponentialDecrease price curve for Liquidation 2.0 auctions.
/// @notice Immutable params (Maker-audited curve). Salt: pythai.net/abacus/v1.
/// @author Professor Codephreak — PYTHAI / BANKON
contract Abacus {
    uint256 public immutable cut; // e.g. 0.99e27 → 1% drop per step
    uint256 public immutable step; // seconds between drops
    uint256 constant RAY = 1e27;

    constructor(uint256 _cut, uint256 _step) {
        require(_cut <= RAY, "Abacus/cut>1");
        cut = _cut;
        step = _step;
    }

    /// @notice Deterministic Dutch-auction price after `dur` seconds from `top`.
    function price(uint256 top, uint256 dur) external view returns (uint256 p) {
        uint256 n = dur / step;
        p = top;
        for (uint256 i; i < n; i++) {
            p = p * cut / RAY;
        }
    }
}
