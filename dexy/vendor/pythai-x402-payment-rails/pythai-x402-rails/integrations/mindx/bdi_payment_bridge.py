#!/usr/bin/env python3
"""
mindX BDI Agent Integration with x402 Payment Rails

Enables Beliefs-Desires-Intentions agents to gate access via RFC 9110 HTTP 402.
Implements:
- CAIP-122 multi-chain authentication
- Rate limiting (token-bucket)
- Payment verification
- Dispute resolution

Author: Gregory L. (codephreak)
License: Apache-2.0
"""

import asyncio
import json
import time
from typing import Dict, Optional, Tuple, Any
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from enum import Enum
import hashlib
import hmac

from eth_keys import keys
from eth_account import Account
from eth_account.messages import encode_defunct
from web3 import Web3

from algosdk import account as algo_account
from algosdk.encoding import decode_address


# ========== Data Types ==========

class PaymentStatus(Enum):
    """Payment settlement states"""
    PENDING = "pending"
    VERIFIED = "verified"
    SETTLED = "settled"
    DISPUTED = "disputed"
    REFUNDED = "refunded"
    EXPIRED = "expired"


@dataclass
class PaymentProof:
    """Cryptographic proof of payment"""
    receipt_id: str
    payer_address: str
    amount: int
    token: str
    timestamp: int
    settled: bool
    signature: str  # Validator signature


@dataclass
class RateLimitState:
    """Token-bucket state for user"""
    user_address: str
    tokens_remaining: float
    bucket_capacity: float
    tokens_per_second: float
    last_refill_time: float


@dataclass
class BDIContext:
    """Context passed to BDI agent inference"""
    payer: str
    payment_proof: Optional[PaymentProof]
    rate_limit_remaining: float
    authenticated: bool
    caip122_chain: str  # "eip155" | "algorand" | "solana"


# ========== Main Bridge ==========

