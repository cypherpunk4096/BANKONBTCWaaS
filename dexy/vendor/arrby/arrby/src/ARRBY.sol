// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.24;

import {IPool, IFlashLoanSimpleReceiver} from "./interfaces/IPool.sol";
import {IV2Router} from "./interfaces/IV2Router.sol";
import {ARRBYCore} from "./ARRBYCore.sol";

/// @title ARRBY — Agnostic Rapid aRBitrage flash-loan executor (Aave V3)
/// @notice Chain-agnostic, DEX-agnostic single-asset flash loan arbitrage
///         executor for Aave V3's flashLoanSimple. Deploy the same bytecode
///         on any EVM chain with an Aave V3 Pool — only the constructor's
///         Pool address changes. Arbitrage legs work against any
///         Uniswap-V2-shaped router (see IV2Router).
///
///         Shared concerns — ownership, reentrancy lock, USDT-safe token
///         calls, path validation, treasury accounting, and the per-run
///         notional cap — live in ARRBYCore, a single audited base this and
///         ARRBY3156 both inherit. See ARRBYCore for the reasoning behind
///         each. This file is now only the Aave-specific flash mechanics.
///
/// @dev Audit history: v1.1 path validation + reentrancy lock + zero-address
///      checks; v1.2 USDT-safe token calls; v1.5 fail-fast deadline +
///      ArbitrageInitiated event; v1.6 refactor into ARRBYCore + treasury
///      accounting (_recordProfit) + per-run notional guardrail
///      (_checkNotional). No upgradeable proxy, no admin key beyond a
///      renounceable owner, no receive()/fallback (cypherpunk2048).
contract ARRBY is ARRBYCore, IFlashLoanSimpleReceiver {
    error NotPool();
    error NotInitiator();

    event ArbitrageInitiated(
        address indexed asset,
        uint256 amount,
        address routerA,
        address routerB,
        uint256 minProfit,
        uint256 deadline
    );
    event ArbitrageExecuted(
        address indexed asset,
        uint256 amountBorrowed,
        uint256 premium,
        uint256 grossReturned,
        uint256 profit,
        address routerA,
        address routerB
    );

    IPool public immutable POOL;

    struct ArbParams {
        address routerA;      // buy leg
        address routerB;      // sell leg back
        address[] pathAtoB;   // e.g. [USDC, WETH] on routerA
        address[] pathBtoA;   // e.g. [WETH, USDC] on routerB
        uint256 minProfit;    // wei of `asset`, revert whole tx if not met
        uint256 deadline;     // unix ts, applied to both swaps
    }

    constructor(address poolAddress, address ownerAddress) {
        if (poolAddress == address(0)) revert ZeroAddress();
        POOL = IPool(poolAddress);
        _initCore(ownerAddress);
    }

    /// @notice Entry point. Pulls `amount` of `asset` from Aave V3, executes
    ///         the two-leg swap, repays principal + premium, and reverts the
    ///         entire transaction if profit < arb.minProfit.
    function initiateArbitrage(address asset, uint256 amount, ArbParams calldata arb) external onlyOwner nonReentrant {
        if (arb.deadline < block.timestamp) revert Expired(arb.deadline, block.timestamp);
        _checkNotional(amount);
        _validatePaths(asset, arb.pathAtoB, arb.pathBtoA);
        emit ArbitrageInitiated(asset, amount, arb.routerA, arb.routerB, arb.minProfit, arb.deadline);
        bytes memory params = abi.encode(arb, msg.sender);
        POOL.flashLoanSimple(address(this), asset, amount, params, 0);
    }

    /// @dev Aave V3 callback. Only the Pool may call this, and only mid-flight
    ///      for a loan this contract itself initiated.
    function executeOperation(
        address asset,
        uint256 amount,
        uint256 premium,
        address initiator,
        bytes calldata params
    ) external override returns (bool) {
        if (msg.sender != address(POOL)) revert NotPool();
        if (initiator != address(this)) revert NotInitiator();

        (ArbParams memory arb, ) = abi.decode(params, (ArbParams, address));

        // --- Leg A: asset -> intermediate, on routerA ---
        _safeApprove(asset, arb.routerA, amount);
        uint256[] memory outA = IV2Router(arb.routerA).swapExactTokensForTokens(
            amount, 0, arb.pathAtoB, address(this), arb.deadline
        );
        uint256 intermediateReceived = outA[outA.length - 1];

        // --- Leg B: intermediate -> asset, on routerB ---
        address intermediateToken = arb.pathAtoB[arb.pathAtoB.length - 1];
        _safeApprove(intermediateToken, arb.routerB, intermediateReceived);
        uint256[] memory outB = IV2Router(arb.routerB).swapExactTokensForTokens(
            intermediateReceived, 0, arb.pathBtoA, address(this), arb.deadline
        );
        uint256 grossReturned = outB[outB.length - 1];

        uint256 amountOwed = amount + premium;
        if (grossReturned < amountOwed + arb.minProfit) revert Unprofitable(amountOwed + arb.minProfit, grossReturned);

        uint256 profit = grossReturned - amountOwed;

        // Repay: Aave pulls amountOwed via allowance after this returns true.
        _safeApprove(asset, address(POOL), amountOwed);

        _recordProfit(asset, profit); // storage-only; no external call added to the callback
        emit ArbitrageExecuted(asset, amount, premium, grossReturned, profit, arb.routerA, arb.routerB);
        return true;
    }

    /// @notice Free pre-flight check. Mirrors executeOperation's math exactly.
    function quoteArbitrage(address asset, uint256 amount, ArbParams calldata arb)
        external
        view
        returns (uint256 grossReturned, uint256 amountOwed, int256 expectedProfit)
    {
        uint256 premium = (amount * POOL.FLASHLOAN_PREMIUM_TOTAL()) / 10000;
        uint256[] memory outA = IV2Router(arb.routerA).getAmountsOut(amount, arb.pathAtoB);
        uint256 intermediateReceived = outA[outA.length - 1];
        uint256[] memory outB = IV2Router(arb.routerB).getAmountsOut(intermediateReceived, arb.pathBtoA);
        grossReturned = outB[outB.length - 1];
        amountOwed = amount + premium;
        expectedProfit = int256(grossReturned) - int256(amountOwed);
    }
}
