// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.20;

import "forge-std/Test.sol";
import "../src/pqc/PQCRegistry.sol";

/// @dev Minimal Senatus mock: root quorum accepts any non-empty co-sign;
///      supermajority accepts any non-zero proposal. Real impl verifies SLH-DSA.
contract SenatusMock is ISenatus {
    mapping(address => bool) public senator;
    function setSenator(address a, bool v) external { senator[a] = v; }
    function isRootQuorum(bytes calldata slhDsaCosign) external pure returns (bool) {
        return slhDsaCosign.length > 0;
    }
    function isSenator(address a) external view returns (bool) { return senator[a]; }
    function passedSupermajority(bytes32 p) external pure returns (bool) {
        return p != bytes32(0);
    }
}

contract PQCRegistryTest is Test {
    PQCRegistry reg;
    SenatusMock senatus;

    // algorithm IDs as seeded in the registry
    uint8 constant ED25519      = 1;
    uint8 constant ML_DSA_44    = 2;
    uint8 constant ML_DSA_65    = 3;
    uint8 constant ML_DSA_87    = 4;
    uint8 constant SLH_DSA_256s = 5;

    bytes constant COSIGN = hex"deadbeef"; // non-empty => root quorum in mock

    function setUp() public {
        senatus = new SenatusMock();
        senatus.setSenator(address(this), true);
        reg = new PQCRegistry(address(senatus));
    }

    // ---- THE CORE INVARIANT: no single-family root ----

    function test_RejectsSingleFamilyTier0() public {
        // ML-DSA-87 alone = one family (LATTICE) at Tier 0 -> must revert.
        uint8[] memory comps = new uint8[](1);
        comps[0] = ML_DSA_87;
        vm.expectRevert(PQCRegistry.SingleFamilyRootForbidden.selector);
        reg.registerSuite(bytes4("S0x"), comps, 0, COSIGN);
    }

    function test_RejectsSingleFamilyTier1() public {
        // Two lattice algorithms still = ONE family -> still forbidden at Tier 1.
        uint8[] memory comps = new uint8[](2);
        comps[0] = ML_DSA_87;
        comps[1] = ML_DSA_65;
        vm.expectRevert(PQCRegistry.SingleFamilyRootForbidden.selector);
        reg.registerSuite(bytes4("S1x"), comps, 1, COSIGN);
    }

    function test_AcceptsMultiFamilyTier0() public {
        // hash + lattice + classical = three families -> allowed.
        uint8[] memory comps = new uint8[](3);
        comps[0] = SLH_DSA_256s; // HASH
        comps[1] = ML_DSA_87;    // LATTICE
        comps[2] = ED25519;      // CLASSICAL
        reg.registerSuite(bytes4("S0"), comps, 0, COSIGN);

        assertTrue(reg.isUsable(bytes4("S0")));
        PQCRegistry.Suite memory s = reg.getSuite(bytes4("S0"));
        assertEq(s.components.length, 3);
        assertEq(s.tier, 0);
    }

    function test_AcceptsMultiFamilyTier1() public {
        uint8[] memory comps = new uint8[](2);
        comps[0] = ML_DSA_87; // LATTICE
        comps[1] = ED25519;   // CLASSICAL
        reg.registerSuite(bytes4("S1"), comps, 1, COSIGN);
        assertTrue(reg.isUsable(bytes4("S1")));
    }

    function test_AllowsSingleFamilyAtTier3() public {
        // Ephemeral leaf: pure PQ single family is permitted (tier > 1).
        uint8[] memory comps = new uint8[](1);
        comps[0] = ML_DSA_44;
        reg.registerSuite(bytes4("S3"), comps, 3, COSIGN);
        assertTrue(reg.isUsable(bytes4("S3")));
    }

    // ---- authority ----

    function test_NonRootCannotRegister() public {
        uint8[] memory comps = new uint8[](2);
        comps[0] = ML_DSA_87; comps[1] = ED25519;
        vm.expectRevert(PQCRegistry.NotRootAuthority.selector);
        reg.registerSuite(bytes4("S1"), comps, 1, ""); // empty cosign => not root
    }

    // ---- deprecation & sunset (agility without redeploy) ----

    function test_DeprecationDisablesUsability() public {
        uint8[] memory comps = new uint8[](2);
        comps[0] = ML_DSA_87; comps[1] = ED25519;
        reg.registerSuite(bytes4("S1"), comps, 1, COSIGN);
        assertTrue(reg.isUsable(bytes4("S1")));

        reg.deprecateSuite(bytes4("S1"), 0, keccak256("prop-1"));
        assertFalse(reg.isUsable(bytes4("S1")));
    }

    function test_SunsetEpochExpiresSuite() public {
        uint8[] memory comps = new uint8[](2);
        comps[0] = ML_DSA_87; comps[1] = ED25519;
        reg.registerSuite(bytes4("S1"), comps, 1, COSIGN);

        uint64 sunset = uint64(block.timestamp + 30 days);
        reg.deprecateSuite(bytes4("S1"), sunset, keccak256("prop-2"));

        // still readable, but not usable after sunset
        vm.warp(block.timestamp + 31 days);
        assertFalse(reg.isUsable(bytes4("S1")));
    }

    function test_CannotDoubleRegister() public {
        uint8[] memory comps = new uint8[](2);
        comps[0] = ML_DSA_87; comps[1] = ED25519;
        reg.registerSuite(bytes4("S1"), comps, 1, COSIGN);
        vm.expectRevert(PQCRegistry.SuiteExists.selector);
        reg.registerSuite(bytes4("S1"), comps, 1, COSIGN);
    }

    // ---- fuzz: any all-lattice component set at tier<=1 must revert ----

    function testFuzz_AllLatticeRootAlwaysReverts(uint8 n) public {
        n = uint8(bound(n, 1, 4));
        uint8[] memory comps = new uint8[](n);
        for (uint8 i = 0; i < n; i++) {
            comps[i] = ML_DSA_87; // all LATTICE
        }
        vm.expectRevert(PQCRegistry.SingleFamilyRootForbidden.selector);
        reg.registerSuite(bytes4("Sf"), comps, 1, COSIGN);
    }
}
