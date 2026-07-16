// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {ARRBY3156} from "../src/ARRBYFlash3156.sol";
import {MockERC20, MockRouter, MockERC3156Lender} from "./mocks/Mocks.sol";

contract ARRBY3156Test is Test {
    ARRBY3156 arb;
    MockERC3156Lender lender;
    MockERC20 usdc;
    MockERC20 weth;
    MockRouter routerA;
    MockRouter routerB;
    address owner = address(0xB055);
    address stranger = address(0xBAD);

    function setUp() public {
        lender = new MockERC3156Lender(); // fee defaults to 0 — DssFlash-style
        usdc = new MockERC20("USD Coin", "USDC");
        weth = new MockERC20("Wrapped Ether", "WETH");
        arb = new ARRBY3156(owner);
    }

    function _path(address a, address b) internal pure returns (address[] memory p) {
        p = new address[](2);
        p[0] = a;
        p[1] = b;
    }

    function test_ZeroFeeArbitrageSucceeds() public {
        routerA = new MockRouter(1, 3000, address(weth));   // 3000 USDC -> 1 WETH
        routerB = new MockRouter(3010, 1, address(usdc));    // 1 WETH -> 3010 USDC

        ARRBY3156.ArbParams memory p = ARRBY3156.ArbParams({
            lender: address(lender),
            routerA: address(routerA),
            routerB: address(routerB),
            pathAtoB: _path(address(usdc), address(weth)),
            pathBtoA: _path(address(weth), address(usdc)),
            minProfit: 5e18,
            deadline: block.timestamp + 300
        });

        vm.prank(owner);
        arb.initiateArbitrage(address(usdc), 3000e18, p);

        // With 0% fee, the entire 10 USDC spread (minus none) is profit.
        uint256 bal = usdc.balanceOf(address(arb));
        assertEq(bal, 10e18, "0-fee lender should leave the full spread as profit");

        vm.prank(owner);
        arb.withdraw(address(usdc), owner, bal);
        assertEq(usdc.balanceOf(owner), bal);
    }

    function test_RevertWhen_Unprofitable() public {
        routerA = new MockRouter(1, 1, address(weth));
        routerB = new MockRouter(1, 1, address(usdc));

        ARRBY3156.ArbParams memory p = ARRBY3156.ArbParams({
            lender: address(lender),
            routerA: address(routerA),
            routerB: address(routerB),
            pathAtoB: _path(address(usdc), address(weth)),
            pathBtoA: _path(address(weth), address(usdc)),
            minProfit: 1,
            deadline: block.timestamp + 300
        });

        vm.prank(owner);
        vm.expectRevert();
        arb.initiateArbitrage(address(usdc), 1000e18, p);
    }

    function test_RevertWhen_NonOwnerInitiates() public {
        routerA = new MockRouter(1, 3000, address(weth));
        routerB = new MockRouter(3010, 1, address(usdc));

        ARRBY3156.ArbParams memory p = ARRBY3156.ArbParams({
            lender: address(lender),
            routerA: address(routerA),
            routerB: address(routerB),
            pathAtoB: _path(address(usdc), address(weth)),
            pathBtoA: _path(address(weth), address(usdc)),
            minProfit: 0,
            deadline: block.timestamp + 300
        });

        vm.prank(stranger);
        vm.expectRevert(ARRBY3156.NotOwner.selector);
        arb.initiateArbitrage(address(usdc), 1000e18, p);
    }

    function test_RevertWhen_CallbackSpoofed() public {
        ARRBY3156.ArbParams memory p = ARRBY3156.ArbParams({
            lender: address(lender),
            routerA: address(0xAAA1),
            routerB: address(0xAAA2),
            pathAtoB: _path(address(usdc), address(weth)),
            pathBtoA: _path(address(weth), address(usdc)),
            minProfit: 0,
            deadline: block.timestamp + 300
        });
        bytes memory params = abi.encode(p, owner);

        // called directly, not via the real lender — msg.sender != arb.lender
        vm.expectRevert(ARRBY3156.NotLender.selector);
        arb.onFlashLoan(address(arb), address(usdc), 1000e18, 0, params);
    }

    function test_RevertWhen_NonZeroFeeStillProfitable() public {
        // Confirms the contract handles a nonzero-fee ERC-3156 lender too —
        // 3156 isn't DssFlash-only, any compliant lender works.
        lender.setFee(2e18); // 2 USDC flat fee for this test
        routerA = new MockRouter(1, 3000, address(weth));
        routerB = new MockRouter(3010, 1, address(usdc));

        ARRBY3156.ArbParams memory p = ARRBY3156.ArbParams({
            lender: address(lender),
            routerA: address(routerA),
            routerB: address(routerB),
            pathAtoB: _path(address(usdc), address(weth)),
            pathBtoA: _path(address(weth), address(usdc)),
            minProfit: 0,
            deadline: block.timestamp + 300
        });

        vm.prank(owner);
        arb.initiateArbitrage(address(usdc), 3000e18, p);

        assertEq(usdc.balanceOf(address(arb)), 8e18, "10 USDC spread minus 2 USDC fee = 8 USDC profit");
    }

    function test_SupportsERC165AndFlashBorrowerInterface() public view {
        assertTrue(arb.supportsInterface(0x01ffc9a7), "ERC-165");
        assertTrue(arb.supportsInterface(0x23e30c8b), "ERC-3156 borrower");
        assertFalse(arb.supportsInterface(0xdeadbeef), "unknown iface");
    }

    function test_RevertWhen_DeadlineAlreadyExpired() public {
        routerA = new MockRouter(1, 3000, address(weth));
        routerB = new MockRouter(3010, 1, address(usdc));
        vm.warp(1_000_000);

        ARRBY3156.ArbParams memory p = ARRBY3156.ArbParams({
            lender: address(lender),
            routerA: address(routerA),
            routerB: address(routerB),
            pathAtoB: _path(address(usdc), address(weth)),
            pathBtoA: _path(address(weth), address(usdc)),
            minProfit: 0,
            deadline: block.timestamp - 1
        });

        vm.prank(owner);
        vm.expectRevert(abi.encodeWithSelector(ARRBY3156.Expired.selector, block.timestamp - 1, block.timestamp));
        arb.initiateArbitrage(address(usdc), 1000e18, p);
    }

    function test_TreasuryAndCumulativeProfitWithZeroFeeLender() public {
        routerA = new MockRouter(1, 3000, address(weth));
        routerB = new MockRouter(3010, 1, address(usdc));
        ARRBY3156.ArbParams memory p = ARRBY3156.ArbParams({
            lender: address(lender), routerA: address(routerA), routerB: address(routerB),
            pathAtoB: _path(address(usdc), address(weth)),
            pathBtoA: _path(address(weth), address(usdc)),
            minProfit: 0, deadline: block.timestamp + 300
        });
        vm.prank(owner);
        arb.initiateArbitrage(address(usdc), 3000e18, p);
        assertEq(arb.cumulativeProfit(address(usdc)), 10e18, "0-fee: full 10 USDC spread recorded");

        address treasury = address(0x7EA5);
        vm.prank(owner);
        arb.setTreasury(treasury, 4000); // 40% to treasury
        vm.prank(owner);
        arb.sweepTreasury(address(usdc));
        assertEq(usdc.balanceOf(treasury), 4e18, "treasury 40%");
        assertEq(usdc.balanceOf(owner), 6e18, "owner 60%");
    }

    function test_RevertWhen_NotionalExceedsCap() public {
        vm.prank(owner);
        arb.setMaxNotional(1000e18);
        routerA = new MockRouter(1, 3000, address(weth));
        routerB = new MockRouter(3010, 1, address(usdc));
        ARRBY3156.ArbParams memory p = ARRBY3156.ArbParams({
            lender: address(lender), routerA: address(routerA), routerB: address(routerB),
            pathAtoB: _path(address(usdc), address(weth)),
            pathBtoA: _path(address(weth), address(usdc)),
            minProfit: 0, deadline: block.timestamp + 300
        });
        vm.prank(owner);
        vm.expectRevert(abi.encodeWithSelector(ARRBY3156.NotionalTooLarge.selector, 2000e18, 1000e18));
        arb.initiateArbitrage(address(usdc), 2000e18, p);
    }
}
