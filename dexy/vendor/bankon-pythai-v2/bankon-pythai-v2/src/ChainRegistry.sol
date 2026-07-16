// SPDX-License-Identifier: Apache-2.0
// (c) BANKON — cypherpunk2048 standard
//
// ChainRegistry — lightweight on-chain mirror of agenticplace.pythai.net/allchain.html
//
// This is NOT part of the LayerZero protocol. LayerZero routes messages by Endpoint ID
// (eid, uint32), which is unrelated to a chain's EVM chainId. This registry exists so that
// AgenticPlace / mindX / BANKON tooling and the DAIO governance contract can look up, on
// mainnet, the mapping {EVM chainId <-> LayerZero eid <-> human name <-> BKPY OFT address}
// without depending on an off-chain JSON file. Populate via addChain() from the DAIO owner
// after each new spoke deployment (see script/WireOApp.s.sol).
pragma solidity ^0.8.22;

import { Ownable } from "@openzeppelin/contracts/access/Ownable.sol";

contract ChainRegistry is Ownable {
    struct ChainInfo {
        uint256 evmChainId; // 0 for non-EVM chains (informational only; see notes below)
        uint32 lzEid;       // LayerZero V2 Endpoint ID, 0 if chain has no OFT deployment
        string name;        // human-readable name, matches allchain.html labeling
        address oft;        // BankonPythaiOFT address on that chain (address(0) if none yet)
        bool isEvm;
        bool active;
    }

    /// @dev keyed by lzEid for EVM/LayerZero-supported chains; for non-LayerZero chains
    ///      (e.g. Algorand, see notes) use a synthetic key documented in NON_EVM_KEYS below.
    mapping(uint32 => ChainInfo) public chains;
    uint32[] public registeredEids;

    /// @notice Synthetic eid used to register Algorand in this registry for UX/reference
    ///         purposes only. Algorand is NOT a LayerZero-supported chain — BKPY cannot be
    ///         bridged there via OFT. Value bridges to Algorand via the separate x402
    ///         gateway (see src/gateway/X402AlgorandGateway.sol and docs/X402_BRIDGE.md).
    uint32 public constant ALGORAND_SYNTHETIC_EID = 999999;

    event ChainRegistered(uint32 indexed lzEid, string name, address oft);
    event ChainUpdated(uint32 indexed lzEid, address oft, bool active);

    constructor(address _owner) Ownable(_owner) {}

    function addChain(
        uint32 _lzEid,
        uint256 _evmChainId,
        string calldata _name,
        address _oft,
        bool _isEvm
    ) external onlyOwner {
        if (chains[_lzEid].lzEid == 0 && _lzEid != 0) {
            registeredEids.push(_lzEid);
        }
        chains[_lzEid] = ChainInfo({
            evmChainId: _evmChainId,
            lzEid: _lzEid,
            name: _name,
            oft: _oft,
            isEvm: _isEvm,
            active: true
        });
        emit ChainRegistered(_lzEid, _name, _oft);
    }

    function setActive(uint32 _lzEid, bool _active) external onlyOwner {
        chains[_lzEid].active = _active;
        emit ChainUpdated(_lzEid, chains[_lzEid].oft, _active);
    }

    function getAllEids() external view returns (uint32[] memory) {
        return registeredEids;
    }

    function count() external view returns (uint256) {
        return registeredEids.length;
    }
}
