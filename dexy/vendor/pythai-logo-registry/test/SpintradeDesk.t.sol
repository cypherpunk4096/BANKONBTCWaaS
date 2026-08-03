// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.26;

import {Test} from "forge-std/Test.sol";
import {LogoRegistry} from "../src/LogoRegistry.sol";
import {SpintradeDesk} from "../src/SpintradeDesk.sol";

contract Treasury2 {
    receive() external payable {}
}

contract SpintradeDeskTest is Test {
    LogoRegistry plr;
    SpintradeDesk desk;
    Treasury2 aerarium;

    address senatus = makeAddr("senatus");
    address mindx = makeAddr("mindx");
    address keeper = makeAddr("spintradeKeeper");
    address alice = makeAddr("alice");
    address mallory = makeAddr("mallory");

    uint256 constant CHAIN_FEE = 1 ether;

    function setUp() public {
        aerarium = new Treasury2();
        // Deploy order: desk needs the registry address, registry needs the
        // desk address. Precompute the registry address (CREATE, nonce+1).
        address predictedPLR = vm.computeCreateAddress(address(this), vm.getNonce(address(this)) + 1);
        desk = new SpintradeDesk(predictedPLR, keeper);
        plr = new LogoRegistry(
            senatus, mindx, address(aerarium), address(desk),
            0.05 ether, 0.02 ether, CHAIN_FEE, "https://rage.pythai.net/plr/"
        );
        assertEq(address(plr), predictedPLR);
        vm.deal(alice, 100 ether);
    }

    function test_endToEnd_onboarding_queuesTicket() public {
        vm.prank(alice);
        plr.onboardChain{value: CHAIN_FEE}(747, "NEWCHAIN", "https://rpc.new.xyz");

        assertEq(desk.ticketCount(), 1);
        assertEq(desk.pendingCount(), 1);
        (uint64 cid, string memory nm, address reg,, bool exec) = desk.tickets(0);
        assertEq(cid, 747);
        assertEq(nm, "NEWCHAIN");
        assertEq(reg, alice);
        assertFalse(exec);
    }

    function test_onlyRegistry_canFile() public {
        vm.prank(mallory);
        vm.expectRevert(SpintradeDesk.NotRegistry.selector);
        desk.onChainOnboarded(1, "fake", mallory);
    }

    function test_keeper_executesTicket() public {
        vm.prank(alice);
        plr.onboardChain{value: CHAIN_FEE}(747, "NEWCHAIN", "rpc");
        vm.prank(keeper);
        desk.markExecuted(0);
        assertEq(desk.pendingCount(), 0);
    }

    function test_nonKeeper_cannotExecute() public {
        vm.prank(alice);
        plr.onboardChain{value: CHAIN_FEE}(747, "N", "rpc");
        vm.prank(mallory);
        vm.expectRevert(SpintradeDesk.NotKeeper.selector);
        desk.markExecuted(0);
    }

    function test_doubleExecute_reverts() public {
        vm.prank(alice);
        plr.onboardChain{value: CHAIN_FEE}(747, "N", "rpc");
        vm.startPrank(keeper);
        desk.markExecuted(0);
        vm.expectRevert(SpintradeDesk.AlreadyExecuted.selector);
        desk.markExecuted(0);
        vm.stopPrank();
    }

    function testFuzz_manyChains_allTicketed(uint8 n) public {
        vm.assume(n > 0 && n <= 25);
        for (uint64 i = 1; i <= n; i++) {
            vm.prank(alice);
            plr.onboardChain{value: CHAIN_FEE}(i, "C", "rpc");
        }
        assertEq(desk.ticketCount(), n);
        assertEq(desk.pendingCount(), n);
    }
}
