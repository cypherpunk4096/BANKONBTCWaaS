# SPDX-License-Identifier: GPL-3.0-or-later
# bankon-vault — overseers: pluggable custody that yields the vault's master material.
#
# Generalised from github.com/gnugui/GNUVAULT overseer.py and mindX bankon_vault/overseer.py
# (both the author's own work). An overseer answers ONE question: "prove you may open this vault,
# and hand me the raw master material." Passphrase, a key file, or a WALLET SIGNATURE.
#
# The signature overseer is the heart of "signature access": the master key is derived from a
# secp256k1 message signature (RFC-6979 → deterministic, so a hardware wallet reproduces it), and
# the signature itself is NEVER stored. Lose the signer, lose the vault — non-custodial by design.
"""
Overseer protocol + three implementations.

  material  =  the 32–64 raw bytes the core feeds into HKDF-SHA512(info="bankon-vault-master-key").

  PassphraseOverseer     material = PBKDF2-HMAC-SHA512(passphrase, salt, 600_000)      [portable]
  KeyfileOverseer        material = 64 random bytes in a 0400 key file                  [machine]
  WalletSignatureOverseer material = HKDF-SHA512(ikm=signature, info="…:<addr>")         [signature]
"""
from __future__ import annotations

import hashlib
import os
from typing import Optional, Protocol, runtime_checkable

from cryptography.hazmat.primitives.hashes import SHA512
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

PBKDF2_ITERS = 600_000          # OWASP 2024 for PBKDF2-HMAC-SHA512
DEFAULT_CHALLENGE = "BANKON-VAULT custody · cypherpunk2048 · the key becomes sovereign"


@runtime_checkable
class Overseer(Protocol):
    kind: str

    def fingerprint(self) -> str: ...
    def verify_evidence(self, evidence, challenge: str) -> bool: ...
    def produce_raw_key(self, challenge: str, evidence) -> bytes: ...


class PassphraseOverseer:
    """Portable custody — the operator remembers a passphrase. Master material = PBKDF2 of it."""
    kind = "passphrase"

    def __init__(self, passphrase: str, salt: bytes):
        self._pp = passphrase.encode()
        self._salt = salt

    def fingerprint(self) -> str:
        return "pass:" + hashlib.sha256(self._salt).hexdigest()[:12]

    def verify_evidence(self, evidence, challenge: str) -> bool:
        return True                                  # the passphrase IS the evidence (a wrong one → InvalidTag)

    def produce_raw_key(self, challenge: str, evidence) -> bytes:
        return PBKDF2HMAC(algorithm=SHA512(), length=64, salt=self._salt,
                          iterations=PBKDF2_ITERS).derive(self._pp)


class KeyfileOverseer:
    """Machine custody — a 64-byte random key file (0400). Auto-generates on first use."""
    kind = "keyfile"

    def __init__(self, key_file: str):
        self.key_file = os.path.abspath(key_file)

    def _material(self) -> bytes:
        if not os.path.exists(self.key_file):
            old = os.umask(0o077)
            try:
                fd = os.open(self.key_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o400)
                try:
                    os.write(fd, os.urandom(64))
                    os.fsync(fd)
                finally:
                    os.close(fd)
            finally:
                os.umask(old)
        with open(self.key_file, "rb") as f:
            m = f.read()
        if len(m) < 32:
            raise ValueError("key file too short")
        return m

    def fingerprint(self) -> str:
        return "keyfile:" + hashlib.sha256(self._material()).hexdigest()[:12]

    def verify_evidence(self, evidence, challenge: str) -> bool:
        return os.path.exists(self.key_file)

    def produce_raw_key(self, challenge: str, evidence) -> bytes:
        return self._material()


class WalletSignatureOverseer:
    """Signature custody — the master key is bound to a wallet SIGNATURE over `challenge`.

    `verifier(message, signature) -> address|None` recovers/validates the signer (chain-supplied,
    e.g. the BTC adapter's BIP-322/BIP-137 verify, or an EVM personal_sign recover). `expected_address`
    pins WHO may open the vault. The signature is used as HKDF IKM and never stored.
    """
    kind = "wallet-signature"

    def __init__(self, verifier, expected_address: str, salt: bytes,
                 challenge: str = DEFAULT_CHALLENGE):
        self._verify = verifier
        self._addr = expected_address.strip()
        self._salt = salt
        self.challenge = challenge

    def fingerprint(self) -> str:
        return "sig:" + hashlib.sha256(self._addr.encode()).hexdigest()[:12]

    def verify_evidence(self, evidence, challenge: str) -> bool:
        # evidence = the signature (hex/base64/bytes); message MUST equal the bound challenge (replay-safe)
        msg = challenge or self.challenge
        try:
            recovered = self._verify(msg, evidence)
        except Exception:
            return False
        return bool(recovered) and recovered.strip() == self._addr

    def produce_raw_key(self, challenge: str, evidence) -> bytes:
        sig = evidence if isinstance(evidence, (bytes, bytearray)) else str(evidence).encode()
        return HKDF(algorithm=SHA512(), length=64, salt=self._salt,
                    info=b"bankon-overseer-wallet-v1:" + self._addr.encode()).derive(bytes(sig))
