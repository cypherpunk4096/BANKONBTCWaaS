#!/usr/bin/env python3
"""
Pre-authentication DDoS shield for the x402 API gateway.

Closes the HIGH-severity finding in LIMITATIONS.md: CAIP-122 signature verification
is O(N) over chains, so unsigned request floods could exhaust CPU BEFORE any auth
check. This middleware rate-limits by IP *before* any signature work happens, and
applies a cheap proof-of-work challenge to suspicious sources.

Order of defense (cheapest first):
  1. IP token bucket        (O(1), rejects floods)
  2. Structural validation  (header shape, size caps)
  3. Optional PoW challenge (for IPs over soft threshold)
  4. THEN signature verify  (expensive; only reached by well-behaved clients)

Author: Gregory L. (codephreak) | License: Apache-2.0
"""

from __future__ import annotations
import time
import hashlib
from dataclasses import dataclass, field
from typing import Dict, Optional


@dataclass
class Bucket:
    tokens: float
    last: float


class PreAuthShield:
    """Cheap, allocation-light gate that runs before expensive signature checks."""

    def __init__(
        self,
        ip_rate_per_sec: float = 20.0,
        ip_burst: float = 40.0,
        soft_pow_threshold: float = 10.0,   # tokens below which PoW is demanded
        pow_difficulty_bits: int = 16,      # ~65k hashes; trivial for one client, costly at scale
        max_body_bytes: int = 16 * 1024,
    ):
        self.ip_rate = ip_rate_per_sec
        self.ip_burst = ip_burst
        self.soft = soft_pow_threshold
        self.pow_bits = pow_difficulty_bits
        self.max_body = max_body_bytes
        self._buckets: Dict[str, Bucket] = {}

    # ---- layer 1: IP token bucket (O(1)) ----

    def _refill(self, ip: str) -> Bucket:
        now = time.monotonic()
        b = self._buckets.get(ip)
        if b is None:
            b = Bucket(self.ip_burst, now)
            self._buckets[ip] = b
            return b
        b.tokens = min(self.ip_burst, b.tokens + (now - b.last) * self.ip_rate)
        b.last = now
        return b

    def check_ip(self, ip: str) -> tuple[bool, float]:
        b = self._refill(ip)
        if b.tokens >= 1.0:
            b.tokens -= 1.0
            return True, b.tokens
        return False, b.tokens

    # ---- layer 2: structural validation ----

    def check_structure(self, headers: dict, body_len: int) -> Optional[str]:
        if body_len > self.max_body:
            return "body_too_large"
        auth = headers.get("Authorization", "")
        if not auth.startswith("CAIP-122 "):
            return "malformed_auth"
        # "CAIP-122 <ns:ref>:<address>:<sig>" — cheap shape check, no crypto yet
        if auth.count(":") < 2:
            return "malformed_auth"
        return None

    # ---- layer 3: proof-of-work challenge (only for pressured IPs) ----

    def needs_pow(self, ip: str) -> bool:
        b = self._buckets.get(ip)
        return b is not None and b.tokens < self.soft

    def issue_challenge(self, ip: str) -> str:
        # Bind challenge to IP + coarse time; client must find nonce s.t.
        # sha256(challenge||nonce) has pow_bits leading zero bits.
        epoch = int(time.time()) // 30
        return hashlib.sha256(f"{ip}:{epoch}".encode()).hexdigest()

    def verify_pow(self, challenge: str, nonce: str) -> bool:
        h = hashlib.sha256(f"{challenge}:{nonce}".encode()).digest()
        bits = 0
        for byte in h:
            if byte == 0:
                bits += 8
            else:
                bits += (8 - byte.bit_length())
                break
        return bits >= self.pow_bits

    # ---- orchestration: returns (allow, http_status, reason) ----

    def admit(self, ip: str, headers: dict, body_len: int,
              pow_nonce: Optional[str] = None) -> tuple[bool, int, str]:
        ok, _ = self.check_ip(ip)
        if not ok:
            return False, 429, "rate_limited"

        err = self.check_structure(headers, body_len)
        if err:
            return False, 400, err

        if self.needs_pow(ip):
            challenge = self.issue_challenge(ip)
            if not pow_nonce or not self.verify_pow(challenge, pow_nonce):
                # 402-adjacent: signal PoW required via 429 + challenge header upstream
                return False, 429, f"pow_required:{challenge}"

        # Only now is the caller permitted to run the expensive signature verify.
        return True, 200, "admit_to_auth"


if __name__ == "__main__":
    # burst 20 > soft 8: first requests admit freely, then PoW is demanded as the
    # bucket drains, then hard rate-limit — signature work is only ever reached by
    # well-behaved clients under their fair share.
    shield = PreAuthShield(ip_rate_per_sec=2, ip_burst=20, soft_pow_threshold=8)
    hdr = {"Authorization": "CAIP-122 eip155:1:0xabc:0xsig"}
    admitted = 0
    for i in range(30):
        ok, status, reason = shield.admit("203.0.113.7", hdr, 512)
        admitted += ok
        tag = reason.split(":")[0]
        print(f"req {i:2d}: allow={ok} status={status} reason={tag}")
    print(f"\nadmitted {admitted}/30 to signature stage; the rest bounced cheaply")
