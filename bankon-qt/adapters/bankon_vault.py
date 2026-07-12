# SPDX-License-Identifier: GPL-3.0-or-later
# Thin BANKON bridge to the ISOLATED bankon-vault module. BANKON builds unsigned PSBTs (WaaS);
# this hands one to the vault for gated, sign-don't-export signing. No vault internals leak in here,
# so the module stays drop-in for any other chain/project.
import os
import sys

_VAULT_DIR = os.path.expanduser("~/bankon-tools/bankon-vault")
if os.path.isdir(_VAULT_DIR) and _VAULT_DIR not in sys.path:
    sys.path.insert(0, _VAULT_DIR)

try:
    from bankon_vault import BankonVault, PassphraseOverseer   # noqa: F401
    from bankon_vault.chains.btc import BitcoinAdapter
    from bankon_vault.policy import ApprovalGate, gated_sign_psbt
    HAVE_VAULT = True
    VAULT_ERR = None
except Exception as e:                                          # module optional — BANKON runs without it
    HAVE_VAULT = False
    VAULT_ERR = str(e)

DEFAULT_PATH = os.environ.get("BANKON_VAULT_PATH", os.path.expanduser("~/.bankon-vault"))


def vault_available() -> bool:
    return HAVE_VAULT


def describe_psbt(psbt_b64: str, network: str = "main") -> dict:
    """Decode a PSBT for a review UI (inputs/outputs/amounts/fee) — no key needed."""
    if not HAVE_VAULT:
        raise RuntimeError("bankon-vault not available: " + str(VAULT_ERR))
    return BitcoinAdapter(network).decode_psbt(psbt_b64)


def sign_unsigned_psbt(psbt_b64: str, passphrase: str, approve, *, entry_id: str = "btc.seed",
                       network: str = "main", path: str = DEFAULT_PATH) -> str:
    """Unlock the vault (passphrase) → gate-sign the PSBT → relock. Returns the signed PSBT (base64).

    `approve(summary) -> bool` is shown the decoded transaction and must confirm (defaults closed).
    The private key never leaves the vault module; only a signed PSBT comes back.
    """
    if not HAVE_VAULT:
        raise RuntimeError("bankon-vault not available: " + str(VAULT_ERR))
    with open(os.path.join(path, ".salt"), "rb") as f:
        salt = f.read()
    v = BankonVault(path)
    v.unlock(PassphraseOverseer(passphrase, salt))
    try:
        return gated_sign_psbt(v, BitcoinAdapter(network), entry_id, psbt_b64,
                               ApprovalGate(approve or (lambda _s: False)))
    finally:
        v.lock()
