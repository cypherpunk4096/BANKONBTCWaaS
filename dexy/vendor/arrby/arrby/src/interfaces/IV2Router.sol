// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.24;

/// @notice Minimal Uniswap-V2-shaped router interface. Any DEX that forked
///         Uniswap V2 (Sushiswap, Quickswap, Camelot V2, PancakeSwap V2,
///         Trader Joe V1, SpookySwap, Velodrome-V2-compatible routers, ...)
///         satisfies this ABI, which is what makes the arbitrage leg
///         DEX-agnostic as well as chain-agnostic.
interface IV2Router {
    function swapExactTokensForTokens(
        uint256 amountIn,
        uint256 amountOutMin,
        address[] calldata path,
        address to,
        uint256 deadline
    ) external returns (uint256[] memory amounts);

    function getAmountsOut(uint256 amountIn, address[] calldata path)
        external
        view
        returns (uint256[] memory amounts);
}

interface IERC20Min {
    function approve(address spender, uint256 amount) external returns (bool);
    function balanceOf(address account) external view returns (uint256);
    function transfer(address to, uint256 amount) external returns (bool);
}
