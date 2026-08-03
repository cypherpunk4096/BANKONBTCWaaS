// SPDX-License-Identifier: Apache-2.0
pragma solidity 0.8.26;

import {ERC20} from "solmate/tokens/ERC20.sol";

/// @notice 6-decimal USDC mock for tests.
contract MockUSDC is ERC20 {
    constructor() ERC20("USD Coin", "USDC", 6) {}

    function mint(address to, uint256 amt) external {
        _mint(to, amt);
    }
}