class X402PaymentBridge:
    """
    Bridges mindX BDI agents with x402 payment rail.
    
    Manages payment verification, rate limiting, and dispute resolution.
    """
    
    def __init__(
        self,
        eth_provider: str = "https://eth.llamarpc.com",
        algo_server: str = "https://mainnet-api.algonode.cloud",
        payment_rail_addr: str = "0x",
        eth_chain_id: int = 1,
    ):
        self.eth_web3 = Web3(Web3.HTTPProvider(eth_provider))
        self.algo_server = algo_server
        self.payment_rail_addr = Web3.to_checksum_address(payment_rail_addr)
        self.eth_chain_id = eth_chain_id
        
        # In-memory rate limit tracking (prod: use Redis)
        self.rate_limits: Dict[str, RateLimitState] = {}
        
        # Payment verification cache (prod: use database)
        self.payment_cache: Dict[str, PaymentProof] = {}
    
    # ===== Authentication (CAIP-122) =====
    
    async def verify_caip122(
        self,
        message: str,
        signature: str,
        address: str,
        chain_namespace: str = "eip155",
        chain_reference: str = "1",
    ) -> Tuple[bool, Optional[str]]:
        """
        Verify CAIP-122 signature across multiple chains.
        
        Returns: (valid, verified_address)
        """
        
        try:
            if chain_namespace == "eip155":
                # Ethereum signature verification
                return self._verify_eip155(message, signature, address)
            
            elif chain_namespace == "algorand":
                # Algorand signature verification
                return await self._verify_algorand(message, signature, address)
            
            elif chain_namespace == "solana":
                # Solana signature verification
                return await self._verify_solana(message, signature, address)
            
            else:
                return False, None
        
        except Exception as e:
            print(f"CAIP-122 verification error: {e}")
            return False, None
    
    def _verify_eip155(
        self,
        message: str,
        signature: str,
        address: str,
    ) -> Tuple[bool, Optional[str]]:
        """Verify EIP-191 signature (Ethereum)"""
        
        try:
            msg = encode_defunct(text=message)
            recovered = Account.recover_message(msg, signature=signature)
            
            valid = recovered.lower() == address.lower()
            return valid, recovered if valid else None
        
        except Exception as e:
            print(f"EIP-155 verification error: {e}")
            return False, None
    
    async def _verify_algorand(
        self,
        message: str,
        signature: str,
        address: str,
    ) -> Tuple[bool, Optional[str]]:
        """Verify Algorand signature"""
        
        try:
            # Algorand signature: base64-encoded message + public key
            from nacl.signing import VerifyKey
            from nacl.exceptions import BadSignatureError
            import base64
            
            # Decode address to public key
            public_key_bytes = decode_address(address)
            verify_key = VerifyKey(public_key_bytes)
            
            # Verify signature
            message_bytes = message.encode('utf-8')
            signature_bytes = base64.b64decode(signature)
            
            try:
                verify_key.verify(message_bytes, signature_bytes)
                return True, address
            except BadSignatureError:
                return False, None
        
        except Exception as e:
            print(f"Algorand verification error: {e}")
            return False, None
    
    async def _verify_solana(
        self,
        message: str,
        signature: str,
        address: str,
    ) -> Tuple[bool, Optional[str]]:
        """Verify Solana signature"""
        
        # Placeholder for Solana verification
        # In production: use solders or solana-py library
        try:
            from nacl.signing import VerifyKey
            from nacl.exceptions import BadSignatureError
            import base64
            
            public_key = VerifyKey(base64.b64decode(address))
            message_bytes = message.encode('utf-8')
            sig_bytes = base64.b64decode(signature)
            
            try:
                public_key.verify(message_bytes, sig_bytes)
                return True, address
            except BadSignatureError:
                return False, None
        
        except Exception as e:
            print(f"Solana verification error: {e}")
            return False, None
    
    # ===== Payment Verification =====
    
    async def check_payment(
        self,
        payer_address: str,
        agent_id: str,
        receipt_id: str = None,
    ) -> Tuple[PaymentStatus, Optional[PaymentProof]]:
        """
        Verify payment status via PaymentRail contract.
        
        Returns: (status, proof)
        """
        
        # Check cache first
        if receipt_id and receipt_id in self.payment_cache:
            proof = self.payment_cache[receipt_id]
            if proof.settled:
                return PaymentStatus.SETTLED, proof
        
        try:
            # Query PaymentRail contract
            # In production: implement actual contract call
            
            # Simulated response
            proof = PaymentProof(
                receipt_id=receipt_id or f"mock-{time.time()}",
                payer_address=payer_address,
                amount=1000000,  # 1 USDC or 1 Algo
                token="USDC",
                timestamp=int(time.time()),
                settled=True,
                signature="0xmock..."
            )
            
            # Cache
            self.payment_cache[proof.receipt_id] = proof
            
            return PaymentStatus.SETTLED, proof
        
        except Exception as e:
            print(f"Payment verification error: {e}")
            return PaymentStatus.PENDING, None
    
    # ===== Rate Limiting =====
    
    def check_rate_limit(
        self,
        user_address: str,
        tokens_required: float = 1.0,
    ) -> Tuple[bool, float]:
        """
        Token-bucket rate limiting.
        
        Returns: (allowed, remaining_tokens)
        """
        
        if user_address not in self.rate_limits:
            # Initialize new user
            self.rate_limits[user_address] = RateLimitState(
                user_address=user_address,
                tokens_remaining=1000.0,  # Default bucket capacity
                bucket_capacity=1000.0,
                tokens_per_second=10.0,
                last_refill_time=time.time(),
            )
        
        limit = self.rate_limits[user_address]
        
        # Refill bucket based on elapsed time
        now = time.time()
        elapsed = now - limit.last_refill_time
        tokens_to_add = elapsed * limit.tokens_per_second
        
        limit.tokens_remaining = min(
            limit.bucket_capacity,
            limit.tokens_remaining + tokens_to_add
        )
        limit.last_refill_time = now
        
        # Check if user has sufficient tokens
        if limit.tokens_remaining >= tokens_required:
            limit.tokens_remaining -= tokens_required
            return True, limit.tokens_remaining
        
        return False, limit.tokens_remaining
    
    async def set_rate_limit(
        self,
        user_address: str,
        tokens_per_second: float,
        bucket_capacity: float,
    ) -> None:
        """Configure rate limit for user (admin)"""
        
        self.rate_limits[user_address] = RateLimitState(
            user_address=user_address,
            tokens_remaining=bucket_capacity,
            bucket_capacity=bucket_capacity,
            tokens_per_second=tokens_per_second,
            last_refill_time=time.time(),
        )
    
    # ===== BDI Agent Integration =====
    
    async def gated_inference(
        self,
        agent_id: str,
        input_data: Dict[str, Any],
        payer_address: str,
        signature: str,
        caip122_chain: str = "eip155",
    ) -> Tuple[int, Dict[str, Any]]:
        """
        Execute agent inference with payment gating.
        
        Returns: (http_status, response_json)
        """
        
        # Step 1: Verify authentication (CAIP-122)
        valid, verified_addr = await self.verify_caip122(
            message=f"{agent_id}:{int(time.time())}",
            signature=signature,
            address=payer_address,
            chain_namespace=caip122_chain,
        )
        
        if not valid:
            return 401, {
                "error": "unauthorized",
                "message": "Invalid CAIP-122 signature"
            }
        
        # Step 2: Check payment status
        payment_status, proof = await self.check_payment(
            payer_address=verified_addr,
            agent_id=agent_id,
        )
        
        if payment_status != PaymentStatus.SETTLED:
            return 402, {
                "error": "payment_required",
                "receipt_id": proof.receipt_id if proof else None,
                "fee": 0.001,
                "currency": "USDC"
            }
        
        # Step 3: Check rate limit
        allowed, remaining = self.check_rate_limit(verified_addr)
        
        if not allowed:
            return 429, {
                "error": "rate_limited",
                "retry_after": 60,
                "tokens_remaining": remaining
            }
        
        # Step 4: Build BDI context
        bdi_context = BDIContext(
            payer=verified_addr,
            payment_proof=proof,
            rate_limit_remaining=remaining,
            authenticated=True,
            caip122_chain=caip122_chain,
        )
        
        # Step 5: Execute agent inference (mock)
        try:
            result = {
                "agent_id": agent_id,
                "input": input_data,
                "output": {
                    "inference": "mock-inference-result",
                    "confidence": 0.95
                },
                "rate_limit_remaining": remaining,
                "payment_proof": {
                    "receipt_id": proof.receipt_id,
                    "settled_at": datetime.fromtimestamp(proof.timestamp).isoformat()
                }
            }
            
            return 200, result
        
        except Exception as e:
            return 500, {
                "error": "inference_failed",
                "message": str(e)
            }
    
    # ===== Dispute Handling =====
    
    async def initiate_dispute(
        self,
        receipt_id: str,
        reason: str,
        payer_address: str,
    ) -> Dict[str, Any]:
        """
        Initiate payment dispute.
        Returns to Senatus governance for resolution.
        """
        
        dispute = {
            "receipt_id": receipt_id,
            "reason": reason,
            "payer": payer_address,
            "initiated_at": datetime.now().isoformat(),
            "status": "pending_senatus_review"
        }
        
        # In production: submit to PaymentRail dispute handler
        print(f"Dispute initiated: {json.dumps(dispute, indent=2)}")
        
        return dispute


