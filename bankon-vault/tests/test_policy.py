# SPDX-License-Identifier: GPL-3.0-or-later
# bankon-vault policy-engine tests. Run: python3 tests/test_policy.py
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bankon_vault import (BankonVault, PassphraseOverseer, PolicyEngine, PolicyConfig,
                          SigningRequest, gated_sign_psbt)
from bankon_vault.chains.btc import BitcoinAdapter

MNEM = "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about"


def _summary(fee=1000, outs=None, total=None):
    outs = outs if outs is not None else [{"address": "bcrt1qaddr", "sats": 5000}]
    return {"fee_sats": fee, "outputs": outs, "out_sats": total if total is not None else sum(o["sats"] for o in outs),
            "network": "regtest"}


def _req(summary, entry="btc.seed", psbt="cHNidP8B"):
    return SigningRequest(entry, psbt, summary)


def _regtest_psbt():
    from embit import bip32, bip39, script
    from embit.psbt import PSBT, DerivationPath
    from embit.transaction import Transaction, TransactionInput, TransactionOutput
    root = bip32.HDKey.from_seed(bip39.mnemonic_to_seed(MNEM))
    path = [84 + 0x80000000, 1 + 0x80000000, 0x80000000, 0, 0]
    leaf = root.derive(path)
    prev = Transaction(vin=[], vout=[TransactionOutput(100000, script.p2wpkh(leaf))])
    tx = Transaction(vin=[TransactionInput(bytes.fromhex(prev.txid().hex()), 0)],
                     vout=[TransactionOutput(90000, script.p2wpkh(root.derive("m/84h/1h/0h/0/1")))])
    p = PSBT(tx)
    p.inputs[0].witness_utxo = prev.vout[0]
    p.inputs[0].bip32_derivations[leaf.key.get_public_key()] = DerivationPath(root.my_fingerprint, path)
    return p.to_base64()


def _engine(cfg, d, **kw):
    return PolicyEngine(cfg, d, approver=kw.pop("approver", lambda s: True), **kw)


def test_allow_when_all_pass():
    d = tempfile.mkdtemp()
    e = _engine(PolicyConfig(max_fee_sats=2000), d)
    assert e.evaluate(_req(_summary(fee=1000))).allowed


def test_max_fee_blocks():
    d = tempfile.mkdtemp()
    dec = _engine(PolicyConfig(max_fee_sats=500), d).evaluate(_req(_summary(fee=1000)))
    assert not dec.allowed and any("fee" in r for r in dec.reasons)


def test_max_output_and_total():
    d = tempfile.mkdtemp()
    dec = _engine(PolicyConfig(max_output_sats=1000), d).evaluate(_req(_summary(outs=[{"address": "a", "sats": 5000}])))
    assert not dec.allowed and any("max_output" in r for r in dec.reasons)
    dec2 = _engine(PolicyConfig(max_total_out_sats=1000), d).evaluate(_req(_summary(total=5000)))
    assert not dec2.allowed and any("total" in r for r in dec2.reasons)


def test_allowlist_and_denylist():
    d = tempfile.mkdtemp()
    dec = _engine(PolicyConfig(allowlist=["good"]), d).evaluate(_req(_summary(outs=[{"address": "bad", "sats": 1}])))
    assert not dec.allowed and any("allowlist" in r for r in dec.reasons)
    ok = _engine(PolicyConfig(allowlist=["good"]), d).evaluate(_req(_summary(outs=[{"address": "good", "sats": 1}])))
    assert ok.allowed
    dd = _engine(PolicyConfig(denylist=["evil"]), d).evaluate(_req(_summary(outs=[{"address": "evil", "sats": 1}])))
    assert not dd.allowed and any("denylist" in r for r in dd.reasons)


def test_timelock_epoch_and_height():
    d = tempfile.mkdtemp()
    future = int(time.time()) + 3600
    dec = _engine(PolicyConfig(not_before_epoch=future), d).evaluate(_req(_summary()))
    assert not dec.allowed and any("timelock" in r for r in dec.reasons)
    dh = PolicyEngine(PolicyConfig(not_before_height=1000), d, current_height=lambda: 500,
                      approver=lambda s: True).evaluate(_req(_summary()))
    assert not dh.allowed and any("height" in r for r in dh.reasons)
    dh_ok = PolicyEngine(PolicyConfig(not_before_height=1000), d, current_height=lambda: 1001,
                         approver=lambda s: True).evaluate(_req(_summary()))
    assert dh_ok.allowed


def test_cooldown_after_signed():
    d = tempfile.mkdtemp()
    e = _engine(PolicyConfig(cooldown_sec=60), d)
    assert e.evaluate(_req(_summary())).allowed        # first is fine
    e.record_signed()
    dec = e.evaluate(_req(_summary()))
    assert not dec.allowed and any("cooldown" in r for r in dec.reasons)


def test_require_approval_denies_without_approver():
    d = tempfile.mkdtemp()
    e = PolicyEngine(PolicyConfig(require_approval=True), d, approver=lambda s: False)
    assert not e.evaluate(_req(_summary())).allowed


def test_quorum_n_of_m():
    d = tempfile.mkdtemp()
    btc = BitcoinAdapter("regtest")
    # two distinct signers (different derivation paths → different pubkeys)
    a = btc.sign_message(MNEM, "x", path="m/84h/1h/0h/0/10")
    b = btc.sign_message(MNEM, "x", path="m/84h/1h/0h/0/11")
    cfg = PolicyConfig(quorum_n=2, quorum_approvers=[a["pubkey"], b["pubkey"]], require_approval=False)
    e = PolicyEngine(cfg, d, verify_sig=btc.verify_message, approver=lambda s: True)
    req = _req(_summary())
    msg = e.quorum_message(req)
    # each approver signs the quorum message
    va = {"pubkey": a["pubkey"], "sig": btc.sign_message(MNEM, msg, path="m/84h/1h/0h/0/10")["signature"]}
    vb = {"pubkey": b["pubkey"], "sig": btc.sign_message(MNEM, msg, path="m/84h/1h/0h/0/11")["signature"]}
    assert not e.evaluate(req, [(va["pubkey"], va["sig"])]).allowed        # 1/2 → deny
    assert e.evaluate(req, [(va["pubkey"], va["sig"]), (vb["pubkey"], vb["sig"])]).allowed  # 2/2 → allow


def test_persist_config_roundtrip():
    d = tempfile.mkdtemp()
    e = _engine(PolicyConfig(max_fee_sats=1234, allowlist=["a", "b"]), d)
    e.save_config()
    loaded = PolicyEngine.load_config(d)
    assert loaded.max_fee_sats == 1234 and loaded.allowlist == ["a", "b"]


def test_engine_as_gate_in_gated_sign():
    d = tempfile.mkdtemp()
    v = BankonVault(d, autolock_sec=0)
    salt = open(os.path.join(d, ".salt"), "rb").read()
    v.unlock(PassphraseOverseer("p", salt))
    v.store("btc.seed", MNEM, "bitcoin_wallet")
    psbt = _regtest_psbt()
    # policy that blocks (fee cap 0) → gated_sign_psbt must refuse and NOT sign
    eng = PolicyEngine(PolicyConfig(max_fee_sats=0), d, approver=lambda s: True)
    try:
        gated_sign_psbt(v, BitcoinAdapter("regtest"), "btc.seed", psbt, eng)
        assert False, "policy did not block"
    except PermissionError as e:
        assert "policy" in str(e) or "fee" in str(e)


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
