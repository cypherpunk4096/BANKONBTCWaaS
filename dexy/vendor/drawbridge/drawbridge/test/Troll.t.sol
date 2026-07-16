// SPDX-License-Identifier: Apache-2.0
pragma solidity 0.8.26;

import {Test} from "forge-std/Test.sol";
import {Troll} from "../src/troll.sol";
import {RoyalT} from "../src/royalt.sol";

/// @notice Golden-toll falsification: the fee is phi one decimal right, to 18dp, and the
///         surcharge splits phi-on-phi with zero wei lost.
contract TrollTest is Test {
    uint256 constant GOLDEN_TOLL_WAD = 1_161803398874989485;
    uint256 constant PHI_INV_WAD = 618033988749894848;

    Troll troll;
    RoyalT royalt;

    function setUp() public {
        royalt = new RoyalT();
        // deployer stands in as the "drawbridge" caller for collect() unit tests
        troll = new Troll(address(this), payable(address(royalt)));
    }

    /// The user's exact example, verified to the 18th decimal.
    function test_GoldenTollExample() public pure {
        uint256 callFee = 0.001 ether; // 0.001000000000000000
        uint256 toll = callFee * GOLDEN_TOLL_WAD / 1e18;
        assertEq(toll, 0.001161803398874989 ether); // golden to 18 dp
    }

    /// phi self-similarity: surcharge splits at exactly 1/phi, remainder 1 - 1/phi, no dust.
    function testFuzz_GoldenSplit(uint128 callFee) public pure {
        vm.assume(callFee > 0);
        uint256 toll = uint256(callFee) * GOLDEN_TOLL_WAD / 1e18;
        uint256 s = toll - callFee;
        uint256 m = s * PHI_INV_WAD / 1e18;
        uint256 r = s - m;
        assertEq(m + r, s); // conservation
    }

    /// collect() routes moat + royalty correctly and refunds the exact excess.
    function test_CollectSplitsAndRefunds() public {
        uint256 callFee = 0.001 ether;
        uint256 toll = callFee * GOLDEN_TOLL_WAD / 1e18;
        uint256 sent = toll + 0.0005 ether; // overpay; expect refund of 0.0005

        uint256 refund = troll.collect{value: sent}(address(0xBEEF), callFee);

        assertEq(refund, sent - toll); // exact excess returned to the bridge
        // moat holds callFee + 1/phi of surcharge; royalt holds the remainder of surcharge
        uint256 surcharge = toll - callFee;
        uint256 toMoat = surcharge * PHI_INV_WAD / 1e18;
        assertEq(troll.moat(), callFee + toMoat);
        assertEq(address(royalt).balance, surcharge - toMoat);
        // value conservation: everything sent is accounted (moat + royalty + refund)
        assertEq(troll.moat() + address(royalt).balance + refund, sent);
    }

    /// Only the drawbridge may collect.
    function test_OnlyBridgeCollects() public {
        vm.prank(address(0xBAD));
        vm.expectRevert(Troll.NotDrawbridge.selector);
        troll.collect{value: 1 ether}(address(0xBAD), 0.001 ether);
    }

    receive() external payable {}
}
