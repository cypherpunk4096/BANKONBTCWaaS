// SPDX-License-Identifier: Apache-2.0
pragma solidity 0.8.26;

interface IVatInit {
    function init(bytes32 ilk, uint256 spot, uint256 line, uint256 dust) external;
}

/// @title CollateralFactory — timelocked, permissionless collateral onboarding.
/// @notice The ONLY governance action in the FOUR BUCKS system. Anyone proposes; anyone
///         executes after DELAY. No admin can cancel, fast-track, or touch existing vaults.
///         Creates NEW isolated ilks only. Salt: pythai.net/collateral_factory/v1.
/// @author Professor Codephreak — PYTHAI / BANKON
contract CollateralFactory {
    uint256 public constant DELAY = 7 days;
    IVatInit public immutable vat;
    mapping(bytes32 => uint256) public eta; // proposalHash → executable timestamp

    event Proposed(bytes32 indexed h, bytes32 ilk, uint256 eta);
    event Executed(bytes32 indexed h, bytes32 ilk);

    constructor(address _vat) {
        vat = IVatInit(_vat);
    }

    function propose(bytes32 ilk, uint256 spot, uint256 line, uint256 dust) external returns (bytes32 h) {
        h = keccak256(abi.encode(ilk, spot, line, dust));
        require(eta[h] == 0, "CF/dup");
        eta[h] = block.timestamp + DELAY; // deterministic; anyone can propose
        emit Proposed(h, ilk, eta[h]);
    }

    function execute(bytes32 ilk, uint256 spot, uint256 line, uint256 dust) external {
        bytes32 h = keccak256(abi.encode(ilk, spot, line, dust));
        uint256 t = eta[h];
        require(t != 0 && block.timestamp >= t, "CF/not-ready"); // anyone can execute
        eta[h] = 0;
        vat.init(ilk, spot, line, dust); // deploys NEW isolated ilk — cannot mutate existing
        emit Executed(h, ilk);
    }
}
