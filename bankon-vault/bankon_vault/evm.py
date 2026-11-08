# SPDX-License-Identifier: GPL-3.0-or-later
# bankon-vault — EVM personal_sign verification, self-contained: Keccak-256 and secp256k1 public-key
# recovery in plain Python (no dependency), so a DAIO seat holder's wallet signature can be checked on an
# air-gapped machine. Verification only: this module never holds or makes a private key.
"""
    addr = recover_personal_sign("message", "0x…65-byte signature…")   # → "0xAbC…" (EIP-55) or None
    verify_personal_sign("message", sig, "0xabc…")                     # → True / False
"""
from __future__ import annotations

from typing import Optional

# ---- Keccak-256 (the pre-NIST padding Ethereum uses; hashlib.sha3_256 is different) ----
_RC = [0x0000000000000001, 0x0000000000008082, 0x800000000000808A, 0x8000000080008000, 0x000000000000808B,
       0x0000000080000001, 0x8000000080008081, 0x8000000000008009, 0x000000000000008A, 0x0000000000000088,
       0x0000000080008009, 0x000000008000000A, 0x000000008000808B, 0x800000000000008B, 0x8000000000008089,
       0x8000000000008003, 0x8000000000008002, 0x8000000000000080, 0x000000000000800A, 0x800000008000000A,
       0x8000000080008081, 0x8000000000008080, 0x0000000080000001, 0x8000000080008008]
_ROT = [[0, 36, 3, 41, 18], [1, 44, 10, 45, 2], [62, 6, 43, 15, 61], [28, 55, 25, 21, 56], [27, 20, 39, 8, 14]]
_M = (1 << 64) - 1


def _rol(x: int, n: int) -> int:
    return ((x << n) | (x >> (64 - n))) & _M if n else x


def _keccak_f(a):
    for rc in _RC:
        c = [a[x][0] ^ a[x][1] ^ a[x][2] ^ a[x][3] ^ a[x][4] for x in range(5)]
        d = [c[(x - 1) % 5] ^ _rol(c[(x + 1) % 5], 1) for x in range(5)]
        a = [[a[x][y] ^ d[x] for y in range(5)] for x in range(5)]
        b = [[0] * 5 for _ in range(5)]
        for x in range(5):
            for y in range(5):
                b[y][(2 * x + 3 * y) % 5] = _rol(a[x][y], _ROT[x][y])
        a = [[b[x][y] ^ ((~b[(x + 1) % 5][y]) & b[(x + 2) % 5][y]) for y in range(5)] for x in range(5)]
        a[0][0] ^= rc
    return a


def keccak256(data: bytes) -> bytes:
    rate = 136
    msg = bytearray(data) + b"\x01"
    msg += b"\x00" * ((-len(msg)) % rate)
    msg[-1] |= 0x80
    a = [[0] * 5 for _ in range(5)]
    for off in range(0, len(msg), rate):
        block = msg[off:off + rate]
        for i in range(rate // 8):
            a[i % 5][i // 5] ^= int.from_bytes(block[8 * i:8 * i + 8], "little")
        a = _keccak_f(a)
    out = b"".join(a[i % 5][i // 5].to_bytes(8, "little") for i in range(4))
    return out[:32]


# ---- secp256k1 public-key recovery ----
_P = 2 ** 256 - 2 ** 32 - 977
_N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
_G = (0x79BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798,
      0x483ADA7726A3C4655DA4FBFC0E1108A8FD17B448A68554199C47D08FFB10D4B8)


def _add(p, q):
    if p is None:
        return q
    if q is None:
        return p
    if p[0] == q[0]:
        if (p[1] + q[1]) % _P == 0:
            return None
        lam = 3 * p[0] * p[0] * pow(2 * p[1], -1, _P) % _P
    else:
        lam = (q[1] - p[1]) * pow(q[0] - p[0], -1, _P) % _P
    x = (lam * lam - p[0] - q[0]) % _P
    return x, (lam * (p[0] - x) - p[1]) % _P


def _mul(k: int, p):
    r = None
    while k:
        if k & 1:
            r = _add(r, p)
        p = _add(p, p)
        k >>= 1
    return r


def checksum_address(addr20: bytes) -> str:
    h = addr20.hex()
    k = keccak256(h.encode()).hex()
    return "0x" + "".join(c.upper() if int(k[i], 16) >= 8 else c for i, c in enumerate(h))


def personal_digest(message) -> bytes:
    m = message.encode() if isinstance(message, str) else bytes(message)
    return keccak256(b"\x19Ethereum Signed Message:\n" + str(len(m)).encode() + m)


def _sig_bytes(signature) -> bytes:
    if isinstance(signature, (bytes, bytearray)):
        return bytes(signature)
    s = str(signature).strip()
    return bytes.fromhex(s[2:] if s.startswith("0x") else s)


def recover_digest(digest: bytes, signature) -> Optional[str]:
    """65-byte r‖s‖v (v 27/28 or 0/1), low-s required → EIP-55 address, or None."""
    try:
        sig = _sig_bytes(signature)
    except ValueError:
        return None
    if len(sig) != 65:
        return None
    r, s, v = int.from_bytes(sig[:32], "big"), int.from_bytes(sig[32:64], "big"), sig[64]
    v = v - 27 if v >= 27 else v
    if v not in (0, 1) or not (0 < r < _N) or not (0 < s <= _N // 2):
        return None
    y2 = (pow(r, 3, _P) + 7) % _P
    y = pow(y2, (_P + 1) // 4, _P)
    if y * y % _P != y2:
        return None
    if y & 1 != v:
        y = _P - y
    e = int.from_bytes(digest, "big") % _N
    rinv = pow(r, -1, _N)
    q = _add(_mul((-e * rinv) % _N, _G), _mul((s * rinv) % _N, (r, y)))
    if q is None:
        return None
    pub = q[0].to_bytes(32, "big") + q[1].to_bytes(32, "big")
    return checksum_address(keccak256(pub)[12:])


def recover_personal_sign(message, signature) -> Optional[str]:
    return recover_digest(personal_digest(message), signature)


def verify_personal_sign(message, signature, address: str) -> bool:
    got = recover_personal_sign(message, signature)
    return bool(got) and got.lower() == str(address).strip().lower()
