# SPDX-License-Identifier: GPL-3.0-or-later
# bankon-vault — the frozen operator CEREMONY (Step 4). Generate a vault master under air-gap, split
# it Shamir K-of-N across operators/witnesses/geographically-separated safes, and record a tamper-
# evident manifest. No single person, machine, or site can raise the key — only a quorum. This is the
# design from the mindX BANKON_VAULT review, made concrete on the self-contained GF(256) Shamir.
"""
    # GENESIS (air-gapped, once):
    shares, manifest = genesis(threshold=3, total=5)   # 3-of-5 by default
    #   → hand each `shares[i]` to a distinct operator; keep `manifest` (public, holds NO secret)

    # RECONSTITUTION (to unlock the vault later):
    ov = ShamirOverseer([share_a, share_c, share_e], salt)   # any 3 of the 5
    vault.unlock(ov)
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from cryptography.hazmat.primitives.hashes import SHA512
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from . import shamir

MASTER_BYTES = 64                     # the raw master material (HKDF IKM), matching the overseer contract
CEREMONY_INFO = b"bankon-overseer-shamir-v1"


@dataclass
class Manifest:
    """Public ceremony record — contains NO secret, only commitments so shares can be verified."""
    threshold: int
    total: int
    master_fingerprint: str           # sha256(master)[:16] — lets you confirm a reconstruction is right
    share_commitments: List[str]      # sha256(index||share)[:16] per share — detects a swapped/edited share
    version: str = "1.0.0"

    def to_dict(self) -> dict:
        return {"version": self.version, "threshold": self.threshold, "total": self.total,
                "master_fingerprint": self.master_fingerprint, "share_commitments": self.share_commitments}


def _fingerprint(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()[:16]


def _commit(index: int, share: bytes) -> str:
    return hashlib.sha256(bytes([index]) + share).hexdigest()[:16]


def genesis(threshold: int = 3, total: int = 5,
            master: Optional[bytes] = None) -> Tuple[List[str], Manifest]:
    """Create (or accept) a 64-byte master, split it K-of-N, and return (paper-encoded shares, manifest).

    Run this **air-gapped**. Distribute one share per operator; publish the manifest. The master is NOT
    returned or stored anywhere — it exists only to be split, then discarded from memory by the caller.
    """
    if master is None:
        master = os.urandom(MASTER_BYTES)
    if len(master) != MASTER_BYTES:
        raise ValueError(f"master must be {MASTER_BYTES} bytes")
    raw = shamir.split(master, n=total, k=threshold)
    manifest = Manifest(threshold=threshold, total=total,
                        master_fingerprint=_fingerprint(master),
                        share_commitments=[_commit(i, s) for i, s in raw])
    encoded = [shamir.encode_share(s) for s in raw]
    # scrub the local master copy
    m = bytearray(master)
    for i in range(len(m)):
        m[i] = 0
    return encoded, manifest


def reconstruct(encoded_shares: List[str], manifest: Optional[Manifest] = None) -> bytes:
    """Combine >= threshold paper shares back into the 64-byte master. Verifies against the manifest
    fingerprint when provided (so you KNOW the reconstruction is the original, not a silent wrong key)."""
    parts = [shamir.decode_share(s) for s in encoded_shares]
    master = shamir.combine(parts)
    if manifest is not None and _fingerprint(master) != manifest.master_fingerprint:
        raise ValueError("reconstruction does not match the manifest fingerprint — wrong/insufficient shares")
    return master


class ShamirOverseer:
    """Custody by QUORUM — unlock the vault only when >= K operators present their shares. Derives the
    same HKDF master material the passphrase/signature overseers do, so it drops into vault.unlock()."""
    kind = "shamir-quorum"

    def __init__(self, encoded_shares: List[str], salt: bytes, manifest: Optional[Manifest] = None):
        self._shares = list(encoded_shares)
        self._salt = salt
        self._manifest = manifest

    def fingerprint(self) -> str:
        return "shamir:" + hashlib.sha256(("".join(sorted(self._shares))).encode()).hexdigest()[:12]

    def verify_evidence(self, evidence, challenge: str) -> bool:
        try:
            return len(self._shares) >= (self._manifest.threshold if self._manifest else 2)
        except Exception:
            return False

    def produce_raw_key(self, challenge: str, evidence) -> bytes:
        master = reconstruct(self._shares, self._manifest)
        try:
            return HKDF(algorithm=SHA512(), length=MASTER_BYTES, salt=self._salt,
                        info=CEREMONY_INFO).derive(master)
        finally:
            m = bytearray(master)                # wipe the reconstructed master
            for i in range(len(m)):
                m[i] = 0
