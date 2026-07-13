# SPDX-License-Identifier: CC0-1.0
"""bankon-ord — optional Bitcoin ordinals/inscriptions/runes module for BANKON (wraps ordinals/ord).

    from bankon_ord import OrdCli
    ord = OrdCli("testnet")          # or "mainnet" / "signet" / "regtest"
    print(ord.preflight())           # honest readiness report; never mutates

Isolated + optional: install only if you want ordinals. Ordinal vs cardinal wallets are strictly
separated (see isolation.py). Not affiliated with ordinals/ord; wraps its CLI (CC0).
"""
from .ord_cli import OrdCli, NetConfig, NETWORKS, resolve_network, OrdError, MIN_ORD
from .isolation import is_ordinal_wallet, assert_ordinal_wallet, guard_mutation, IsolationError

__all__ = ["OrdCli", "NetConfig", "NETWORKS", "resolve_network", "OrdError", "MIN_ORD",
           "is_ordinal_wallet", "assert_ordinal_wallet", "guard_mutation", "IsolationError"]
__version__ = "0.1.0-alpha"
