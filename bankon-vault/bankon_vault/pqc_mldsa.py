# SPDX-License-Identifier: GPL-3.0-or-later
# bankon-vault — ML-DSA (FIPS 204, CRYSTALS-Dilithium) post-quantum signatures. Together with
# pqc_falcon (FN-DSA) this completes the CP2048-QR signature roadmap: a NIST-final PQC signer that
# drops in BESIDE secp256k1 without touching the vault core.
#
# What it is for TODAY (honest):
#   • Tier-Q identity keys — mint/sign/verify a post-quantum identity (chains/pqc.py adapter).
#   • PQC QUORUM — `make_verifier()` plugs into PolicyEngine(verify_sig=…), so N-of-M signing
#     approval can be collected from ML-DSA keys: the *authorization* layer goes post-quantum even
#     while the BTC signature itself stays secp256k1 (consensus-frozen, Tier-C — no vault can fix).
#
# Backends: `dilithium-py` (pure Python FIPS 204 — POC-grade, no C build) then liboqs. Degrades
# honestly when neither is installed.
from __future__ import annotations

import hashlib
import json
from typing import Optional

VARIANTS = ("ML-DSA-44", "ML-DSA-65", "ML-DSA-87")
DEFAULT_VARIANT = "ML-DSA-65"           # NIST category 3


def _dpy(variant: str):
    from dilithium_py.ml_dsa import ML_DSA_44, ML_DSA_65, ML_DSA_87
    return {"ML-DSA-44": ML_DSA_44, "ML-DSA-65": ML_DSA_65, "ML-DSA-87": ML_DSA_87}[variant]


def backend() -> Optional[str]:
    try:
        _dpy(DEFAULT_VARIANT)
        return "dilithium-py"
    except Exception:
        pass
    try:                                 # liboqs import can hang on auto-build → subprocess probe
        import subprocess
        import sys
        out = subprocess.run(
            [sys.executable, "-c", "import oqs,json;print(json.dumps(oqs.get_enabled_sig_mechanisms()))"],
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
    return {"backend": b, "available": b is not None, "variants": list(VARIANTS) if b else [],
            "note": ("ML-DSA active — Tier-Q identity + PQC quorum available" if b else
                     "no ML-DSA backend. `pip install --user dilithium-py` (pure Python, POC) or "
                     "a prebuilt liboqs. Classical quorum/identity unaffected.")}


def generate(variant: str = DEFAULT_VARIANT) -> dict:
    """Mint an ML-DSA keypair (hex). The secret key is client-side material — store it in the
    vault like any other entry; NEVER send it anywhere (CP2048-QR: client-side keys, no escrow)."""
    if variant not in VARIANTS:
        raise ValueError(f"variant must be one of {VARIANTS}")
    b = backend()
    if b == "dilithium-py":
        pk, sk = _dpy(variant).keygen()
    elif b == "liboqs":
        import oqs
        with oqs.Signature(variant) as signer:
            pk = signer.generate_keypair()
            sk = signer.export_secret_key()
    else:
        raise RuntimeError("no ML-DSA backend available")
    return {"scheme": "ML-DSA (FIPS 204)", "variant": variant, "tier": "Tier-Q (experimental)",
            "public_key": pk.hex(), "secret_key": sk.hex(),
            "note": "post-quantum identity/quorum key — NOT a Bitcoin key (BTC stays secp256k1)"}


def sign(secret_key_hex: str, message: bytes, variant: str = DEFAULT_VARIANT) -> str:
    b = backend()
    if b == "dilithium-py":
        return _dpy(variant).sign(bytes.fromhex(secret_key_hex), message).hex()
    if b == "liboqs":
        import oqs
        with oqs.Signature(variant, bytes.fromhex(secret_key_hex)) as signer:
            return signer.sign(message).hex()
    raise RuntimeError("no ML-DSA backend available")


def verify(public_key_hex: str, message: bytes, signature_hex: str,
           variant: str = DEFAULT_VARIANT) -> bool:
    try:
        b = backend()
        if b == "dilithium-py":
            return bool(_dpy(variant).verify(bytes.fromhex(public_key_hex), message,
                                             bytes.fromhex(signature_hex)))
        if b == "liboqs":
            import oqs
            with oqs.Signature(variant) as verifier:
                return bool(verifier.verify(message, bytes.fromhex(signature_hex),
                                            bytes.fromhex(public_key_hex)))
    except Exception:
        return False
    return False


def fingerprint(public_key_hex: str) -> str:
    """Short stable identity for an ML-DSA pubkey (used as the quorum approver id)."""
    return "mldsa:" + hashlib.sha256(bytes.fromhex(public_key_hex)).hexdigest()[:16]


def make_verifier(variant: str = DEFAULT_VARIANT):
    """PolicyEngine-compatible verify_sig(message, signature, pubkey) — the drop-in that makes
    N-of-M signing quorum POST-QUANTUM. Approvers are pinned by their ML-DSA pubkey hex in
    PolicyConfig.quorum_approvers, exactly like secp256k1 pubkeys with the BTC adapter."""
    def _v(message, signature, pubkey) -> Optional[str]:
        m = message.encode() if isinstance(message, str) else bytes(message)
        try:
            ok = verify(str(pubkey), m, str(signature), variant)
        except Exception:
            return None
        return fingerprint(str(pubkey)) if ok else None
    return _v
