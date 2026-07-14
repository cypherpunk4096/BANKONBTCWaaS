# SPDX-License-Identifier: GPL-3.0-or-later
# bankon-vault — HYBRID post-quantum custody (CP2048-QR roadmap item, now shipped).
#
# The vault's master material becomes  HKDF(inner_material || ML-KEM_shared_secret)  — an attacker
# must break BOTH the classical custody (passphrase / keyfile / wallet signature) AND ML-KEM
# (FIPS 203, lattice KEM). The hybrid can never be WEAKER than the inner overseer alone: even a
# broken PQC layer only concatenates extra bytes into the HKDF IKM.
#
# Why a KEM and not a PQC *signature* for key derivation: Falcon/ML-DSA signatures are RANDOMIZED —
# the same challenge yields a different signature every time, so signature-as-IKM (the trick that
# makes WalletSignatureOverseer work with deterministic RFC-6979 ECDSA) cannot work post-quantum.
# A KEM gives what key derivation actually needs: a stable shared secret recoverable only with the
# decapsulation key. This is exactly the "PQC KEM layered over the master material" the SECURITY.md
# conformance table promised.
#
# Backends, in order: `kyber-py` (pure Python, FIPS 203 ML-KEM — POC-grade, no C build) then
# `liboqs` (production-grade C). Degrades honestly when neither is installed.
#
# Enrollment stores ONLY public artifacts beside the vault (.pqc.json: the KEM ciphertext + a
# commitment to the shared secret). The decapsulation key is returned to the OPERATOR — store it
# offline like a Shamir share; it is never written next to the vault (else it is no second factor).
from __future__ import annotations

import hashlib
import json
import os
from typing import Optional, Tuple

from cryptography.hazmat.primitives.hashes import SHA512
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

KEM_VARIANTS = ("ML-KEM-512", "ML-KEM-768", "ML-KEM-1024")
DEFAULT_VARIANT = "ML-KEM-768"          # NIST category 3 — the FIPS 203 default recommendation
PQC_FILE = ".pqc.json"
_COMMIT_TAG = b"bankon-pqc-ss-commit-v1"
_HYBRID_INFO = b"bankon-vault-hybrid-pqc-v1"


# ---- KEM backend (kyber-py → liboqs → none) ----

def _kyber(variant: str):
    from kyber_py.ml_kem import ML_KEM_512, ML_KEM_768, ML_KEM_1024
    return {"ML-KEM-512": ML_KEM_512, "ML-KEM-768": ML_KEM_768, "ML-KEM-1024": ML_KEM_1024}[variant]


def backend() -> Optional[str]:
    try:
        _kyber(DEFAULT_VARIANT)
        return "kyber-py"
    except Exception:
        pass
    # liboqs-python may auto-BUILD liboqs on import (can hang) — probe in a subprocess, like pqc_falcon
    try:
        import subprocess
        import sys
        out = subprocess.run(
            [sys.executable, "-c", "import oqs,json;print(json.dumps(oqs.get_enabled_kem_mechanisms()))"],
            capture_output=True, text=True, timeout=4.0)
        if out.returncode == 0 and DEFAULT_VARIANT in json.loads(out.stdout.strip() or "[]"):
            return "liboqs"
    except Exception:
        pass
    return None


def available() -> bool:
    return backend() is not None


def status() -> dict:
    b = backend()
    return {"backend": b, "available": b is not None, "variants": list(KEM_VARIANTS) if b else [],
            "note": ("hybrid custody active — master = HKDF(classical || ML-KEM ss)" if b else
                     "no ML-KEM backend. `pip install --user kyber-py` (pure Python, POC) or a "
                     "prebuilt liboqs + liboqs-python. Vault stays fully usable classically.")}


def _keygen(variant: str) -> Tuple[bytes, bytes]:
    b = backend()
    if b == "kyber-py":
        return _kyber(variant).keygen()                       # (ek, dk)
    if b == "liboqs":
        import oqs
        with oqs.KeyEncapsulation(variant) as kem:
            ek = kem.generate_keypair()
            return ek, kem.export_secret_key()
    raise RuntimeError("no ML-KEM backend available")


def _encaps(variant: str, ek: bytes) -> Tuple[bytes, bytes]:
    b = backend()
    if b == "kyber-py":
        ss, ct = _kyber(variant).encaps(ek)
        return ss, ct
    if b == "liboqs":
        import oqs
        with oqs.KeyEncapsulation(variant) as kem:
            ct, ss = kem.encap_secret(ek)
            return ss, ct
    raise RuntimeError("no ML-KEM backend available")


