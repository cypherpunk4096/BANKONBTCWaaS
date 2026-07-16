// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {ARRBY} from "../src/ARRBY.sol";
import {MockERC20, MockPool, MockRouter, MockNoReturnToken} from "./mocks/Mocks.sol";

contract ARRBYTest is Test {
    ARRBY arb;
    MockPool pool;
    MockERC20 usdc;
    MockERC20 weth;
    MockRouter routerA; // USDC -> WETH
    MockRouter routerB; // WETH -> USDC
    address owner = address(0xB055);
    address stranger = address(0xBAD);

    function setUp() public {
        pool = new MockPool();
        usdc = new MockERC20("USD Coin", "USDC");
        weth = new MockERC20("Wrapped Ether", "WETH");
        arb = new ARRBY(address(pool), owner);
    }

    function _path(address a, address b) internal pure returns (address[] memory p) {
        p = new address[](2);
        p[0] = a;
        p[1] = b;
    }

    function test_ProfitableArbitrageSucceedsAndPaysOwnerViaWithdraw() public {
        // routerA: 3000 USDC -> 1 WETH (rate favors buying WETH cheap)
        routerA = new MockRouter(1, 3000, address(weth));
        // routerB: 1 WETH -> 3050 USDC (sell WETH high elsewhere)
        routerB = new MockRouter(3050, 1, address(usdc));

        ARRBY.ArbParams memory p = ARRBY.ArbParams({
            routerA: address(routerA),
            routerB: address(routerB),
            pathAtoB: _path(address(usdc), address(weth)),
            pathBtoA: _path(address(weth), address(usdc)),
            minProfit: 10e18,
            deadline: block.timestamp + 300
        });

        uint256 amount = 3000e18;

        vm.prank(owner);
        arb.initiateArbitrage(address(usdc), amount, p);

        uint256 balAfter = usdc.balanceOf(address(arb));
        assertGt(balAfter, 0, "contract should hold profit after repay");

        vm.prank(owner);
        arb.withdraw(address(usdc), owner, balAfter);
        assertEq(usdc.balanceOf(owner), balAfter);
    }

    function test_RevertWhen_Unprofitable() public {
        // Flat rates both ways with a small premium make this a losing trade.
        routerA = new MockRouter(1, 1, address(weth));
        routerB = new MockRouter(1, 1, address(usdc));

        ARRBY.ArbParams memory p = ARRBY.ArbParams({
            routerA: address(routerA),
            routerB: address(routerB),
            pathAtoB: _path(address(usdc), address(weth)),
            pathBtoA: _path(address(weth), address(usdc)),
            minProfit: 1, // even 1 wei of profit is impossible once premium is charged
            deadline: block.timestamp + 300
        });

        vm.prank(owner);
        vm.expectRevert(); // Unprofitable(...)
        arb.initiateArbitrage(address(usdc), 1000e18, p);
    }

    function test_RevertWhen_NonOwnerInitiates() public {
        routerA = new MockRouter(1, 3000, address(weth));
        routerB = new MockRouter(3050, 1, address(usdc));

        ARRBY.ArbParams memory p = ARRBY.ArbParams({
            routerA: address(routerA),
            routerB: address(routerB),
            pathAtoB: _path(address(usdc), address(weth)),
            pathBtoA: _path(address(weth), address(usdc)),
            minProfit: 0,
            deadline: block.timestamp + 300
        });

        vm.prank(stranger);
        vm.expectRevert(ARRBY.NotOwner.selector);
        arb.initiateArbitrage(address(usdc), 1000e18, p);
    }

    function test_RevertWhen_PoolCallbackSpoofed() public {
        ARRBY.ArbParams memory p = ARRBY.ArbParams({
            routerA: address(0),
            routerB: address(0),
            pathAtoB: _path(address(usdc), address(weth)),
            pathBtoA: _path(address(weth), address(usdc)),
            minProfit: 0,
            deadline: block.timestamp + 300
        });
        bytes memory params = abi.encode(p, owner);

        vm.expectRevert(ARRBY.NotPool.selector);
        arb.executeOperation(address(usdc), 1000e18, 1e18, address(arb), params);
    }

    function test_OwnerCanRenounce() public {
        vm.prank(owner);
        arb.renounceOwnership();
        assertEq(arb.owner(), address(0));
    }

    function test_RevertWhen_PathDoesNotStartAtBorrowedAsset() public {
        routerA = new MockRouter(1, 3000, address(weth));
        routerB = new MockRouter(3050, 1, address(usdc));

        // pathAtoB starts at WETH instead of the borrowed asset (USDC) — audit fix catches this.
        ARRBY.ArbParams memory p = ARRBY.ArbParams({
            routerA: address(routerA),
            routerB: address(routerB),
            pathAtoB: _path(address(weth), address(usdc)),
            pathBtoA: _path(address(weth), address(usdc)),
            minProfit: 0,
            deadline: block.timestamp + 300
        });

        vm.prank(owner);
        vm.expectRevert(ARRBY.BadPath.selector);
        arb.initiateArbitrage(address(usdc), 1000e18, p);
    }

    function test_RevertWhen_PathsDoNotJoinAtSameIntermediate() public {
        routerA = new MockRouter(1, 3000, address(weth));
        routerB = new MockRouter(3050, 1, address(usdc));
        MockERC20 dai = new MockERC20("Dai", "DAI");

        // pathAtoB ends in WETH but pathBtoA starts from DAI — mismatched intermediate.
        ARRBY.ArbParams memory p = ARRBY.ArbParams({
            routerA: address(routerA),
            routerB: address(routerB),
            pathAtoB: _path(address(usdc), address(weth)),
            pathBtoA: _path(address(dai), address(usdc)),
            minProfit: 0,
            deadline: block.timestamp + 300
        });

        vm.prank(owner);
        vm.expectRevert(ARRBY.BadPath.selector);
        arb.initiateArbitrage(address(usdc), 1000e18, p);
    }

    function test_WithdrawWorksWithNoReturnToken() public {
        // v1.2 regression: USDT-style tokens return nothing from transfer.
        // Under the old IERC20Min-typed call this would revert on return-data
        // decoding; _safeTransfer must handle it.
        MockNoReturnToken usdt = new MockNoReturnToken();
        usdt.mint(address(arb), 500e6);

        vm.prank(owner);
        arb.withdraw(address(usdt), owner, 500e6);
        assertEq(usdt.balanceOf(owner), 500e6);
    }

    function test_RevertWhen_DeadlineAlreadyExpired() public {
        routerA = new MockRouter(1, 3000, address(weth));
        routerB = new MockRouter(3050, 1, address(usdc));
        vm.warp(1_000_000);

        ARRBY.ArbParams memory p = ARRBY.ArbParams({
            routerA: address(routerA),
            routerB: address(routerB),
            pathAtoB: _path(address(usdc), address(weth)),
            pathBtoA: _path(address(weth), address(usdc)),
            minProfit: 0,
            deadline: block.timestamp - 1 // already in the past
        });

        vm.prank(owner);
        vm.expectRevert(abi.encodeWithSelector(ARRBY.Expired.selector, block.timestamp - 1, block.timestamp));
        arb.initiateArbitrage(address(usdc), 1000e18, p);
    }

    function test_RecordsCumulativeProfitOnSuccess() public {
        routerA = new MockRouter(1, 3000, address(weth));
        routerB = new MockRouter(3050, 1, address(usdc));
        ARRBY.ArbParams memory p = ARRBY.ArbParams({
            routerA: address(routerA), routerB: address(routerB),
            pathAtoB: _path(address(usdc), address(weth)),
            pathBtoA: _path(address(weth), address(usdc)),
            minProfit: 0, deadline: block.timestamp + 300
        });
        vm.prank(owner);
        arb.initiateArbitrage(address(usdc), 3000e18, p);
        // 3050 gross - 3000 principal - 1.5 premium (5bps) = 48.5 profit
        assertEq(arb.cumulativeProfit(address(usdc)), 48.5e18, "cumulative profit tracked on-chain");
    }

    function test_TreasurySweepSplitsBetweenTreasuryAndOwner() public {
        address treasury = address(0x7EA5);
        vm.prank(owner);
        arb.setTreasury(treasury, 2500); // 25% to treasury, 75% to owner

        // seed the contract with 100 USDC of "profit"
        usdc.mint(address(arb), 100e18);

        vm.prank(owner);
        arb.sweepTreasury(address(usdc));

        assertEq(usdc.balanceOf(treasury), 25e18, "treasury gets 25%");
        assertEq(usdc.balanceOf(owner), 75e18, "owner gets remainder");
        assertEq(usdc.balanceOf(address(arb)), 0, "contract emptied");
    }

    function test_RevertWhen_NotionalExceedsCap() public {
        vm.prank(owner);
        arb.setMaxNotional(1000e18);

        routerA = new MockRouter(1, 3000, address(weth));
        routerB = new MockRouter(3050, 1, address(usdc));
        ARRBY.ArbParams memory p = ARRBY.ArbParams({
            routerA: address(routerA), routerB: address(routerB),
            pathAtoB: _path(address(usdc), address(weth)),
            pathBtoA: _path(address(weth), address(usdc)),
            minProfit: 0, deadline: block.timestamp + 300
        });
        vm.prank(owner);
        vm.expectRevert(abi.encodeWithSelector(ARRBY.NotionalTooLarge.selector, 2000e18, 1000e18));
        arb.initiateArbitrage(address(usdc), 2000e18, p);
    }

    function test_RevertWhen_TreasuryBpsTooHigh() public {
        vm.prank(owner);
        vm.expectRevert(ARRBY.BadBps.selector);
        arb.setTreasury(address(0x7EA5), 10001);
    }

    function test_RevertWhen_ConstructedWithZeroAddress() public {
        vm.expectRevert(ARRBY.ZeroAddress.selector);
        new ARRBY(address(0), owner);

        vm.expectRevert(ARRBY.ZeroAddress.selector);
        new ARRBY(address(pool), address(0));
    }
}
