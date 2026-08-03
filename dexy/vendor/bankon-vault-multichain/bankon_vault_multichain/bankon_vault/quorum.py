"""N-of-M custody for a Tomb key (offline Shamir), anchored to a DAIO quorum.

Shares are produced/consumed OFFLINE. Chains see only a commitment and
approvals - never a share. Self-hosted analogue of secrets.dyne.org.
"""
from __future__ import annotations
import hashlib
from dataclasses import dataclass
from pathlib import Path

from pyshamir import split, combine   # pip install pyshamir


@dataclass(frozen=True)
class QuorumPolicy:
    threshold: int      # N required
    shares: int         # M total owners
    label: str = "bankon.eth/operator-vault"


def commitment(key_path: Path) -> str:
    """SHA-256 hex over the tomb key file - the on-chain commitment."""
    return hashlib.sha256(key_path.read_bytes()).hexdigest()


def shard(key_path: Path, policy: QuorumPolicy) -> list[bytes]:
    """Split the key into M shares; distribute to owners out-of-band."""
    return split(key_path.read_bytes(), policy.shares, policy.threshold)


def reconstitute(shares: list[bytes], out_key: Path, expect: str) -> Path:
    """Merge >= threshold shares; verify against the anchored commitment. Fail closed."""
    raw = combine(shares)
    if hashlib.sha256(raw).hexdigest() != expect.removeprefix("0x"):
        raise ValueError("reconstituted key fails commitment check")
    out_key.write_bytes(raw)
    out_key.chmod(0o400)
    return out_key
