// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.24;

import {IERC3156FlashLender, IERC3156FlashBorrower, IERC165} from "./interfaces/IERC3156.sol";
import {IV2Router} from "./interfaces/IV2Router.sol";
import {ARRBYCore} from "./ARRBYCore.sol";

/// @title ARRBY3156 — ERC-3156 flash-mint arbitrage executor
/// @notice Sibling to ARRBY.sol for the standard EIP-3156 flash loan
///         interface rather than Aave's flashLoanSimple. Works against any
///         ERC-3156 lender — most notably MakerDAO/Sky's DssFlash, which
///         flash-mints DAI at a verified 0% fee on Ethereum mainnet
///         (0x60744434d6339a6B27d73d9Eda62b6F66a0a04FA). Where a 3156 lender
///         and Aave both exist for an asset, 3156 is strictly cheaper —
///         backend/src/flash-sources.js picks the cheaper source at call time.
///
///         Shared concerns (ownership, reentrancy, USDT-safe token calls,
///         path validation, treasury, notional cap) live in ARRBYCore, the
///         same base ARRBY.sol inherits — one audited implementation, no
///         duplication.
///
/// @dev v1.5 added EIP-165 (advertises IERC165 + the ERC-3156 borrower id)
///      and a fail-fast deadline check. v1.6 refactors shared logic into
///      ARRBYCore and adds treasury accounting + the per-run notional cap.
contract ARRBY3156 is ARRBYCore, IERC3156FlashBorrower, IERC165 {
    bytes32 private constant CALLBACK_SUCCESS = keccak256("ERC3156FlashBorrower.onFlashLoan");

    // EIP-165 interface ids (computed via keccak256, verified in-session).
    bytes4 private constant IID_ERC165 = 0x01ffc9a7;            // supportsInterface(bytes4)
    bytes4 private constant IID_ERC3156_BORROWER = 0x23e30c8b; // onFlashLoan(address,address,uint256,uint256,bytes)

    error NotLender();
    error NotInitiator();

    event ArbitrageInitiated(
        address indexed lender,
        address indexed asset,
        uint256 amount,
        address routerA,
        address routerB,
        uint256 minProfit,
        uint256 deadline
    );
    event ArbitrageExecuted(
        address indexed lender,
        address indexed asset,
        uint256 amountBorrowed,
        uint256 fee,
        uint256 grossReturned,
        uint256 profit,
        address routerA,
        address routerB
    );

    struct ArbParams {
        address lender;       // any IERC3156FlashLender, e.g. DssFlash
        address routerA;
        address routerB;
        address[] pathAtoB;
        address[] pathBtoA;
        uint256 minProfit;
        uint256 deadline;
    }

    constructor(address ownerAddress) {
        _initCore(ownerAddress);
    }

    /// @notice Entry point. `arb.lender` is any ERC-3156 lender (pass DssFlash's
    ///         address for 0%-fee DAI on mainnet, or any other compliant lender).
    function initiateArbitrage(address asset, uint256 amount, ArbParams calldata arb) external onlyOwner nonReentrant {
        if (arb.deadline < block.timestamp) revert Expired(arb.deadline, block.timestamp);
        if (arb.lender == address(0) || arb.routerA == address(0) || arb.routerB == address(0)) revert ZeroAddress();
        _checkNotional(amount);
        _validatePaths(asset, arb.pathAtoB, arb.pathBtoA);
        emit ArbitrageInitiated(arb.lender, asset, amount, arb.routerA, arb.routerB, arb.minProfit, arb.deadline);
        bytes memory params = abi.encode(arb, msg.sender);
        IERC3156FlashLender(arb.lender).flashLoan(this, asset, amount, params);
    }

    /// @notice EIP-165. Lets ERC-3156 lenders / tooling verify this contract
    ///         really is a flash borrower before calling into it.
    function supportsInterface(bytes4 interfaceId) external pure override returns (bool) {
        return interfaceId == IID_ERC165 || interfaceId == IID_ERC3156_BORROWER;
    }

    function onFlashLoan(
        address initiator,
        address asset,
        uint256 amount,
        uint256 fee,
        bytes calldata params
    ) external override returns (bytes32) {
        // Per EIP-3156, flashLoan() calls receiver.onFlashLoan() directly, so
        // msg.sender here IS the lender. Require it to match the lender the
        // initiator claimed, stopping a rogue contract from driving this
        // callback with attacker-controlled params from an unauthorized lender.
        (ArbParams memory arb, ) = abi.decode(params, (ArbParams, address));
        if (msg.sender != arb.lender) revert NotLender();
        if (initiator != address(this)) revert NotInitiator();

        _safeApprove(asset, arb.routerA, amount);
        uint256[] memory outA = IV2Router(arb.routerA).swapExactTokensForTokens(
            amount, 0, arb.pathAtoB, address(this), arb.deadline
        );
        uint256 intermediateReceived = outA[outA.length - 1];

        address intermediateToken = arb.pathAtoB[arb.pathAtoB.length - 1];
        _safeApprove(intermediateToken, arb.routerB, intermediateReceived);
        uint256[] memory outB = IV2Router(arb.routerB).swapExactTokensForTokens(
            intermediateReceived, 0, arb.pathBtoA, address(this), arb.deadline
        );
        uint256 grossReturned = outB[outB.length - 1];

        uint256 amountOwed = amount + fee;
        if (grossReturned < amountOwed + arb.minProfit) revert Unprofitable(amountOwed + arb.minProfit, grossReturned);

        uint256 profit = grossReturned - amountOwed;
        _safeApprove(asset, msg.sender, amountOwed); // ERC-3156 pulls amount+fee via allowance after we return

        _recordProfit(asset, profit);
        emit ArbitrageExecuted(msg.sender, asset, amount, fee, grossReturned, profit, arb.routerA, arb.routerB);
        return CALLBACK_SUCCESS;
    }

    /// @notice Free pre-flight check mirroring the callback's math exactly.
    function quoteArbitrage(address asset, uint256 amount, ArbParams calldata arb)
        external
        view
        returns (uint256 grossReturned, uint256 amountOwed, int256 expectedProfit)
    {
        uint256 fee = IERC3156FlashLender(arb.lender).flashFee(asset, amount);
        uint256[] memory outA = IV2Router(arb.routerA).getAmountsOut(amount, arb.pathAtoB);
        uint256 intermediateReceived = outA[outA.length - 1];
        uint256[] memory outB = IV2Router(arb.routerB).getAmountsOut(intermediateReceived, arb.pathBtoA);
        grossReturned = outB[outB.length - 1];
        amountOwed = amount + fee;
        expectedProfit = int256(grossReturned) - int256(amountOwed);
    }
}
