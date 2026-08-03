// SPDX-License-Identifier: Apache-2.0
pragma solidity 0.8.26;

import {Test} from "forge-std/Test.sol";
import {BridgeHandler} from "./handlers/BridgeHandler.sol";

/// @notice Complete-closure falsification suite for Drawbridge v2.
///         The bridge must never stay open: every crossing terminates as
///         confirmed+closed, bounced, or slammed — and closes COMPLETELY (record deleted).
contract ClosureInvariants is Test {
    BridgeHandler h;

    function setUp() public {
        h = new BridgeHandler();
        targetContract(address(h));
    }

    /// COMPLETE CLOSURE: after draining queues + warping past RECLAIM_DELAY,
    /// zero OPEN records survive and inFlight == 0.
    function invariant_BridgeAlwaysCloses() public view {
        assertTrue(h.noEternalOpenGates());
    }

    /// No double-mint: for every id, (dst minted) XOR (src refunded), never both.
    function invariant_ExactlyOnce() public view {
        assertTrue(h.exactlyOnceDelivery());
    }

    /// Supply conservation including flight: Σ supply + Σ inFlight is constant.
    function invariant_ConservationWithFlight() public view {
        assertTrue(h.conserved());
    }

    /// Moat outflow ≤ Σ quoted ACK fees; every other moat path is inflow-only.
    function invariant_MoatPurposeLocked() public view {
        assertTrue(h.moatOnlyPaysAcks());
    }

    /// RoyalT: accrued + renderedTotal == Σ royal shares.
    function invariant_RoyalExact() public view {
        assertTrue(h.royaltyConserved());
    }

    /// Golden toll to the 18th decimal on every raise (0.001 → 0.001161803398874989).
    function testFuzz_GoldenToll(uint128 callFee) public view {
        h.assertGolden(callFee);
    }
}
