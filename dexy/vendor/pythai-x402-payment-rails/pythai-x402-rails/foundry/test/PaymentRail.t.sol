// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.20;

import "forge-std/Test.sol";
import "../src/core/PaymentRail.sol";

contract PaymentRailTest is Test {
    PaymentRail rail;
    address validatorRegistry = makeAddr("validator");
    address senatus = makeAddr("senatus");
    address treasury = makeAddr("treasury");
    address payee = makeAddr("payee");
    address payer = makeAddr("payer");
    
    function setUp() public {
        rail = new PaymentRail(validatorRegistry, senatus, treasury);
        rail.approvePayee(payee);
        
        // Fund payer with ETH
        vm.deal(payer, 10 ether);
    }
    
    function testProcessPaymentETH() public {
        vm.prank(payer);
        bytes32 receiptId = rail.processPayment(
            keccak256("content-hash"),
            1 ether,
            address(0),
            payee
        );
        
        assert(receiptId != bytes32(0));
        
        (,,, PaymentRail.PaymentState state,) = rail.getPaymentDetails(receiptId);
        assertEq(uint(state), uint(PaymentRail.PaymentState.PENDING));
    }
    
    function testRateLimitBucketRefill() public {
        vm.prank(senatus);
        rail.setRateLimit(payer, 10, 100); // 10 tokens/sec, 100 capacity
        
        // First call should succeed
        vm.prank(payer);
        uint256 remaining = rail.checkRateLimit(payer);
        assertEq(remaining, 99);
        
        // Immediate second call should fail (1 token remaining, but need 1)
        vm.prank(payer);
        vm.expectRevert("Rate limit exceeded");
        rail.checkRateLimit(payer);
        
        // After 1 second, we should have ~10 tokens refilled
        vm.warp(block.timestamp + 1 seconds);
        
        vm.prank(payer);
        remaining = rail.checkRateLimit(payer);
        assert(remaining > 0);
    }
    
    function testDebasementFeeCalculation() public {
        uint256 amount = 1 ether;
        
        // Day 0: fee should be ~100% (10^18 deflate factor)
        // (1 ether * 10^18) / 10^18 = 1 ether
        uint256 fee0 = rail.calculateDebasementFee(amount);
        assertGe(fee0, amount * 99 / 100); // Close to amount
        
        // Skip to day 1
        vm.warp(block.timestamp + 1 days);
        uint256 fee1 = rail.calculateDebasementFee(amount);
        
        // Fee should be slightly lower (10^-12 deflation per day)
        assertLt(fee1, fee0);
        assert(fee1 > amount * 99 / 100);
    }
    
    function testAccessTokenExpiry() public {
        vm.prank(payer);
        bytes32 receiptId = rail.processPayment(
            keccak256("content-hash"),
            1 ether,
            address(0),
            payee
        );
        
        // Settle payment
        vm.prank(validatorRegistry);
        rail.settlePayment(receiptId);
        
        // Generate access token valid for 1 hour
        vm.prank(payer);
        string memory token = rail.getAccessToken(receiptId, 3600);
        assert(bytes(token).length > 0);
        
        // Verify payment should pass
        bool valid = rail.verifyPayment(receiptId);
        assertTrue(valid);
        
        // After 1 hour, payment should be expired
        vm.warp(block.timestamp + 3601);
        valid = rail.verifyPayment(receiptId);
        assertFalse(valid);
    }
    
    function testRefundExpired() public {
        vm.prank(payer);
        bytes32 receiptId = rail.processPayment(
            keccak256("content-hash"),
            1 ether,
            address(0),
            payee
        );
        
        uint256 balanceBefore = payer.balance;
        
        // Expire payment
        vm.warp(block.timestamp + 1 hours + 1 seconds);
        
        // Refund
        vm.prank(payer);
        uint256 refundAmount = rail.refundExpired(receiptId);
        
        assertEq(refundAmount, 1 ether);
        assertEq(payer.balance, balanceBefore + 1 ether);
    }
    
    function testDisputeInitiation() public {
        vm.prank(payer);
        bytes32 receiptId = rail.processPayment(
            keccak256("content-hash"),
            1 ether,
            address(0),
            payee
        );
        
        vm.prank(payer);
        rail.initiateDispute(receiptId, "Incorrect service");
        
        (,,,PaymentRail.PaymentState state,) = rail.getPaymentDetails(receiptId);
        assertEq(uint(state), uint(PaymentRail.PaymentState.DISPUTED));
    }
    
    // Helper to view payment details
    function getPaymentDetails(bytes32 receiptId) 
        public 
        view 
        returns (address, address, uint256, PaymentRail.PaymentState, bool)
    {
        PaymentRail.PaymentRecord memory payment = rail.payments(receiptId);
        return (payment.payer, payment.payee, payment.amount, payment.state, payment.settled);
    }
}
