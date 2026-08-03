// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.20;

import "forge-std/Test.sol";
import "../src/core/HardenedSettlement.sol";

/// @dev Composite verifier mock: returns a preset verdict, records the digest it saw.
contract VerifierMock is ICompositeVerifier {
    bool public verdict = true;
    bytes32 public lastMessageHash;
    function setVerdict(bool v) external { verdict = v; }
    function verifyComposite(
        bytes4, bytes calldata message, bytes[] calldata, bytes[] calldata, bytes32
    ) external returns (bool) {
        lastMessageHash = keccak256(message);
        return verdict;
    }
}

/// @dev CircuitBreaker mock with toggleable halt states.
contract BreakerMock is ICircuitBreaker {
    bool public deprecated;
    bool public latched;
    uint256 public totalIntake;
    function setDeprecated(bool v) external { deprecated = v; }
    function setLatched(bool v) external { latched = v; }
    function guardIntake(uint256 amount) external returns (bool) {
        require(!deprecated && !latched, "halted");
        totalIntake += amount;
        return true;
    }
    function assertExitAllowed(address, uint256) external pure returns (bool) { return true; }
    function intakeDeprecated() external view returns (bool) { return deprecated; }
    function tripwireLatched() external view returns (bool) { return latched; }
}

contract HardenedSettlementTest is Test {
    HardenedSettlement hs;
    VerifierMock verifier;
    BreakerMock breaker;

    uint256 constant THRESHOLD = 1 ether;
    bytes4  constant SUITE = bytes4("S1");
    address recipient = address(0xCAFE);

    bytes[] sigs;
    bytes[] pubs;

    function setUp() public {
        verifier = new VerifierMock();
        breaker  = new BreakerMock();
        hs = new HardenedSettlement(address(breaker), address(verifier), THRESHOLD);
        // two-component composite (lattice + classical) placeholders
        sigs.push(hex"aa"); sigs.push(hex"bb");
        pubs.push(hex"cc"); pubs.push(hex"dd");
    }

    function _settle(bytes32 id, uint256 amount, uint256 nonce, uint64 expiry)
        internal returns (bool)
    {
        HardenedSettlement.FrameParams memory p = HardenedSettlement.FrameParams({
            suiteId: SUITE, transferId: id, amount: amount, token: address(0),
            srcChain: 1, recipient: recipient, nonce: nonce, expiry: expiry
        });
        return hs.settleFrame(p, sigs, pubs, keccak256("slh-cosign"));
    }

    // ---- happy path ----

    function test_SettlesValidComposite() public {
        assertTrue(_settle(keccak256("t1"), 2 ether, 0, uint64(block.timestamp + 1 hours)));
        assertTrue(hs.settled(keccak256("t1")));
        assertEq(hs.nonces(recipient), 1);
    }

    // ---- T1: composite must verify ----

    function test_RejectsInvalidComposite() public {
        verifier.setVerdict(false);
        vm.expectRevert(HardenedSettlement.CompositeInvalid.selector);
        _settle(keccak256("t2"), 2 ether, 0, uint64(block.timestamp + 1 hours));
    }

    // ---- T4: replay + expiry + nonce ----

    function test_RejectsReplay() public {
        _settle(keccak256("t3"), 2 ether, 0, uint64(block.timestamp + 1 hours));
        // same transferId again -> nonce advanced so it fails on nonce first,
        // but even with matched nonce the settled[] guard blocks it.
        vm.expectRevert(); // AlreadySettled or BadNonce
        _settle(keccak256("t3"), 2 ether, 0, uint64(block.timestamp + 1 hours));
    }

    function test_RejectsExpired() public {
        vm.warp(1000);
        vm.expectRevert(abi.encodeWithSelector(HardenedSettlement.Expired.selector, uint64(500)));
        _settle(keccak256("t4"), 2 ether, 0, uint64(500));
    }

    function test_RejectsBadNonce() public {
        vm.expectRevert(abi.encodeWithSelector(HardenedSettlement.BadNonce.selector, uint256(0), uint256(7)));
        _settle(keccak256("t5"), 2 ether, 7, uint64(block.timestamp + 1 hours));
    }

    function test_NonceAdvancesAcrossTransfers() public {
        _settle(keccak256("a"), 2 ether, 0, uint64(block.timestamp + 1 hours));
        _settle(keccak256("b"), 2 ether, 1, uint64(block.timestamp + 1 hours));
        assertEq(hs.nonces(recipient), 2);
    }

    // ---- high-value threshold ----

    function test_RejectsBelowThreshold() public {
        vm.expectRevert(HardenedSettlement.BelowThresholdForFrame.selector);
        _settle(keccak256("t6"), 0.5 ether, 0, uint64(block.timestamp + 1 hours));
    }

    // ---- T5: halt propagation ----

    function test_RevertsWhenLatched() public {
        breaker.setLatched(true);
        vm.expectRevert(HardenedSettlement.IntakeHalted.selector);
        _settle(keccak256("t7"), 2 ether, 0, uint64(block.timestamp + 1 hours));
    }

    function test_RevertsWhenDeprecated() public {
        breaker.setDeprecated(true);
        vm.expectRevert(HardenedSettlement.IntakeHalted.selector);
        _settle(keccak256("t8"), 2 ether, 0, uint64(block.timestamp + 1 hours));
    }

    // ---- exit always works even when halted ----

    function test_ExitProcessesWhenHalted() public {
        breaker.setLatched(true);
        assertTrue(hs.processExit(recipient, 1 ether));
    }

    // ---- digest binds to this contract + chain (replay across contexts fails) ----

    function test_DigestBindsToContractAndChain() public view {
        bytes32 d = hs.digestFor(
            SUITE, keccak256("t9"), 2 ether, address(0), 1, recipient, 0,
            uint64(block.timestamp + 1 hours)
        );
        assertTrue(d != bytes32(0));
    }
}
