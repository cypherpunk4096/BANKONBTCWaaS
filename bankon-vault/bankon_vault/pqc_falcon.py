# SPDX-License-Identifier: GPL-3.0-or-later
# bankon-vault — POST-QUANTUM signature POC (Falcon / FN-DSA). This is a proof-of-concept for the
# CP2048-QR quantum-native path, honest about its status:
#
#   • Bitcoin mainnet CANNOT verify Falcon — its signatures are secp256k1 (Tier-C, consensus-frozen).
#   • Falcon (FN-DSA) is a NIST-selected lattice signature — the same scheme Algorand went quantum-
#     native with (Nov 2025). This POC lets BANKON mint a PQC identity key TODAY for the quantum-
#     native path (Algorand-style / future BTC soft-fork), so the wallet is Tier-Q-ready.
#   • Crypto-agile by contract (CP2048-QR): this drops in beside secp256k1, it does not replace it.
#
# Backend: liboqs via `oqs` (liboqs-python). Degrades honestly if the PQC backend isn't built.
"""
    from bankon_vault import pqc_falcon
    if pqc_falcon.available():
        kp = pqc_falcon.generate("Falcon-512")        # {variant, public_key, secret_key, ...}
        sig = pqc_falcon.sign(kp["secret_key"], b"msg")
        assert pqc_falcon.verify(kp["public_key"], b"msg", sig, kp["variant"])
"""
from __future__ import annotations

from typing import Optional

VARIANTS = ("Falcon-512", "Falcon-1024")   # 1024 ≈ NIST L5 (Algorand uses Falcon-1024)


def _oqs():
    import oqs
    return oqs


# `import oqs` can BLOCK (liboqs-python auto-builds the C library on first import). So the availability
# check runs in a SUBPROCESS with a hard timeout — status()/available() can never hang the caller.
def _probe(timeout: float = 4.0) -> list:
    import subprocess
    import sys
    try:
        out = subprocess.run(
            [sys.executable, "-c", "import oqs,json;print(json.dumps(oqs.get_enabled_sig_mechanisms()))"],
            capture_output=True, text=True, timeout=timeout)
        import json
        return json.loads(out.stdout.strip() or "[]") if out.returncode == 0 else []
    except Exception:
        return []


def available(variant: str = "Falcon-512") -> bool:
    return variant in _probe()


def status() -> dict:
    enabled = _probe()
    if enabled:
        return {"backend": "liboqs", "available": any(v in enabled for v in VARIANTS),
                "variants": [v for v in VARIANTS if v in enabled],
                "note": "experimental — Bitcoin signing stays secp256k1; Falcon is for the Tier-Q path"}
    return {"backend": None, "available": False, "variants": [],
            "note": "PQC backend unavailable (liboqs not built). Install a PREBUILT liboqs, then "
                    "`pip install --user liboqs-python`. POC degrades honestly until then."}


def generate(variant: str = "Falcon-512") -> dict:
    """Mint a Falcon keypair (hex). The secret key is client-side material — store it in the vault
    like any other secret; NEVER send it to a server (CP2048-QR: client-side keys, no escrow)."""
    if variant not in VARIANTS:
        raise ValueError(f"variant must be one of {VARIANTS}")
    oqs = _oqs()
    with oqs.Signature(variant) as signer:
        pub = signer.generate_keypair()
        sec = signer.export_secret_key()
    return {"scheme": "FN-DSA (Falcon)", "variant": variant, "tier": "Tier-Q (experimental)",
            "public_key": pub.hex(), "secret_key": sec.hex(),
            "note": "post-quantum identity key — NOT a Bitcoin key (BTC stays secp256k1)"}


def sign(secret_key_hex: str, message: bytes, variant: str = "Falcon-512") -> str:
    oqs = _oqs()
    with oqs.Signature(variant, bytes.fromhex(secret_key_hex)) as signer:
        return signer.sign(message).hex()


def verify(public_key_hex: str, message: bytes, signature_hex: str, variant: str = "Falcon-512") -> bool:
    oqs = _oqs()
    with oqs.Signature(variant) as verifier:
        return bool(verifier.verify(message, bytes.fromhex(signature_hex), bytes.fromhex(public_key_hex)))
