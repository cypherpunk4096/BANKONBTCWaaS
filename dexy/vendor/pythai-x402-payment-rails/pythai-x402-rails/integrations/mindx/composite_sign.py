#!/usr/bin/env python3
"""
CP-2048 reference composite signer / verifier.

IMPORTANT: This is orchestration scaffolding, NOT a cryptographic implementation.
Every primitive call delegates to a vetted library (liboqs via python-oqs, and
PyNaCl for Ed25519). Do not substitute hand-rolled lattice or Gaussian sampling.

Composite rule (draft-ietf-lamps-pq-composite-sigs): a signature is the ordered
concatenation of component signatures; verification succeeds IFF every component
verifies. There is no "either/or".

Author: Gregory L. (codephreak) | License: Apache-2.0
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import List, Tuple
import struct

# Vetted primitives only.
try:
    import oqs                      # liboqs: ML-DSA, SLH-DSA
    from nacl.signing import SigningKey, VerifyKey   # Ed25519
    from nacl.exceptions import BadSignatureError
except ImportError:  # pragma: no cover
    oqs = None  # allows import for schema/tooling without the crypto stack


# ---- Suite definitions (mirror of on-chain PQCRegistry) ----

FAMILY_CLASSICAL, FAMILY_LATTICE, FAMILY_HASH, FAMILY_CODE = range(4)

# component := (label, family, backend)
#   backend "ed25519" -> PyNaCl ; otherwise -> liboqs mechanism name
SUITES = {
    "CP2048-SIG-0": [   # Tier 0 roots: hash + lattice + classical (3 families)
        ("SLH-DSA-SHA2-256s", FAMILY_HASH,      "SPHINCS+-SHA2-256s-simple"),
        ("ML-DSA-87",         FAMILY_LATTICE,   "ML-DSA-87"),
        ("Ed25519",           FAMILY_CLASSICAL, "ed25519"),
    ],
    "CP2048-SIG-1": [   # Tier 1 validators/governance: lattice + classical
        ("ML-DSA-87", FAMILY_LATTICE,   "ML-DSA-87"),
        ("Ed25519",   FAMILY_CLASSICAL, "ed25519"),
    ],
    "CP2048-SIG-2": [   # Tier 2 agents/identity
        ("ML-DSA-65", FAMILY_LATTICE,   "ML-DSA-65"),
        ("Ed25519",   FAMILY_CLASSICAL, "ed25519"),
    ],
    "CP2048-SIG-3": [   # Tier 3 ephemeral, pure PQ
        ("ML-DSA-44", FAMILY_LATTICE, "ML-DSA-44"),
    ],
}


def _distinct_family_count(suite: str) -> int:
    return len({fam for _, fam, _ in SUITES[suite]})


def assert_tier_diversity(suite: str, tier: int) -> None:
    """Client-side mirror of the registry's anti-single-family guard."""
    if tier <= 1 and _distinct_family_count(suite) < 2:
        raise ValueError(
            f"{suite} is single-family; Tier {tier} requires >=2 families "
            f"(this is exactly the Falcon-1024-only failure CP-2048 forbids)"
        )


# ---- Wire format: [u8 n_components][ (u16 len || bytes) * n ] ----

def _pack(parts: List[bytes]) -> bytes:
    out = struct.pack("B", len(parts))
    for p in parts:
        out += struct.pack(">H", len(p)) + p
    return out


def _unpack(blob: bytes) -> List[bytes]:
    n = blob[0]; off = 1; parts = []
    for _ in range(n):
        (ln,) = struct.unpack_from(">H", blob, off); off += 2
        parts.append(blob[off:off + ln]); off += ln
    return parts


@dataclass
class CompositeKey:
    suite: str
    public_parts: List[bytes]
    _secret_handles: list  # opaque; kept out of serialization

    def public_bytes(self) -> bytes:
        return _pack(self.public_parts)


# ---- Keygen / Sign / Verify ----

def keygen(suite: str) -> CompositeKey:
    if oqs is None:
        raise RuntimeError("crypto backend unavailable (install python-oqs + pynacl)")
    pubs, secrets = [], []
    for _, _, backend in SUITES[suite]:
        if backend == "ed25519":
            sk = SigningKey.generate()
            pubs.append(bytes(sk.verify_key)); secrets.append(("ed25519", sk))
        else:
            signer = oqs.Signature(backend)
            pubs.append(signer.generate_keypair()); secrets.append((backend, signer))
    return CompositeKey(suite, pubs, secrets)


def sign(key: CompositeKey, msg: bytes) -> bytes:
    """Produce a composite signature: one component per suite algorithm."""
    sigs = []
    for backend, handle in key._secret_handles:
        if backend == "ed25519":
            sigs.append(handle.sign(msg).signature)
        else:
            sigs.append(handle.sign(msg))
    return _pack(sigs)


def verify(suite: str, msg: bytes, sig_blob: bytes, pub_blob: bytes) -> bool:
    """
    ALL components must verify. Any single failure -> False.
    This is the property that makes hybrid strictly safer than one algorithm.
    """
    if oqs is None:
        raise RuntimeError("crypto backend unavailable")
    sigs = _unpack(sig_blob)
    pubs = _unpack(pub_blob)
    comps = SUITES[suite]
    if not (len(sigs) == len(pubs) == len(comps)):
        return False

    for (_, _, backend), s, p in zip(comps, sigs, pubs):
        try:
            if backend == "ed25519":
                VerifyKey(p).verify(msg, s)
            else:
                if not oqs.Signature(backend).verify(msg, s, p):
                    return False
        except BadSignatureError:
            return False
        except Exception:
            return False
    return True


if __name__ == "__main__":
    # Illustrative flow (requires the crypto backend to actually run).
    for suite in SUITES:
        tier = int(suite[-1])
        try:
            assert_tier_diversity(suite, tier)
            print(f"{suite}: {_distinct_family_count(suite)} families, tier {tier}  OK")
        except ValueError as e:
            print(f"{suite}: REJECTED -> {e}")
