// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.26;

/// ---------------------------------------------------------------------------
/// BONAFIDE RATIFICATION ADAPTER — the Senatus-side executor for the PLR
/// docket. mindX files each assessed logo as a proposal; when Fides-weighted
/// voting concludes, Senatus executes exactly one of two outcomes per docket
/// item through this adapter: ratify (accept the new logo as canonical) or
/// reject (return it to Pending with a recorded reason).
///
/// The adapter exists so Senatus's generic proposal executor never needs to
/// know the registry's ABI, and so every governance decision over a logo
/// leaves a uniform, indexable audit record: proposal id, docket id, verdict,
/// voter turnout digest, and the Arweave permalink of the assessment manifest
/// the voters actually saw.
///
/// cypherpunk2048: no proxy, no admin. PLR and SENATUS immutable.
/// ---------------------------------------------------------------------------

interface IPLR {
    function ratify(uint256 tokenId) external;
    function reject(uint256 tokenId, string calldata reason) external;
}

contract RatificationAdapter {
    struct Verdict {
        uint256 proposalId;     // BONAFIDE proposal id (Tabularium reference)
        uint256 tokenId;        // PLR iNFT id under judgment
        bool accepted;
        string manifestCID;     // Arweave CID of the assessment manifest voted on
        bytes32 turnoutDigest;  // keccak256 of the Fides-weighted tally snapshot
        uint64 executedAt;
    }

    address public immutable PLR;
    address public immutable SENATUS;

    Verdict[] public verdicts;
    /// tokenId => list of verdict indices (a logo may face the docket many times)
    mapping(uint256 => uint256[]) public docketHistory;

    event Ratified(
        uint256 indexed proposalId,
        uint256 indexed tokenId,
        string manifestCID,
        bytes32 turnoutDigest
    );
    event Rejected(
        uint256 indexed proposalId,
        uint256 indexed tokenId,
        string reason,
        string manifestCID,
        bytes32 turnoutDigest
    );

    error NotSenatus();

    constructor(address plr, address senatus) {
        PLR = plr;
        SENATUS = senatus;
    }

    modifier onlySenatus() {
        if (msg.sender != SENATUS) revert NotSenatus();
        _;
    }

    /// Executed by Senatus when a docket proposal passes.
    function executeRatify(
        uint256 proposalId,
        uint256 tokenId,
        string calldata manifestCID,
        bytes32 turnoutDigest
    ) external onlySenatus {
        IPLR(PLR).ratify(tokenId);
        _record(proposalId, tokenId, true, manifestCID, turnoutDigest);
        emit Ratified(proposalId, tokenId, manifestCID, turnoutDigest);
    }

    /// Executed by Senatus when a docket proposal fails.
    function executeReject(
        uint256 proposalId,
        uint256 tokenId,
        string calldata reason,
        string calldata manifestCID,
        bytes32 turnoutDigest
    ) external onlySenatus {
        IPLR(PLR).reject(tokenId, reason);
        _record(proposalId, tokenId, false, manifestCID, turnoutDigest);
        emit Rejected(proposalId, tokenId, reason, manifestCID, turnoutDigest);
    }

    function _record(
        uint256 proposalId,
        uint256 tokenId,
        bool accepted,
        string calldata manifestCID,
        bytes32 turnoutDigest
    ) private {
        verdicts.push(Verdict({
            proposalId: proposalId,
            tokenId: tokenId,
            accepted: accepted,
            manifestCID: manifestCID,
            turnoutDigest: turnoutDigest,
            executedAt: uint64(block.timestamp)
        }));
        docketHistory[tokenId].push(verdicts.length - 1);
    }

    function verdictCount() external view returns (uint256) {
        return verdicts.length;
    }

    function historyOf(uint256 tokenId) external view returns (uint256[] memory) {
        return docketHistory[tokenId];
    }
}
