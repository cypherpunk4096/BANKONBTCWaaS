// SPDX-License-Identifier: Apache-2.0
pragma solidity 0.8.26;

import {Test} from "forge-std/Test.sol";
import {PAI} from "../src/pai.sol";
import {MockUSDC} from "./mocks/MockUSDC.sol";

/// @notice Popperian falsification suite for PAI: state the invariant, let Forge break it.
contract PAITest is Test {
    PAI pai;
    MockUSDC usdc;

    function setUp() public {
        usdc = new MockUSDC();
        pai = new PAI(address(usdc));
    }

    // ── unit ─────────────────────────────────────────────────────────────────
    function test_MintExactParity() public {
        usdc.mint(address(this), 1_000000); // 1.000000 USDC
        usdc.approve(address(pai), 1_000000);
        uint256 out = pai.mint(1_000000);
        assertEq(out, 1e18); // 1.000000000000000000 PAI
        assertTrue(pai.backingOk());
    }

    function test_RedeemNonExactReverts() public {
        usdc.mint(address(this), 1_000000);
        usdc.approve(address(pai), 1_000000);
        pai.mint(1_000000);
        vm.expectRevert("PAI/non-exact");
        pai.redeem(1e18 + 1); // dust that would round the reserve
    }

    function test_RateIsPar() public view {
        assertEq(pai.rate(), 1e18);
    }

    // ── fuzz ─────────────────────────────────────────────────────────────────
    function testFuzz_MintRedeemRoundTrip(uint96 amt) public {
        vm.assume(amt > 0);
        usdc.mint(address(this), amt);
        usdc.approve(address(pai), amt);
        uint256 out = pai.mint(amt);
        assertEq(out, uint256(amt) * 1e12);
        uint256 back = pai.redeem(out);
        assertEq(back, amt); // exact round-trip, no leakage
        assertTrue(pai.backingOk());
    }

    // ── invariant: reserve*1e12 == totalSupply, ALWAYS ────────────────────────
    function invariant_FullyBacked() public view {
        assertTrue(pai.backingOk());
    }
}
