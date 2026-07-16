// SPDX-License-Identifier: Apache-2.0
pragma solidity 0.8.26;

// PYTHAI PAI — "PAI is for pay". USD-measurement token, 1:1 USDC-collateralized.
// Canonical deploy target: Circle Arc L1 (USDC = native gas, 6 decimals).
// cypherpunk2048: immutable, no admin keys, no proxy, mainnet-only.

import {ERC20} from "solmate/tokens/ERC20.sol";
import {SafeTransferLib} from "solmate/utils/SafeTransferLib.sol";
import {ReentrancyGuard} from "solmate/utils/ReentrancyGuard.sol";

interface IERC20Meta {
    function decimals() external view returns (uint8);
}

/// @title PAI — USD-pegged in-house measurement token (18 dec) backed 1:1 by USDC (6 dec)
/// @author Professor Codephreak — PYTHAI / BANKON
contract PAI is ERC20, ReentrancyGuard {
    using SafeTransferLib for ERC20;

    ERC20 public immutable usdc; // reserve asset (native USDC on Arc)
    uint256 public immutable CONV; // 1e12 == 10**(18-6), decimal parity factor
    uint256 public constant RATE = 1e18; // canonical rate: 1 PAI == 1 USD unit (par)

    event Minted(address indexed to, uint256 usdcIn, uint256 paiOut);
    event Redeemed(address indexed from, uint256 paiIn, uint256 usdcOut);
    /// @notice allchain oracle broadcast: canonical Arc PAI is the reference-rate source
    event RateBeacon(uint256 rate, uint256 totalSupply, uint256 reserve, uint256 blockTs);

    constructor(address _usdc) ERC20("PYTHAI PAI", "PAI", 18) {
        require(_usdc != address(0), "PAI/zero-usdc");
        uint8 d = IERC20Meta(_usdc).decimals();
        require(d <= 18, "PAI/bad-usdc-decimals");
        usdc = ERC20(_usdc);
        CONV = 10 ** (18 - d); // 1e12 for canonical 6-decimal USDC
    }

    /// @notice Lock `usdcAmt` (6-dec) and mint exact 18-dec PAI. Checks-effects-interactions.
    function mint(uint256 usdcAmt) external nonReentrant returns (uint256 paiOut) {
        require(usdcAmt > 0, "PAI/zero-in");
        paiOut = usdcAmt * CONV; // exact: no precision loss (mul only)
        _mint(msg.sender, paiOut); // effect
        usdc.safeTransferFrom(msg.sender, address(this), usdcAmt); // interaction
        emit Minted(msg.sender, usdcAmt, paiOut);
        _beacon();
    }

    /// @notice Burn 18-dec PAI (exact multiple of CONV) and release 6-dec USDC.
    function redeem(uint256 paiAmt) external nonReentrant returns (uint256 usdcOut) {
        require(paiAmt > 0, "PAI/zero-in");
        require(paiAmt % CONV == 0, "PAI/non-exact"); // forbid reserve-rounding dust
        usdcOut = paiAmt / CONV; // exact integer division, remainder == 0
        _burn(msg.sender, paiAmt); // effect (reverts if balance too low)
        usdc.safeTransfer(msg.sender, usdcOut); // interaction
        emit Redeemed(msg.sender, paiAmt, usdcOut);
        _beacon();
    }

    /// @notice Reference rate for allchain EVM PAI representations (always par).
    function rate() external pure returns (uint256) {
        return RATE;
    }

    /// @notice Solvency invariant surface: reserve must always back supply exactly.
    function reserve() public view returns (uint256) {
        return usdc.balanceOf(address(this));
    }

    function backingOk() public view returns (bool) {
        return reserve() * CONV == totalSupply();
    }

    function _beacon() internal {
        emit RateBeacon(RATE, totalSupply(), reserve(), block.timestamp);
    }
}
