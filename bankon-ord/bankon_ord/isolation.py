# SPDX-License-Identifier: CC0-1.0
# bankon-ord — wallet ISOLATION. The single most important safety rule for ordinals:
#
#   Bitcoin Core is NOT inscription-aware. A normal spend from a wallet that holds inscriptions can
#   send the inscribed sat as ordinary change and DESTROY the inscription. Therefore ordinal-bearing
#   ("ordinal") and plain-BTC ("cardinal") wallets must NEVER mix, and the generic BANKON/vault BTC
#   signer must NEVER be pointed at an ordinal wallet's UTXOs.
#
# This module encodes that as guardrails: a naming convention, a fail-closed check before any mutating
# ord action, and a "material funds" warning. Reads are always allowed; mutations are gated.
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Optional

ORDINAL_MARKERS = ("ord", "ordinal", "inscription", "insc", "rune")   # a wallet name must declare intent
MATERIAL_FUNDS_SATS = 10_000_000        # 0.1 BTC — refuse ordinals ops on a wallet this "hot" (docs' warning)


class IsolationError(RuntimeError):
    pass


def is_ordinal_wallet(name: str) -> bool:
    """An ordinal wallet must DECLARE itself with a marker as a WHOLE TOKEN (e.g. 'ord-main',
    'inscriptions', 'my_rune_wallet') — NOT as a substring, or cardinal names like 'landlord',
    'password', 'my-records' would wrongly qualify and be allowed to inscribe/spend."""
    tokens = [t for t in re.split(r"[^a-z0-9]+", (name or "").lower()) if t]
    # a token must START with a marker (handles plurals: ordinals/inscriptions/runes) — so 'landlord',
    # 'wordpress', 'accord', 'fjord', 'password', 'my-records' (no token begins with a marker) are OUT.
    return any(tok.startswith(m) for tok in tokens for m in ORDINAL_MARKERS)


def assert_ordinal_wallet(name: str) -> None:
    if not is_ordinal_wallet(name):
        raise IsolationError(
            f"wallet {name!r} is not marked as an ordinal wallet. Name it with 'ord'/'ordinal'/"
            f"'inscription' and keep it SEPARATE from any wallet holding plain BTC — a normal spend "
            f"on an inscription wallet can destroy the inscription.")


def assert_not_material_funds(balance_sats: Optional[int]) -> None:
    if balance_sats is not None and balance_sats >= MATERIAL_FUNDS_SATS:
        raise IsolationError(
            f"this wallet holds {balance_sats/1e8:.4f} BTC (>= {MATERIAL_FUNDS_SATS/1e8} BTC). The ord "
            f"docs warn against using ordinals tooling with material funds — move plain BTC to a "
            f"cardinal wallet first.")


@dataclass
class GuardResult:
    ok: bool
    reason: str = ""


def guard_mutation(wallet: str, balance_sats: Optional[int] = None,
                   approve: Optional[Callable[[dict], bool]] = None,
                   action: str = "inscribe", details: Optional[dict] = None,
                   allow_unknown_balance: bool = False) -> GuardResult:
    """Fail-closed gate for any ord action that MOVES coins/inscriptions (inscribe/send/etch).

      1) the wallet must be an ordinal wallet (isolation),
      2) its balance must be KNOWN and not material — an UNKNOWN balance fails closed (a hiccup in the
         balance fetch must not let a 5-BTC wallet through), unless allow_unknown_balance is set,
      3) a human must approve (shown the action + details); default = deny.
    """
    if balance_sats is None and not allow_unknown_balance:
        return GuardResult(False, "wallet balance is UNKNOWN — refusing (fail-closed). Confirm the "
                                  "balance, or pass allow_unknown_balance to override deliberately.")
    try:
        assert_ordinal_wallet(wallet)
        assert_not_material_funds(balance_sats)
    except IsolationError as e:
        return GuardResult(False, str(e))
    payload = {"action": action, "wallet": wallet, **(details or {})}
    if not (approve and approve(payload)):
        return GuardResult(False, "not approved")
    return GuardResult(True, "approved")
