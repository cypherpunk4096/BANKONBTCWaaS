// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.24;

/// @notice Minimal Aave V3 Pool interface — identical address ABI on every
///         chain Aave V3 is deployed to (Ethereum, Arbitrum, Optimism, Base,
///         Polygon, Avalanche, Metis, Scroll, Gnosis, BNB, zkSync, etc).
///         This is what makes the flash loan itself chain-agnostic: only the
///         Pool address in config/chains.json changes per chain.
interface IPool {
    function flashLoanSimple(
        address receiverAddress,
        address asset,
        uint256 amount,
        bytes calldata params,
        uint16 referralCode
    ) external;

    function FLASHLOAN_PREMIUM_TOTAL() external view returns (uint128);
}

interface IFlashLoanSimpleReceiver {
    function executeOperation(
        address asset,
        uint256 amount,
        uint256 premium,
        address initiator,
        bytes calldata params
    ) external returns (bool);
}
