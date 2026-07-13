# SPDX-License-Identifier: GPL-3.0-or-later
"""bankon-vault — the definitive chain-agnostic vault (BTC-first).

    from bankon_vault import BankonVault, PassphraseOverseer
    from bankon_vault.chains.btc import BitcoinAdapter

The core (BankonVault, overseers) is chain-independent. BTC lives in chains.btc and needs `embit`.
"""
from .core import BankonVault, VaultEntry, VaultError, VaultLocked, VAULT_VERSION
from .overseer import (Overseer, PassphraseOverseer, KeyfileOverseer,
                       WalletSignatureOverseer, DEFAULT_CHALLENGE)
from .policy import (DenyAll, ApprovalGate, SigningRequest, gated_sign_psbt,
                     PolicyEngine, PolicyConfig, Decision)

__all__ = ["BankonVault", "VaultEntry", "VaultError", "VaultLocked", "VAULT_VERSION",
           "Overseer", "PassphraseOverseer", "KeyfileOverseer", "WalletSignatureOverseer",
           "DEFAULT_CHALLENGE", "DenyAll", "ApprovalGate", "SigningRequest", "gated_sign_psbt",
           "PolicyEngine", "PolicyConfig", "Decision"]
__version__ = VAULT_VERSION