def _decaps(variant: str, dk: bytes, ct: bytes) -> bytes:
    b = backend()
    if b == "kyber-py":
        return _kyber(variant).decaps(dk, ct)
    if b == "liboqs":
        import oqs
        with oqs.KeyEncapsulation(variant, dk) as kem:
            return kem.decap_secret(ct)
    raise RuntimeError("no ML-KEM backend available")


def _commit(ss: bytes) -> str:
    # sha256 commitment of a 256-bit uniform secret — public, ungrindable; lets a wrong
    # decapsulation key fail EARLY with a clear error instead of a downstream InvalidTag.
    return hashlib.sha256(_COMMIT_TAG + ss).hexdigest()


# ---- enrollment + overseer ----

def enroll(vault_dir: str, variant: str = DEFAULT_VARIANT) -> dict:
    """Enroll hybrid-PQC custody for a vault directory. Writes PUBLIC artifacts to .pqc.json and
    returns {"decaps_key": hex, ...} — hand the decaps key to the operator, store it OFFLINE.
    Enrolling is only half the ceremony: re-key the vault so the master actually depends on it
    (unlock with the inner overseer, then rotate to HybridPQCOverseer — or enroll before `init`)."""
    if variant not in KEM_VARIANTS:
        raise ValueError(f"variant must be one of {KEM_VARIANTS}")
    ek, dk = _keygen(variant)
    ss, ct = _encaps(variant, ek)
    doc = {"scheme": variant, "backend": backend(), "ct": ct.hex(), "ss_commit": _commit(ss),
           "ek_fingerprint": hashlib.sha256(ek).hexdigest()[:16]}
    path = os.path.join(os.path.abspath(os.path.expanduser(vault_dir)), PQC_FILE)
    old = os.umask(0o077)
    try:
        with open(path, "w") as f:
            json.dump(doc, f, indent=1)
            f.flush()
            os.fsync(f.fileno())
    finally:
        os.umask(old)
    out = {"decaps_key": dk.hex(), "variant": variant, "backend": doc["backend"],
           "pqc_file": path, "ss_commit": doc["ss_commit"],
           "note": "store the decaps_key OFFLINE (it is the quantum second factor); it is not "
                   "saved beside the vault. Unlock with HybridPQCOverseer(inner, decaps_key, dir)."}
    ss = b"\x00" * len(ss)          # drop our reference to the shared secret
    return out


class HybridPQCOverseer:
    """Wraps ANY inner overseer; master material = HKDF-SHA512(inner_material || ML-KEM ss).

        info = enroll(vault_dir)                       # once — keep info["decaps_key"] offline
        ov = HybridPQCOverseer(PassphraseOverseer(pp, salt), info["decaps_key"], vault_dir)
        vault.unlock(ov)

    Fail-closed: a wrong/absent decaps key or missing .pqc.json refuses before any decrypt."""
    kind = "hybrid-pqc"

    def __init__(self, inner, decaps_key, vault_dir: str):
        self.inner = inner
        self._dk = bytes.fromhex(decaps_key) if isinstance(decaps_key, str) else bytes(decaps_key)
        self._dir = os.path.abspath(os.path.expanduser(vault_dir))

    def _doc(self) -> dict:
        with open(os.path.join(self._dir, PQC_FILE)) as f:
            return json.load(f)

    def _shared_secret(self) -> bytes:
        doc = self._doc()
        ss = _decaps(doc["scheme"], self._dk, bytes.fromhex(doc["ct"]))
        # ML-KEM implicit rejection means a wrong dk still "succeeds" with garbage — the public
        # commitment catches that here, with a clear error instead of a downstream InvalidTag.
        if _commit(ss) != doc["ss_commit"]:
            raise PermissionError("ML-KEM decapsulation does not match the enrolled commitment "
                                  "(wrong decaps key or tampered .pqc.json)")
        return ss

    def fingerprint(self) -> str:
        try:
            fp = self._doc().get("ek_fingerprint", "?")
        except OSError:
            fp = "?"
        return f"hybrid[{self.inner.fingerprint()}+mlkem:{fp}]"

    def verify_evidence(self, evidence, challenge: str) -> bool:
        if not os.path.exists(os.path.join(self._dir, PQC_FILE)):
            return False                                     # not enrolled → refuse, never bypass
        try:
            self._shared_secret()
        except Exception:
            return False
        return self.inner.verify_evidence(evidence, challenge)

    def produce_raw_key(self, challenge: str, evidence) -> bytes:
        inner_ikm = self.inner.produce_raw_key(challenge, evidence)
        ss = self._shared_secret()
        doc = self._doc()
        salt = bytes.fromhex(doc["ss_commit"])               # public, per-enrollment
        out = HKDF(algorithm=SHA512(), length=64, salt=salt,
                   info=_HYBRID_INFO).derive(bytes(inner_ikm) + ss)
        ss = b"\x00" * len(ss)
        return out
