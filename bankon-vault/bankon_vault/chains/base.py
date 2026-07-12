# SPDX-License-Identifier: GPL-3.0-or-later
# bankon-vault — chain adapter interface. The core is chain-agnostic; a ChainAdapter teaches it how
# to derive addresses, sign messages (for signature-gating), and sign transactions for ONE chain —
# without ever exporting the private key. Add ethereum.py / algorand.py / … the same way btc.py does.
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional


class ChainAdapter(ABC):
    name: str = "chain"

    @abstractmethod
    def new_secret(self) -> str:
        """Return fresh key material (mnemonic / seed / xprv) to be STORED as a vault entry."""

    @abstractmethod
    def address(self, secret: str, path: Optional[str] = None, kind: str = "default") -> str:
        """Derive a receive address from stored secret material."""

    @abstractmethod
    def sign_message(self, secret: str, message: str, path: Optional[str] = None) -> dict:
        """Sign `message` for signature-gating → {address, pubkey, signature}. Key never leaves."""

    @abstractmethod
    def verify_message(self, message: str, signature, expected) -> Optional[str]:
        """Verify a signature; return the signer identity (address/pubkey) if valid, else None.
        Shaped to plug straight into overseer.WalletSignatureOverseer(verifier=...)."""

    @abstractmethod
    def sign_psbt(self, secret: str, psbt_b64: str) -> str:
        """Sign a PSBT with the stored secret and return the signed PSBT (base64). Sign-don't-export."""
