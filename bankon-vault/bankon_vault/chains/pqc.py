# SPDX-License-Identifier: GPL-3.0-or-later
# bankon-vault — the Tier-Q IDENTITY adapter (ML-DSA / FIPS 204). Completes the CP2048-QR roadmap
# item "an FN-DSA (Falcon) / ML-DSA overseer+adapter": a post-quantum ChainAdapter that drops in
# beside btc.py without touching the core.
#
# HONEST SCOPE — read before using:
#   • This is an IDENTITY/GATING adapter, not a coin adapter. It mints and uses post-quantum
#     ML-DSA keys for message signing, approval, and PolicyEngine quorum. It cannot sign Bitcoin
#     transactions — Bitcoin consensus verifies only secp256k1 (Tier-C), so sign_psbt refuses.
#   • Do NOT use it with WalletSignatureOverseer for KEY DERIVATION: ML-DSA signatures are
#     RANDOMIZED, so signature-as-IKM yields a different master every unlock. Post-quantum
#     CUSTODY is pqc_hybrid.HybridPQCOverseer (ML-KEM) — a KEM gives the stable secret that key
#     derivation needs. This adapter covers the post-quantum *authorization* side.
from __future__ import annotations

import json
from typing import Optional

from .base import ChainAdapter
from .. import pqc_mldsa


class MLDSAAdapter(ChainAdapter):
    """Post-quantum identity adapter. Secrets are JSON keypair blobs minted by new_secret()."""
    name = "pqc-mldsa"

    def __init__(self, variant: str = pqc_mldsa.DEFAULT_VARIANT):
        if variant not in pqc_mldsa.VARIANTS:
            raise ValueError(f"variant must be one of {pqc_mldsa.VARIANTS}")
        self.variant = variant

    # ---- key material ----
    def new_secret(self) -> str:
        """A fresh ML-DSA keypair as a JSON string — STORE this as the vault entry."""
        kp = pqc_mldsa.generate(self.variant)
        return json.dumps({"variant": kp["variant"], "public_key": kp["public_key"],
                           "secret_key": kp["secret_key"]})

    def _keypair(self, secret: str) -> dict:
        kp = json.loads(secret)
        if kp.get("variant") != self.variant:
            raise ValueError(f"entry is {kp.get('variant')!r}, adapter is {self.variant!r}")
        return kp

    # ---- identity ----
    def address(self, secret: str, path: Optional[str] = None, kind: str = "default") -> str:
        """The Tier-Q identity = a stable fingerprint of the ML-DSA pubkey (no chain address)."""
        return pqc_mldsa.fingerprint(self._keypair(secret)["public_key"])

    def pubkey(self, secret: str) -> str:
        return self._keypair(secret)["public_key"]

    # ---- signature gating (sign-don't-export) ----
    def sign_message(self, secret: str, message: str, path: Optional[str] = None) -> dict:
        kp = self._keypair(secret)
        sig = pqc_mldsa.sign(kp["secret_key"], message.encode(), self.variant)
        return {"address": pqc_mldsa.fingerprint(kp["public_key"]), "pubkey": kp["public_key"],
                "signature": sig, "scheme": f"{self.variant} (FIPS 204)"}

    def verify_message(self, message: str, signature, expected) -> Optional[str]:
        """`expected` = the ML-DSA pubkey hex. Returns the identity fingerprint if valid, else
        None. PolicyEngine(verify_sig=adapter.verify_message) makes signing quorum post-quantum."""
        m = message.encode() if isinstance(message, str) else bytes(message)
        try:
            ok = pqc_mldsa.verify(str(expected), m, str(signature), self.variant)
        except Exception:
            return None
        return pqc_mldsa.fingerprint(str(expected)) if ok else None

    # ---- transactions: refused, honestly ----
    def sign_psbt(self, secret: str, psbt_b64: str) -> str:
        raise NotImplementedError(
            "Bitcoin consensus cannot verify ML-DSA — BTC transaction signing stays with the "
            "secp256k1 BitcoinAdapter (Tier-C). This adapter provides Tier-Q identity/quorum only.")
