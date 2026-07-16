// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.20;

/**
 * @title CompositeVerifier
 * @notice CP-2048 hybrid signature verification gateway. Resolves a suite by ID
 *         from PQCRegistry and requires EVERY component to verify (composite rule).
 *         Replaces the single Falcon-1024 verification path of the x402 v1 profile.
 * @dev Classical (Ed25519) is verified via precompile/library; lattice (ML-DSA) via
 *      a PQC verify precompile; hash-based (SLH-DSA) co-signs are verified off-chain
 *      for Tier-0 governance and attested here by hash commitment.
 * @author Gregory L. (codephreak)
 */

interface IPQCRegistry {
    enum AlgFamily { CLASSICAL, LATTICE, HASH, CODE }
    struct Suite {
        bytes4 suiteId;
        uint8[] components;
        AlgFamily[] families;
        uint8 tier;
        bool registered;
        bool deprecated;
        uint64 sunsetEpoch;
    }
    function getSuite(bytes4 suiteId) external view returns (Suite memory);
    function isUsable(bytes4 suiteId) external view returns (bool);
}

contract CompositeVerifier {

    IPQCRegistry public immutable registry;

    // Precompile addresses (0G Aristotle EIP-8051/8052 profile).
    address constant ED25519_PRECOMPILE = address(0x0a);
    address constant MLDSA_PRECOMPILE   = address(0x0b);
    // Hash-based verification is off-chain; only its commitment is checked here.

    error SuiteNotUsable(bytes4 suiteId);
    error ComponentCountMismatch();
    error ComponentVerifyFailed(uint8 index);
    error HashCosignRequired();

    event CompositeVerified(bytes4 indexed suiteId, bytes32 indexed msgHash, uint8 components);

    constructor(address _registry) {
        registry = IPQCRegistry(_registry);
    }

    /**
     * @notice Verify a CP-2048 composite signature.
     * @param suiteId       Suite identifier (resolved via PQCRegistry).
     * @param message       Domain-separated message (see DomainSeparator).
     * @param sigComponents Signature components, ordered to match suite.components.
     * @param pubComponents Public-key components, same order.
     * @param slhCosignHash Commitment to the off-chain SLH-DSA co-signature (Tier<=1).
     * @return ok true iff ALL components verify.
     */
    function verifyComposite(
        bytes4 suiteId,
        bytes calldata message,
        bytes[] calldata sigComponents,
        bytes[] calldata pubComponents,
        bytes32 slhCosignHash
    ) external returns (bool ok) {
        if (!registry.isUsable(suiteId)) revert SuiteNotUsable(suiteId);

        IPQCRegistry.Suite memory s = registry.getSuite(suiteId);
        uint256 n = s.components.length;
        if (sigComponents.length != n || pubComponents.length != n) {
            revert ComponentCountMismatch();
        }

        bool sawHash;
        bytes32 msgHash = keccak256(message);

        for (uint8 i = 0; i < n; i++) {
            IPQCRegistry.AlgFamily fam = s.families[i];

            if (fam == IPQCRegistry.AlgFamily.CLASSICAL) {
                if (!_verifyEd25519(message, sigComponents[i], pubComponents[i])) {
                    revert ComponentVerifyFailed(i);
                }
            } else if (fam == IPQCRegistry.AlgFamily.LATTICE) {
                if (!_verifyMlDsa(message, sigComponents[i], pubComponents[i])) {
                    revert ComponentVerifyFailed(i);
                }
            } else if (fam == IPQCRegistry.AlgFamily.HASH) {
                // SLH-DSA verified off-chain; require matching commitment.
                if (keccak256(sigComponents[i]) != slhCosignHash) {
                    revert ComponentVerifyFailed(i);
                }
                sawHash = true;
            } else {
                revert ComponentVerifyFailed(i); // CODE family: KEM only, not a signer
            }
        }

        // Tier 0/1 MUST carry a hash-based co-sign (defense-in-depth for roots).
        if (s.tier <= 1 && !sawHash) revert HashCosignRequired();

        emit CompositeVerified(suiteId, msgHash, uint8(n));
        return true;
    }

    // ---- component verifiers (delegate to precompiles / libraries) ----

    function _verifyEd25519(bytes calldata msg_, bytes calldata sig, bytes calldata pk)
        internal view returns (bool)
    {
        (bool s, bytes memory r) =
            ED25519_PRECOMPILE.staticcall(abi.encodePacked(pk, sig, keccak256(msg_)));
        return s && r.length == 32 && r[31] == 0x01;
    }

    function _verifyMlDsa(bytes calldata msg_, bytes calldata sig, bytes calldata pk)
        internal view returns (bool)
    {
        (bool s, bytes memory r) =
            MLDSA_PRECOMPILE.staticcall(abi.encode(msg_, sig, pk));
        return s && r.length == 32 && r[31] == 0x01;
    }
}
