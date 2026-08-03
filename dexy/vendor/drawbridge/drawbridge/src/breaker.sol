// SPDX-License-Identifier: Apache-2.0
pragma solidity 0.8.26;

interface IOracleView {
    function fallbackMedian() external view returns (uint256);
}

/// @title DeterministicBreaker — permissionless circuit breaker (no admin-gated pause).
/// @notice Trips automatically when the in-house fallback median and the Pyth read diverge
///         beyond maxDivergeBps; untrips deterministically when they reconverge. No key can
///         override it. Salt: pythai.net/breaker/v1.
/// @author Professor Codephreak — PYTHAI / BANKON
contract DeterministicBreaker {
    IOracleView public immutable oracle;
    uint256 public immutable maxDivergeBps;

    constructor(address _oracle, uint256 _bps) {
        oracle = IOracleView(_oracle);
        maxDivergeBps = _bps;
    }

    /// @notice Contracts gate mint/liquidation on this. Pure function of oracle state.
    /// @param pythRead the current Pyth-derived reference (passed by caller who poked Pyth)
    function tripped(uint256 pythRead) external view returns (bool) {
        uint256 med = oracle.fallbackMedian();
        if (med == 0 || pythRead == 0) return true; // no data → safe-stop
        uint256 hi = pythRead > med ? pythRead : med;
        uint256 lo = pythRead > med ? med : pythRead;
        return (hi - lo) * 10_000 > lo * maxDivergeBps;
    }
}