# ========== RAGE Memory Integration ==========

class RAGEMemory:
    """
    Recency-Augmented Guided Experience memory for agents.
    Tracks per-user context for BDI belief/desire/intention updates.
    """
    
    def __init__(self):
        self.recent_payments: Dict[str, list] = {}
        self.user_context: Dict[str, Dict] = {}
    
    def record_payment(
        self,
        payer: str,
        amount: int,
        tx_hash: str,
        agent_id: str,
    ) -> None:
        """Record payment in memory"""
        
        if payer not in self.recent_payments:
            self.recent_payments[payer] = []
        
        self.recent_payments[payer].append({
            "amount": amount,
            "tx_hash": tx_hash,
            "agent_id": agent_id,
            "timestamp": time.time()
        })
        
        # Keep only last 100 payments per user
        if len(self.recent_payments[payer]) > 100:
            self.recent_payments[payer] = self.recent_payments[payer][-100:]
    
    def get_user_context(self, user_address: str) -> Dict[str, Any]:
        """
        Return contextual state for BDI belief/desire/intention update.
        
        Beliefs: What the agent believes about the user
        Desires: What goals the agent should optimize for
        Intentions: What actions the agent should take
        """
        
        if user_address not in self.recent_payments:
            return {
                "is_new_user": True,
                "total_spent": 0,
                "payment_frequency": 0,
                "preferred_agent": None
            }
        
        payments = self.recent_payments[user_address]
        now = time.time()
        
        # Calculate recent metrics
        one_hour_ago = now - 3600
        recent_payments = [p for p in payments if p["timestamp"] > one_hour_ago]
        
        one_day_ago = now - 86400
        daily_payments = [p for p in payments if p["timestamp"] > one_day_ago]
        
        # Infer preferred agent
        agent_counts = {}
        for p in payments[-20:]:  # Last 20 payments
            agent_id = p["agent_id"]
            agent_counts[agent_id] = agent_counts.get(agent_id, 0) + 1
        
        preferred_agent = max(agent_counts.items(), key=lambda x: x[1])[0] if agent_counts else None
        
        return {
            "is_new_user": len(payments) == 0,
            "total_spent": sum(p["amount"] for p in payments),
            "payment_frequency": len(daily_payments) / 1,  # per day
            "preferred_agent": preferred_agent,
            "recent_activity": {
                "last_hour": len(recent_payments),
                "last_day": len(daily_payments),
            },
            "likely_intent": self._infer_intent(payments)
        }
    
    def _infer_intent(self, payments: list) -> str:
        """Infer user intent from payment pattern"""
        
        if len(payments) < 3:
            return "exploratory"
        
        # Check frequency
        timestamps = [p["timestamp"] for p in payments[-10:]]
        if len(timestamps) > 1:
            avg_interval = (timestamps[-1] - timestamps[0]) / len(timestamps)
            
            if avg_interval < 300:  # < 5 min apart
                return "intensive_use"
            elif avg_interval < 3600:  # < 1 hour
                return "active_user"
            else:
                return "casual_user"
        
        return "unknown"


# ========== Async Handler ==========

async def main():
    """Example usage"""
    
    bridge = X402PaymentBridge(
        eth_provider="https://eth.llamarpc.com",
        payment_rail_addr="0x",  # Set to actual address
    )
    
    # Example: Verify CAIP-122 and execute gated inference
    status, response = await bridge.gated_inference(
        agent_id="agent-uuid-123",
        input_data={"query": "What is the meaning of life?"},
        payer_address="0x1234567890123456789012345678901234567890",
        signature="0xmock-signature",
        caip122_chain="eip155",
    )
    
    print(f"Status: {status}")
    print(f"Response: {json.dumps(response, indent=2)}")


if __name__ == "__main__":
    asyncio.run(main())
