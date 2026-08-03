// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.20;

import "forge-std/Test.sol";
import "../src/security/CircuitBreaker.sol";

contract SenatusBreakerMock is ISenatusBreaker {
    function passedSupermajority(bytes32 p) external pure returns (bool) {
        return p != bytes32(0);
    }
}

contract CircuitBreakerTest is Test {
    CircuitBreaker cb;
    SenatusBreakerMock senatus;

    uint256 constant WINDOW = 3600;          // 1 hour
    uint256 constant CEILING = 1_000_000;    // max settled per window

    function setUp() public {
        senatus = new SenatusBreakerMock();
        cb = new CircuitBreaker(address(senatus), WINDOW, CEILING);
    }

    // ---- tripwire ----

    function test_IntakeAllowedBelowCeiling() public {
        assertTrue(cb.guardIntake(500_000));
        assertFalse(cb.tripwireLatched());
    }

    function test_TripwireLatchesOverCeiling() public {
        cb.guardIntake(600_000);
        // second intake crosses the ceiling -> latch + revert
        vm.expectRevert(CircuitBreaker.IntakeHalted.selector);
        cb.guardIntake(600_000);
        assertTrue(cb.tripwireLatched());
    }

    function test_LatchedBlocksFurtherIntake() public {
        cb.guardIntake(600_000);
        vm.expectRevert(CircuitBreaker.IntakeHalted.selector);
        cb.guardIntake(600_000);
        // any subsequent intake also reverts while latched
        vm.expectRevert(CircuitBreaker.IntakeHalted.selector);
        cb.guardIntake(1);
    }

    function test_WindowRollResetsAccumulator() public {
        cb.guardIntake(900_000);
        assertFalse(cb.tripwireLatched());
        // roll past the window; accumulator resets, so another 900k is fine
        vm.warp(block.timestamp + WINDOW + 1);
        assertTrue(cb.guardIntake(900_000));
        assertFalse(cb.tripwireLatched());
    }

    // ---- exits ALWAYS allowed ----

    function test_ExitAllowedEvenWhenLatched() public {
        cb.guardIntake(600_000);
        vm.expectRevert(CircuitBreaker.IntakeHalted.selector);
        cb.guardIntake(600_000);
        assertTrue(cb.tripwireLatched());

        // exit must still succeed while halted — users are never trapped
        assertTrue(cb.assertExitAllowed(address(0xBEEF), 123));
    }

    function test_ExitAllowedWhenDeprecated() public {
        cb.deprecateIntake(keccak256("migrate"));
        assertTrue(cb.intakeDeprecated());
        // intake now halted...
        vm.expectRevert(CircuitBreaker.IntakeHalted.selector);
        cb.guardIntake(1);
        // ...but exit still works
        assertTrue(cb.assertExitAllowed(address(0xBEEF), 1));
    }

    // ---- governance ----

    function test_DeprecateRequiresApproval() public {
        vm.expectRevert("not approved");
        cb.deprecateIntake(bytes32(0)); // zero proposal => not approved in mock
    }

    function test_ResetRequiresApprovalAndClearsLatch() public {
        cb.guardIntake(600_000);
        vm.expectRevert(CircuitBreaker.IntakeHalted.selector);
        cb.guardIntake(600_000);
        assertTrue(cb.tripwireLatched());

        vm.expectRevert("not approved");
        cb.reset(bytes32(0));

        cb.reset(keccak256("post-audit"));
        assertFalse(cb.tripwireLatched());
        // intake works again after reset
        assertTrue(cb.guardIntake(100_000));
    }

    // ---- fuzz: cumulative intake within a window can never exceed ceiling silently ----

    function testFuzz_NeverSettlesAboveCeilingWithoutLatch(uint256 a, uint256 b) public {
        a = bound(a, 1, CEILING);
        b = bound(b, 1, CEILING);
        cb.guardIntake(a);
        if (a + b > CEILING) {
            vm.expectRevert(CircuitBreaker.IntakeHalted.selector);
            cb.guardIntake(b);
            assertTrue(cb.tripwireLatched());
        } else {
            assertTrue(cb.guardIntake(b));
            assertFalse(cb.tripwireLatched());
        }
    }
}
