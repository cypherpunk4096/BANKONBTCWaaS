// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.20;

/**
 * @title PQCRegistry
 * @notice The single crypto-agile surface of an otherwise immutable system.
 *         Immutable consumer contracts reference suite IDs; this registry maps
 *         IDs -> suite parameters and can DEPRECATE (never mutate) a broken suite.
 * @dev CP-2048 v2.0.0. Enforces >=2 algorithm families for Tier 0/1 at write time,
 *      structurally preventing a regression to a single-algorithm (Falcon-only) root.
 * @author Gregory L. (codephreak)
 */

interface ISenatus {
    function isRootQuorum(bytes calldata slhDsaCosign) external view returns (bool);
    function isSenator(address a) external view returns (bool);
    function passedSupermajority(bytes32 proposal) external view returns (bool);
}

contract PQCRegistry {

    // Disjoint mathematical assumptions. Diversity across these is the whole point.
    enum AlgFamily { CLASSICAL, LATTICE, HASH, CODE }

    struct Suite {
        bytes4      suiteId;      // "S0".."S3", "SL"
        uint8[]     components;   // algorithm IDs; ALL must verify (composite)
        AlgFamily[] families;     // parallel to components
        uint8       tier;         // 0 = roots ... 3 = ephemeral
        bool        registered;
        bool        deprecated;
        uint64      sunsetEpoch;  // verifiers MUST reject after this; 0 = none
    }

    // Algorithm ID -> human label (for tooling; verification uses IDs)
    mapping(uint8 => string) public algName;
    mapping(uint8 => AlgFamily) public algFamily;

    mapping(bytes4 => Suite) private suites;
    bytes4[] public suiteList;

    ISenatus public immutable senatus;

    event SuiteRegistered(bytes4 indexed suiteId, uint8 tier, uint8 familyCount);
    event SuiteDeprecated(bytes4 indexed suiteId, uint64 sunsetEpoch);
    event AlgorithmDefined(uint8 indexed algId, string name, AlgFamily family);

    error SingleFamilyRootForbidden();   // the anti-Falcon-only guard
    error NotRootAuthority();
    error NotSenator();
    error SuiteExists();
    error SuiteMissing();
    error ImmutableMeaning();

    constructor(address _senatus) {
        senatus = ISenatus(_senatus);
        _seedAlgorithms();
    }

    // ---- Algorithm catalog (seeded; extendable by root authority) ----

    function _seedAlgorithms() internal {
        _defineAlg(1, "Ed25519",       AlgFamily.CLASSICAL);
        _defineAlg(2, "ML-DSA-44",     AlgFamily.LATTICE);
        _defineAlg(3, "ML-DSA-65",     AlgFamily.LATTICE);
        _defineAlg(4, "ML-DSA-87",     AlgFamily.LATTICE);
        _defineAlg(5, "SLH-DSA-256s",  AlgFamily.HASH);
        _defineAlg(6, "FN-DSA-1024",   AlgFamily.LATTICE); // optional leaf only
        // KEM side (recorded for completeness; used off-chain)
        _defineAlg(20, "X25519",       AlgFamily.CLASSICAL);
        _defineAlg(21, "ML-KEM-768",   AlgFamily.LATTICE);
        _defineAlg(22, "ML-KEM-1024",  AlgFamily.LATTICE);
        _defineAlg(23, "HQC",          AlgFamily.CODE);
    }

    function _defineAlg(uint8 id, string memory name, AlgFamily fam) internal {
        algName[id] = name;
        algFamily[id] = fam;
        emit AlgorithmDefined(id, name, fam);
    }

    // ---- Suite registration (Tier 0 root authority) ----

    /**
     * @notice Register a new suite. Tier 0/1 MUST span >=2 distinct families.
     * @param slhDsaCosign Hash-based (SLH-DSA) co-signature proving root quorum.
     */
    function registerSuite(
        bytes4 suiteId,
        uint8[] calldata components,
        uint8 tier,
        bytes calldata slhDsaCosign
    ) external {
        if (!senatus.isRootQuorum(slhDsaCosign)) revert NotRootAuthority();
        if (suites[suiteId].registered) revert SuiteExists();

        AlgFamily[] memory fams = new AlgFamily[](components.length);
        uint256 familyMask;
        for (uint256 i = 0; i < components.length; i++) {
            fams[i] = algFamily[components[i]];
            familyMask |= (1 << uint256(fams[i]));
        }

        // Anti-regression guard: roots and governance keys cannot be single-family.
        if (tier <= 1 && _popcount(familyMask) < 2) revert SingleFamilyRootForbidden();

        suites[suiteId] = Suite({
            suiteId: suiteId,
            components: components,
            families: fams,
            tier: tier,
            registered: true,
            deprecated: false,
            sunsetEpoch: 0
        });
        suiteList.push(suiteId);

        emit SuiteRegistered(suiteId, tier, uint8(_popcount(familyMask)));
    }

    /**
     * @notice Deprecate a suite (retire without redeploy). Meaning is never mutated;
     *         a successor suite is registered separately.
     */
    function deprecateSuite(bytes4 suiteId, uint64 sunsetEpoch, bytes32 proposal) external {
        if (!senatus.isSenator(msg.sender)) revert NotSenator();
        if (!senatus.passedSupermajority(proposal)) revert NotSenator();
        Suite storage s = suites[suiteId];
        if (!s.registered) revert SuiteMissing();
        s.deprecated = true;
        s.sunsetEpoch = sunsetEpoch;
        emit SuiteDeprecated(suiteId, sunsetEpoch);
    }

    // ---- Read path (consumed by CompositeVerifier & Frame Tx logic) ----

    function getSuite(bytes4 suiteId) external view returns (Suite memory) {
        Suite memory s = suites[suiteId];
        if (!s.registered) revert SuiteMissing();
        return s;
    }

    function isUsable(bytes4 suiteId) external view returns (bool) {
        Suite memory s = suites[suiteId];
        if (!s.registered || s.deprecated) return false;
        if (s.sunsetEpoch != 0 && block.timestamp >= s.sunsetEpoch) return false;
        return true;
    }

    function suiteCount() external view returns (uint256) {
        return suiteList.length;
    }

    // ---- helpers ----

    function _popcount(uint256 x) internal pure returns (uint256 c) {
        while (x != 0) { c += x & 1; x >>= 1; }
    }
}
