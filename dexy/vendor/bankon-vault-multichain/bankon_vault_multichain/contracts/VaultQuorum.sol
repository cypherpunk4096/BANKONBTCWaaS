// SPDX-License-Identifier: GPL-3.0-or-later
pragma solidity ^0.8.24;

/// @title  VaultQuorum (multi-chain, deterministic, immutable)
/// @notice N-of-M approval ledger authorizing OFF-CHAIN reconstitution of a Tomb key.
///         It stores ONLY a commitment = sha256(tomb.key). Deployed at the SAME address on
///         every target chain (CREATE2 + identical constructor args), so the commitment is
///         anchored and cross-verifiable everywhere. No admin, no upgrade path — the config
///         is fixed at construction. That immutability + cross-chain equality IS the integrity
///         guarantee. Shares themselves never touch any chain (Shamir, off-chain).
contract VaultQuorum {
    // --- immutable config: identical on all chains => identical initcode => same CREATE2 address
    bytes32 public immutable commitment;      // sha256(tomb.key)
    uint8   public immutable threshold;       // N approvals required
    uint256 public immutable primaryChainId;  // the authoritative chain
    uint256 public immutable fallbackDelay;   // break-glass timelock (seconds) on non-primary

    // --- mutable state ---
    mapping(address => bool) public isOwner;  // the M approver set
    mapping(address => bool) public approved;
    uint8   public approvals;
    uint256 public fallbackReadyAt;           // set when quorum reached on a non-primary chain
    bool    private _quorumReached;

    event Approved(address indexed owner, uint8 count);
    event QuorumReached(uint256 indexed chainId, uint256 fallbackReadyAt);
    event Unlocked(bytes32 commitment, uint256 indexed chainId);

    error NotOwner();
    error AlreadyApproved();

    constructor(
        bytes32 _commitment,
        uint8 _threshold,
        uint256 _primaryChainId,
        uint256 _fallbackDelay,
        address[] memory owners
    ) {
        commitment     = _commitment;
        threshold      = _threshold;
        primaryChainId = _primaryChainId;
        fallbackDelay  = _fallbackDelay;
        for (uint256 i; i < owners.length; ++i) isOwner[owners[i]] = true;
    }

    /// @return true if THIS deployment is the authoritative (primary) chain.
    function isPrimary() public view returns (bool) {
        return block.chainid == primaryChainId;
    }

    /// @notice Register an owner approval. At quorum: primary unlocks instantly; a non-primary
    ///         chain instead arms a timelock so a single compromised chain cannot fast-authorize.
    function approve() external {
        if (!isOwner[msg.sender]) revert NotOwner();
        if (approved[msg.sender]) revert AlreadyApproved();
        approved[msg.sender] = true;
        approvals += 1;
        emit Approved(msg.sender, approvals);

        if (!_quorumReached && approvals >= threshold) {
            _quorumReached = true;
            if (isPrimary()) {
                emit Unlocked(commitment, block.chainid);
            } else {
                fallbackReadyAt = block.timestamp + fallbackDelay;
                emit QuorumReached(block.chainid, fallbackReadyAt);
            }
        }
    }

    /// @notice Whether reconstitution is authorized ON THIS chain right now.
    ///         Primary: immediately at quorum. Non-primary: quorum + elapsed timelock.
    function unlocked() external view returns (bool) {
        if (!_quorumReached) return false;
        if (isPrimary()) return true;
        return fallbackReadyAt != 0 && block.timestamp >= fallbackReadyAt;
    }
}
