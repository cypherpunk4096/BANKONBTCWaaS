# SPDX-License-Identifier: CC0-1.0
# bankon-ord tests — run WITHOUT ord installed (they exercise network resolution, isolation, gating,
# arg-building and honest degradation; they never call the real ord binary or the network).
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bankon_ord import OrdCli, resolve_network, is_ordinal_wallet, guard_mutation, IsolationError
from bankon_ord.ord_cli import OrdError, NETWORKS
from bankon_ord.isolation import assert_ordinal_wallet, MATERIAL_FUNDS_SATS


def test_all_networks_resolve_main_and_test():
    assert resolve_network("main").name == "mainnet"
    assert resolve_network("test").name == "testnet"
    for n in ("mainnet", "testnet", "signet", "regtest"):
        assert resolve_network(n).name == n
    # distinct RPC ports incl. mainnet 8332 + testnet 18332
    assert NETWORKS["mainnet"].rpc_port == 8332 and NETWORKS["testnet"].rpc_port == 18332
    assert NETWORKS["mainnet"].is_test is False and NETWORKS["testnet"].is_test is True


def test_unknown_network_rejected():
    try:
        resolve_network("liquid"); assert False
    except ValueError:
        pass


def test_ord_chain_flag_per_network():
    assert OrdCli("testnet").net.ord_chain == "testnet"
    assert OrdCli("mainnet").net.ord_chain == "mainnet"


def test_base_args_include_chain_and_rpc():
    o = OrdCli("testnet", ord_bin="/nonexistent/ord")
    args = o._base()
    assert "--chain" in args and "testnet" in args
    assert any(a.startswith("http://127.0.0.1:18332") for a in args)
    assert "--index" in args and o.net.name in o.index_dir     # per-network index isolation


def test_degrades_honestly_without_ord():
    o = OrdCli("mainnet", ord_bin="/nonexistent/ord")
    assert o.available() is False
    pf = o.preflight()
    assert pf["ord_installed"] is False and pf["network"] == "mainnet"
    try:
        o.wallet_balance("ord-x"); assert False
    except OrdError as e:
        assert "not installed" in str(e)


def test_isolation_naming():
    for good in ("ord-main", "ordinals", "my-inscriptions", "rune-vault"):
        assert is_ordinal_wallet(good)
    for bad in ("savings", "main", "cold", "btc-hot"):
        assert not is_ordinal_wallet(bad)
    try:
        assert_ordinal_wallet("savings"); assert False
    except IsolationError:
        pass


def test_gate_fails_closed_on_cardinal_wallet():
    g = guard_mutation("savings", balance_sats=0, approve=lambda p: True, action="inscribe")
    assert not g.ok and "ordinal" in g.reason.lower()


def test_gate_blocks_material_funds():
    g = guard_mutation("ord-main", balance_sats=MATERIAL_FUNDS_SATS, approve=lambda p: True)
    assert not g.ok and "material" in g.reason.lower() or "BTC" in g.reason


def test_gate_requires_approval():
    g = guard_mutation("ord-main", balance_sats=0, approve=lambda p: False)
    assert not g.ok and g.reason == "not approved"
    g2 = guard_mutation("ord-main", balance_sats=0, approve=lambda p: True)
    assert g2.ok


def test_inscribe_dry_run_is_gated_and_no_broadcast():
    o = OrdCli("testnet", ord_bin="/nonexistent/ord")
    seen = {}
    res = o.inscribe_gated("ord-test", "art.png", 5, lambda p: seen.update(p) or True,
                           balance_sats=0, dry_run=True)
    assert res["gated"] and "inscribe" in res["would_run"]
    assert seen["network"] == "testnet" and seen["fee_rate"] == 5     # approver saw the details
    # a cardinal wallet must be blocked even in dry-run
    try:
        o.inscribe_gated("savings", "art.png", 5, lambda p: True, dry_run=True); assert False
    except OrdError as e:
        assert "blocked" in str(e)


def test_isolation_rejects_cardinal_substrings():
    # regression: substring match wrongly accepted cardinal names — now token-boundary
    for cardinal in ("landlord", "password", "wordpress", "my-records", "fjord-savings", "accord"):
        assert not is_ordinal_wallet(cardinal), f"{cardinal} must NOT be an ordinal wallet"
    for ordinal in ("ord-main", "inscriptions", "my_rune_wallet", "insc-01", "ordinal-hot"):
        assert is_ordinal_wallet(ordinal), f"{ordinal} must be an ordinal wallet"

def test_guard_fails_closed_on_unknown_balance():
    g = guard_mutation("ord-main", balance_sats=None, approve=lambda p: True)
    assert not g.ok and "UNKNOWN" in g.reason
    g2 = guard_mutation("ord-main", balance_sats=None, approve=lambda p: True, allow_unknown_balance=True)
    assert g2.ok   # explicit override works


if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    ok = 0
    for n, f in fns:
        try:
            f(); print(f"  ✓ {n}"); ok += 1
        except Exception as e:
            print(f"  ✗ {n}: {type(e).__name__}: {e}")
    print(f"\n{ok}/{len(fns)} passed")
    sys.exit(0 if ok == len(fns) else 1)