// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.20;

import "forge-std/Script.sol";
import "../src/pqc/PQCRegistry.sol";
import "../src/pqc/CompositeVerifier.sol";
import "../src/security/CircuitBreaker.sol";
import "../src/core/HardenedSettlement.sol";

/**
 * @title DeployHardened
 * @notice Wires the CP-2048 defensive stack in dependency order and seeds the
 *         canonical suites. Run after core PaymentRail deployment.
 *
 *   PQCRegistry  <- senatus
 *   CompositeVerifier <- registry
 *   CircuitBreaker <- senatus, window, ceiling
 *   HardenedSettlement <- breaker, verifier, highValueThreshold
 *
 * Env:
 *   SENATUS_ADDR            governance multisig (root quorum authority)
 *   CB_WINDOW_SECONDS       tripwire rolling window (e.g. 3600)
 *   CB_MAX_PER_WINDOW       max settled value per window (wei)
 *   HIGH_VALUE_THRESHOLD    composite-signature-required threshold (wei)
 *   ROOT_COSIGN             hex SLH-DSA co-signature proving root quorum
 */
contract DeployHardened is Script {

    // canonical algorithm IDs (must match PQCRegistry seed)
    uint8 constant ED25519      = 1;
    uint8 constant ML_DSA_44    = 2;
    uint8 constant ML_DSA_65    = 3;
    uint8 constant ML_DSA_87    = 4;
    uint8 constant SLH_DSA_256s = 5;

    function run() external {
        address senatus = vm.envAddress("SENATUS_ADDR");
        uint256 window  = vm.envUint("CB_WINDOW_SECONDS");
        uint256 ceiling = vm.envUint("CB_MAX_PER_WINDOW");
        uint256 hvt     = vm.envUint("HIGH_VALUE_THRESHOLD");
        bytes memory cosign = vm.envBytes("ROOT_COSIGN");

        vm.startBroadcast();

        PQCRegistry registry = new PQCRegistry(senatus);
        CompositeVerifier verifier = new CompositeVerifier(address(registry));
        CircuitBreaker breaker = new CircuitBreaker(senatus, window, ceiling);
        HardenedSettlement settlement =
            new HardenedSettlement(address(breaker), address(verifier), hvt);

        // Seed canonical suites. Tier 0/1 span >=2 families (registry enforces this).
        _seedSuites(registry, cosign);

        vm.stopBroadcast();

        console.log("PQCRegistry:       ", address(registry));
        console.log("CompositeVerifier: ", address(verifier));
        console.log("CircuitBreaker:    ", address(breaker));
        console.log("HardenedSettlement:", address(settlement));
    }

    function _seedSuites(PQCRegistry registry, bytes memory cosign) internal {
        // CP2048-SIG-0 : SLH-DSA + ML-DSA-87 + Ed25519  (Tier 0 roots)
        uint8[] memory s0 = new uint8[](3);
        s0[0] = SLH_DSA_256s; s0[1] = ML_DSA_87; s0[2] = ED25519;
        registry.registerSuite(bytes4("S0"), s0, 0, cosign);

        // CP2048-SIG-1 : ML-DSA-87 + Ed25519  (Tier 1 validators/governance)
        uint8[] memory s1 = new uint8[](2);
        s1[0] = ML_DSA_87; s1[1] = ED25519;
        registry.registerSuite(bytes4("S1"), s1, 1, cosign);

        // CP2048-SIG-2 : ML-DSA-65 + Ed25519  (Tier 2 agents/identity)
        uint8[] memory s2 = new uint8[](2);
        s2[0] = ML_DSA_65; s2[1] = ED25519;
        registry.registerSuite(bytes4("S2"), s2, 2, cosign);

        // CP2048-SIG-3 : ML-DSA-44 (Tier 3 ephemeral, single-family allowed)
        uint8[] memory s3 = new uint8[](1);
        s3[0] = ML_DSA_44;
        registry.registerSuite(bytes4("S3"), s3, 3, cosign);
    }
}
