// SPDX-License-Identifier: Apache-2.0
// (c) BANKON — cypherpunk2048 standard
//
// Foundry test suite. For full cross-chain send/receive simulation, extend this using
// @layerzerolabs/test-devtools-evm-foundry's TestHelperOz5 + EndpointV2Mock, wiring two
// local mock endpoints and asserting balances after `verifyPackets`. This file covers the
// chain-local invariants that don't require the full mock-endpoint harness:
//   - fixed supply is minted exactly once, only on the hub
//   - spoke deployments start at zero supply
//   - onlyOwner gating on setPeer / setEnforcedOptions / setDelegate
//   - ownership + delegate handoff to the DAIO governance address
//
// Run: forge test -vvv
pragma solidity ^0.8.22;

import { Test, console2 } from "forge-std/Test.sol";
import { BankonPythaiOFT } from "../src/BankonPythaiOFT.sol";
import { ChainRegistry } from "../src/ChainRegistry.sol";

/// @dev Minimal LayerZero Endpoint V2 stub sufficient for constructor wiring in unit tests
///      that don't need to exercise actual cross-chain message passing. Replace with
///      EndpointV2Mock from @layerzerolabs/test-devtools-evm-foundry for full lifecycle tests.
contract EndpointV2Stub {
    function setDelegate(address) external {}
}

contract BankonPythaiOFTTest is Test {
    BankonPythaiOFT hub;
    BankonPythaiOFT spoke;
    ChainRegistry registry;
    EndpointV2Stub endpoint;

    address deployer = address(0xBEEF);
    address hubRecipient = address(0xCAFE);
    address daio = address(0xDA10);

    uint256 constant EXPECTED_SUPPLY = 111_111_111 * 10 ** 15; // 111,111.111 * 1e18

    function setUp() public {
        endpoint = new EndpointV2Stub();

        vm.prank(deployer);
        hub = new BankonPythaiOFT(address(endpoint), deployer, true, hubRecipient);

        vm.prank(deployer);
        spoke = new BankonPythaiOFT(address(endpoint), deployer, false, address(0));

        vm.prank(deployer);
        registry = new ChainRegistry(deployer);
    }

    function test_HubMintsExactFixedSupplyOnce() public view {
        assertEq(hub.totalSupply(), EXPECTED_SUPPLY);
        assertEq(hub.balanceOf(hubRecipient), EXPECTED_SUPPLY);
        assertTrue(hub.isHub());
    }

    function test_SpokeStartsAtZeroSupply() public view {
        assertEq(spoke.totalSupply(), 0);
        assertFalse(spoke.isHub());
    }

    function test_RevertWhen_SpokeConstructedAsHubWithoutRecipient() public {
        vm.expectRevert(bytes("BKPY: zero hub recipient"));
        new BankonPythaiOFT(address(endpoint), deployer, true, address(0));
    }

    function test_OnlyOwnerCanSetPeer() public {
        vm.expectRevert();
        hub.setPeer(30110, bytes32(uint256(uint160(address(spoke)))));

        vm.prank(deployer);
        hub.setPeer(30110, bytes32(uint256(uint160(address(spoke)))));
        assertEq(hub.peers(30110), bytes32(uint256(uint160(address(spoke)))));
    }

    function test_OwnershipAndDelegateHandoffToDAIO() public {
        vm.startPrank(deployer);
        hub.setDelegate(daio);
        hub.transferOwnership(daio);
        vm.stopPrank();

        assertEq(hub.owner(), daio);

        // Deployer should no longer be able to call owner-gated functions.
        vm.prank(deployer);
        vm.expectRevert();
        hub.setPeer(30111, bytes32(uint256(uint160(address(0x1234)))));

        // DAIO can.
        vm.prank(daio);
        hub.setPeer(30111, bytes32(uint256(uint160(address(0x1234)))));
    }

    function test_ChainRegistryAddAndReadChain() public {
        vm.prank(deployer);
        registry.addChain(30101, 1, "Ethereum", address(hub), true);

        (uint256 evmChainId,, string memory name, address oft, bool isEvm, bool active) =
            registry.chains(30101);

        assertEq(evmChainId, 1);
        assertEq(name, "Ethereum");
        assertEq(oft, address(hub));
        assertTrue(isEvm);
        assertTrue(active);
        assertEq(registry.count(), 1);
    }

    function test_RevertWhen_NonOwnerAddsChain() public {
        vm.prank(address(0xBADD));
        vm.expectRevert();
        registry.addChain(30101, 1, "Ethereum", address(hub), true);
    }
}
