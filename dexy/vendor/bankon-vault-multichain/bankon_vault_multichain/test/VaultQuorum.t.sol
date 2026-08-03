// SPDX-License-Identifier: GPL-3.0-or-later
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {VaultQuorum} from "../contracts/VaultQuorum.sol";

contract VaultQuorumTest is Test {
    address a = address(0xA1);
    address b = address(0xB2);
    address c = address(0xC3);

    bytes32 commit = sha256("dummy-tomb-key-bytes");
    uint256 constant PRIMARY = 1;      // chainid 1 = authoritative
    uint256 constant FB = 7 days;

    function _deploy() internal returns (VaultQuorum) {
        address[] memory o = new address[](3);
        (o[0], o[1], o[2]) = (a, b, c);
        return new VaultQuorum(commit, 2, PRIMARY, FB, o);
    }

    function test_PrimaryUnlocksImmediatelyAtQuorum() public {
        vm.chainId(PRIMARY);
        VaultQuorum q = _deploy();
        vm.prank(a); q.approve();
        assertFalse(q.unlocked());
        vm.prank(b); q.approve();
        assertTrue(q.unlocked());
    }

    function test_NonPrimaryRequiresTimelock() public {
        vm.chainId(10);                // non-primary
        VaultQuorum q = _deploy();
        vm.prank(a); q.approve();
        vm.prank(b); q.approve();
        assertFalse(q.unlocked());     // quorum met, timelock not elapsed
        vm.warp(block.timestamp + FB);
        assertTrue(q.unlocked());      // break-glass ready
    }

    function test_RejectsNonOwner() public {
        vm.chainId(PRIMARY);
        VaultQuorum q = _deploy();
        vm.prank(address(0xDEAD));
        vm.expectRevert(VaultQuorum.NotOwner.selector);
        q.approve();
    }

    function test_NoDoubleApprove() public {
        vm.chainId(PRIMARY);
        VaultQuorum q = _deploy();
        vm.startPrank(a);
        q.approve();
        vm.expectRevert(VaultQuorum.AlreadyApproved.selector);
        q.approve();
        vm.stopPrank();
    }

    /// The integrity backbone: identical commitment regardless of chain.
    function test_CommitmentEqualAcrossChains() public {
        vm.chainId(PRIMARY); VaultQuorum q1 = _deploy();
        vm.chainId(137);     VaultQuorum q2 = _deploy();
        assertEq(q1.commitment(), q2.commitment());
        assertEq(q1.threshold(), q2.threshold());
        assertEq(q1.primaryChainId(), q2.primaryChainId());
    }
}
