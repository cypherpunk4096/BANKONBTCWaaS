// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.26;

import {Test} from "forge-std/Test.sol";
import {LogoRegistry} from "../src/LogoRegistry.sol";
import {RatificationAdapter} from "../src/RatificationAdapter.sol";

contract Treasury3 {
    receive() external payable {}
}

/// The adapter IS the Senatus role on PLR: Senatus (the BONAFIDE executor)
/// calls the adapter, the adapter calls the registry. PLR's SENATUS is set
/// to the adapter address at deploy.
contract RatificationAdapterTest is Test {
    LogoRegistry plr;
    RatificationAdapter adapter;
    Treasury3 aerarium;

    address senatusExec = makeAddr("senatusExecutor"); // BONAFIDE Senatus
    address mindx = makeAddr("mindx");
    address spintrade = makeAddr("desk");
    address alice = makeAddr("alice");
    address mallory = makeAddr("mallory");

    uint256 constant SUB_FEE = 0.05 ether;

    function setUp() public {
        aerarium = new Treasury3();
        address predictedPLR =
            vm.computeCreateAddress(address(this), vm.getNonce(address(this)) + 1);
        adapter = new RatificationAdapter(predictedPLR, senatusExec);
        plr = new LogoRegistry(
            address(adapter), // adapter holds the SENATUS role on the registry
            mindx, address(aerarium), spintrade,
            SUB_FEE, 0.02 ether, 1 ether, "https://rage.pythai.net/plr/"
        );
        assertEq(address(plr), predictedPLR);
        vm.deal(alice, 10 ether);
    }

    function _assessed() internal returns (uint256 id) {
        vm.prank(alice);
        id = plr.submitLogo{value: SUB_FEE}(1, address(0xBEEF), keccak256("logo"), "cid");
        vm.prank(mindx);
        plr.assess(id, "ar://manifest-and-logo");
    }

    function test_ratify_throughAdapter_makesCanonical() public {
        uint256 id = _assessed();
        vm.prank(senatusExec);
        adapter.executeRatify(101, id, "ar://manifest-and-logo", keccak256("tally"));

        (bool verified,,,) = plr.canonicalLogo(1, address(0xBEEF));
        assertTrue(verified);
        assertEq(adapter.verdictCount(), 1);
        (uint256 pid, uint256 tid, bool accepted,,,) = adapter.verdicts(0);
        assertEq(pid, 101);
        assertEq(tid, id);
        assertTrue(accepted);
    }

    function test_reject_throughAdapter_returnsToPending() public {
        uint256 id = _assessed();
        vm.prank(senatusExec);
        adapter.executeReject(102, id, "brand dispute", "ar://m", keccak256("tally"));

        (bool verified,,,) = plr.canonicalLogo(1, address(0xBEEF));
        assertFalse(verified);
        // re-assessment path stays open, adapter records full history
        vm.prank(mindx);
        plr.assess(id, "ar://m2");
        vm.prank(senatusExec);
        adapter.executeRatify(103, id, "ar://m2", keccak256("tally2"));
        assertEq(adapter.historyOf(id).length, 2);
        (verified,,,) = plr.canonicalLogo(1, address(0xBEEF));
        assertTrue(verified);
    }

    function test_onlySenatusExecutor() public {
        uint256 id = _assessed();
        vm.prank(mallory);
        vm.expectRevert(RatificationAdapter.NotSenatus.selector);
        adapter.executeRatify(1, id, "ar://m", bytes32(0));
        vm.prank(mindx); // the agent cannot execute its own docket
        vm.expectRevert(RatificationAdapter.NotSenatus.selector);
        adapter.executeRatify(1, id, "ar://m", bytes32(0));
    }

    function test_directRatify_onRegistry_blockedForAll() public {
        uint256 id = _assessed();
        // nobody, including the Senatus executor, can bypass the adapter
        vm.prank(senatusExec);
        vm.expectRevert(LogoRegistry.NotSenatus.selector);
        plr.ratify(id);
        vm.prank(mindx);
        vm.expectRevert(LogoRegistry.NotSenatus.selector);
        plr.ratify(id);
    }

    function test_ratify_nonAssessed_reverts_throughAdapter() public {
        vm.prank(alice);
        uint256 id = plr.submitLogo{value: SUB_FEE}(1, address(0xBEEF), keccak256("l"), "cid");
        vm.prank(senatusExec); // still Pending — adapter call must bubble the revert
        vm.expectRevert(
            abi.encodeWithSelector(LogoRegistry.BadStatus.selector, LogoRegistry.Status.Pending)
        );
        adapter.executeRatify(1, id, "ar://m", bytes32(0));
    }
}
