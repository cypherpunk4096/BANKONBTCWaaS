# SPDX-License-Identifier: GPL-3.0-or-later
# bankon-vault — Shamir Secret Sharing over GF(2^8). Split the vault's master material into N shares
# such that any K reconstruct it and K-1 reveal nothing. This is the mathematics under the frozen
# "operator ceremony": no single person, safe, or site holds the key — only a quorum can raise it.
#
# Self-contained and auditable (no dependency): a byte-wise Shamir with the AES field polynomial
# 0x11B, exp/log tables for multiply/divide, and Lagrange interpolation at x=0.
"""
    shares = split(secret_bytes, n=5, k=3)     # list of (index:int, share:bytes)
    secret = combine(shares[:3])               # any 3 of the 5 reconstruct; any 2 do not

Each share is the same length as the secret. Indices are 1..255 (x=0 is the secret; never a share).
"""
from __future__ import annotations

import os
from typing import List, Tuple

# ---- GF(2^8) with reducing polynomial 0x11B (AES field), generator 0x03 ----
def _peasant(a: int, b: int) -> int:
    """Table-free GF(2^8) multiply — used once to build the exp/log tables."""
    p = 0
    for _ in range(8):
        if b & 1:
            p ^= a
        hi = a & 0x80
        a = (a << 1) & 0xFF
        if hi:
            a ^= 0x1B                       # reduce by 0x11B (low byte after the shift)
        b >>= 1
    return p


_EXP = [0] * 512
_LOG = [0] * 256
_x = 1
for _i in range(255):
    _EXP[_i] = _x
    _LOG[_x] = _i
    _x = _peasant(_x, 0x03)                  # 0x03 IS a generator of the group (0x02 is not)
for _i in range(255, 512):
    _EXP[_i] = _EXP[_i - 255]


def _mul(a: int, b: int) -> int:
    if a == 0 or b == 0:
        return 0
    return _EXP[_LOG[a] + _LOG[b]]


def _div(a: int, b: int) -> int:
    if b == 0:
        raise ZeroDivisionError("GF division by zero")
    if a == 0:
        return 0
    return _EXP[(_LOG[a] - _LOG[b]) % 255]


def _eval(coeffs: List[int], x: int) -> int:
    """Horner evaluation of a GF(256) polynomial (coeffs[0] = constant term = the secret byte)."""
    y = 0
    for c in reversed(coeffs):
        y = _mul(y, x) ^ c
    return y


def split(secret: bytes, n: int, k: int) -> List[Tuple[int, bytes]]:
    """Split `secret` into `n` shares, any `k` of which reconstruct it (2 <= k <= n <= 255)."""
    if not (2 <= k <= n <= 255):
        raise ValueError("need 2 <= k <= n <= 255")
    if not secret:
        raise ValueError("empty secret")
    shares = [bytearray() for _ in range(n)]
    for byte in secret:                                   # independent polynomial per secret byte
        coeffs = [byte] + [c for c in os.urandom(k - 1)]  # degree k-1; constant term = secret byte
        for si in range(n):
            shares[si].append(_eval(coeffs, si + 1))      # x = 1..n
    return [(si + 1, bytes(shares[si])) for si in range(n)]


def combine(shares: List[Tuple[int, bytes]]) -> bytes:
    """Reconstruct the secret from >= k shares via Lagrange interpolation at x = 0."""
    if len(shares) < 2:
        raise ValueError("need at least 2 shares")
    xs = [s[0] for s in shares]
    if len(set(xs)) != len(xs):
        raise ValueError("duplicate share indices")
    length = len(shares[0][1])
    if any(len(s[1]) != length for s in shares):
        raise ValueError("shares differ in length")
    out = bytearray(length)
    for pos in range(length):
        acc = 0
        for i, (xi, yi) in enumerate(shares):
            num, den = 1, 1
            for j, (xj, _) in enumerate(shares):         # Lagrange basis at x=0
                if i == j:
                    continue
                num = _mul(num, xj)                       # (0 - xj) == xj  in GF(2)
                den = _mul(den, xi ^ xj)                  # (xi - xj) == xi ^ xj
            acc ^= _mul(yi[pos], _div(num, den))
        out[pos] = acc
    return bytes(out)


# Human-portable share encoding: "<index>-<hexbytes>" (easy to write on paper / read back).
def encode_share(share: Tuple[int, bytes]) -> str:
    return f"{share[0]}-{share[1].hex()}"


def decode_share(text: str) -> Tuple[int, bytes]:
    idx, hexs = text.strip().split("-", 1)
    return int(idx), bytes.fromhex(hexs)
