# SPDX-License-Identifier: GPL-3.0-or-later
# bankon-vault — signature-access GATING. Layered by design: ALPHA ships explicit per-sign approval;
# the Policy interface (spend limits / allowlists / timelocks / N-of-M) is the Step-3 layer.
#
# The gate is the thing standing between "the vault holds a key" and "a transaction gets signed".
# Defaults CLOSED: if no approver is wired, signing is refused.
from __future__ import annotations

from typing import Callable, Optional, Protocol


class SigningRequest:
    """Everything the gate needs to decide on ONE signature."""
    def __init__(self, entry_id: str, psbt_b64: str, summary: dict, requester: str = "local"):
        self.entry_id = entry_id
        self.psbt_b64 = psbt_b64
        self.summary = summary          # from BitcoinAdapter.decode_psbt: inputs/outputs/amounts/fee
        self.requester = requester


class Gate(Protocol):
    def approve(self, req: SigningRequest) -> bool: ...


class DenyAll:
    """The default. Nothing signs until a real gate is installed."""
    def approve(self, req: SigningRequest) -> bool:
        return False


class ApprovalGate:
    """ALPHA gate — an explicit human approval per signature. `approver(summary) -> bool` is shown the
    decoded PSBT (inputs/outputs/amounts/fee) and must return True. Wire it to a GTK dialog, a CLI
    prompt, or ICE's Thaw-to-sign confirm."""
    def __init__(self, approver: Callable[[dict], bool]):
        self._approver = approver

    def approve(self, req: SigningRequest) -> bool:
        try:
            return bool(self._approver(req.summary))
        except Exception:
            return False


# The programmable engine lives in policy_engine.py (spend limits, allow/deny lists, cooldown,
# timelocks, N-of-M quorum, audit log). Import it here so callers can `from ...policy import
# PolicyEngine, PolicyConfig`. It implements the Gate protocol, so it drops into gated_sign_psbt.
from .policy_engine import PolicyEngine, PolicyConfig, Decision   # noqa: E402,F401


def gated_sign_psbt(vault, adapter, entry_id: str, psbt_b64: str, gate: Gate,
                    requester: str = "local") -> str:
    """The one true signing path: decode → GATE → unlock-retrieve-sign-wipe. Raises on denial.

    `vault` must already be unlocked (the overseer decides WHO; the gate decides WHETHER-this-tx).
    The secret is retrieved, handed to the adapter to sign, and the plaintext bytearray is zeroed —
    the key never escapes this function.
    """
    summary = adapter.decode_psbt(psbt_b64)
    req = SigningRequest(entry_id, psbt_b64, summary, requester)
    # a PolicyEngine exposes evaluate() with detailed reasons (and may carry quorum votes on the req)
    if hasattr(gate, "evaluate"):
        decision = gate.evaluate(req, getattr(req, "quorum_votes", None))
        if not decision:
            raise PermissionError("signature denied by policy: " + "; ".join(decision.reasons))
    elif not gate.approve(req):
        raise PermissionError("signature denied by gate")
    secret = vault.retrieve(entry_id)                 # bytearray
    if secret is None:
        raise KeyError(f"no vault entry {entry_id!r}")
    try:
        signed = adapter.sign_psbt(secret.decode(), psbt_b64)
    finally:
        for i in range(len(secret)):                  # wipe the plaintext seed
            secret[i] = 0
    if hasattr(gate, "record_signed"):                # arm the cooldown after a successful sign
        gate.record_signed()
    return signed
