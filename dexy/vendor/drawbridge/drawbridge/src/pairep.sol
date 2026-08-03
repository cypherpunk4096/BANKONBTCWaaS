// SPDX-License-Identifier: Apache-2.0
pragma solidity 0.8.26;

import {ERC20} from "solmate/tokens/ERC20.sol";

interface IRateBeaconReader {
    function canonicalRate() external view returns (uint256 rate, uint256 ts);
}

/// @title PAIrep — PAI representation on a non-Arc EVM chain; inherits the canonical Arc rate.
/// @notice Mint/burn is controlled ONLY by a permissionless bridge attestation (burn-and-mint),
///         never by an admin key. Rate is read from a relayed beacon of the canonical Arc PAI.
/// @author Professor Codephreak — PYTHAI / BANKON
contract PAIrep is ERC20 {
    IRateBeaconReader public immutable beacon; // relayed Arc RateBeacon reader (immutable)
    address public immutable drawbridge; // sole minter (burn-and-mint transport)
    uint256 public constant MAX_STALE = 3600; // 1h staleness ceiling on the relayed rate

    error NotMinter();

    constructor(address _beacon, address _drawbridge) ERC20("PYTHAI PAI (rep)", "PAI", 18) {
        beacon = IRateBeaconReader(_beacon);
        drawbridge = _drawbridge;
    }

    /// @notice Local rate = canonical Arc rate, reverting if the relayed beacon is stale.
    function rate() external view returns (uint256 r) {
        uint256 ts;
        (r, ts) = beacon.canonicalRate();
        require(block.timestamp - ts <= MAX_STALE, "PAIrep/stale-beacon");
        require(r == 1e18, "PAIrep/rate-depeg"); // par-only invariant
    }

    modifier onlyBridge() {
        if (msg.sender != drawbridge) revert NotMinter();
        _;
    }

    function mint(address to, uint256 amt) external onlyBridge {
        _mint(to, amt);
    }

    function burn(address from, uint256 amt) external onlyBridge {
        _burn(from, amt);
    }
}
