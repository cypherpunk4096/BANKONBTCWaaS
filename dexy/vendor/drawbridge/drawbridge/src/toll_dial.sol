// SPDX-License-Identifier: Apache-2.0
pragma solidity 0.8.26;

interface ISpringToll {
    function setToll(uint256) external;
}

/// @title TollDial — anyone proposes a toll change; anyone executes after 7 days.
/// @notice No cancel. No fast-track. No owner. Cap enforced by Spring itself.
///         DAIO/BONAFIDE participates by proposing/attesting — never by privilege.
///         Salt: pythai.net/toll_dial/v1.
/// @author Professor Codephreak — PYTHAI / BANKON
contract TollDial {
    uint256 public constant DELAY = 7 days;
    ISpringToll public immutable spring;
    mapping(bytes32 => uint256) public eta; // keccak(newBps, nonce) → executable ts
    uint256 public nonce;

    event TollProposed(uint256 newBps, uint256 nonce, uint256 eta);
    event TollExecuted(uint256 newBps, uint256 nonce);

    constructor(address _spring) {
        spring = ISpringToll(_spring);
    }

    function propose(uint256 newBps) external returns (uint256 n) {
        n = ++nonce;
        bytes32 h = keccak256(abi.encode(newBps, n));
        eta[h] = block.timestamp + DELAY; // deterministic, public
        emit TollProposed(newBps, n, eta[h]);
    }

    function execute(uint256 newBps, uint256 n) external {
        bytes32 h = keccak256(abi.encode(newBps, n));
        uint256 t = eta[h];
        require(t != 0 && block.timestamp >= t, "DIAL/not-ready");
        eta[h] = 0;
        spring.setToll(newBps); // Spring re-checks MAX_TOLL_BPS
        emit TollExecuted(newBps, n);
    }
}
