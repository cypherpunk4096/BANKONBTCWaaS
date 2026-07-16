// SPDX-License-Identifier: Apache-2.0
pragma solidity 0.8.26;

import {ERC20} from "solmate/tokens/ERC20.sol";

/// @title PAIm — multichain PAI. Minters fixed at construction via CREATE2 precompute.
/// @notice Only the Troll/Spring (deposit/redeem) and Drawbridge (bridge) may mint/burn.
///         No owner. No setter. Addresses are precomputed with CREATE2 and pinned.
/// @author Professor Codephreak — PYTHAI / BANKON
contract PAIm is ERC20 {
    address public immutable gate; // per-chain USDC well (Troll or Spring)
    address public immutable drawbridge; // LayerZero V2 burn-and-mint transport

    error NotMinter();

    constructor(address _gate, address _drawbridge) ERC20("PYTHAI PAI", "PAI", 18) {
        require(_gate != address(0) && _drawbridge != address(0), "PAI/zero");
        gate = _gate;
        drawbridge = _drawbridge;
    }

    modifier onlyMinter() {
        if (msg.sender != gate && msg.sender != drawbridge) revert NotMinter();
        _;
    }

    function mint(address to, uint256 amt) external onlyMinter {
        _mint(to, amt);
    }

    function burn(address from, uint256 amt) external onlyMinter {
        _burn(from, amt);
    }
}
