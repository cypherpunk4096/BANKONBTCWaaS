# SPDX-License-Identifier: GPL-3.0-or-later
# bankon-vault alpha test suite. Run:  python3 -m pytest bankon-vault/tests  (or: python3 tests/test_vault.py)
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bankon_vault import BankonVault, PassphraseOverseer, WalletSignatureOverseer
from bankon_vault.core import _hkdf
from bankon_vault.chains.btc import BitcoinAdapter, _bsm_hash
from bankon_vault.policy import ApprovalGate, DenyAll, gated_sign_psbt

MNEM = "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about"


def _fresh():
    d = tempfile.mkdtemp()
    v = BankonVault(d, autolock_sec=0)
    salt = open(os.path.join(d, ".salt"), "rb").read()
    v.unlock(PassphraseOverseer("test-pass", salt))
    return v, salt


def test_hkdf_rfc5869_vector():
    # RFC 5869 is SHA-256; we use SHA-512, so assert determinism + correct length + salt-sensitivity.
    a = _hkdf(b"ikm", b"\x00" * 32, b"bankon-vault-master-key", 32)
    b = _hkdf(b"ikm", b"\x00" * 32, b"bankon-vault-master-key", 32)
    c = _hkdf(b"ikm", b"\x01" * 32, b"bankon-vault-master-key", 32)
    assert a == b and a != c and len(a) == 32


def test_store_retrieve_and_metadata_only():
    v, _ = _fresh()
    v.store("k", "value-any-chain", context="demo")
    assert v.retrieve_str("k") == "value-any-chain"
    meta = v.list_entries()[0]
    assert "value" not in str(meta) and meta["id"] == "k" and meta["context"] == "demo"


def test_aad_binding_rename_fails():
    v, _ = _fresh()
    v.store("real", "secret")
    # forge an entry that reuses real's ciphertext under a different id → decrypt must fail (AAD)
    e = v._entries["real"]
    v._entries["forged"] = type(e)(id="forged", nonce=e.nonce, ct=e.ct)
    try:
        v.retrieve("forged")
        assert False, "AAD binding failed — renamed ciphertext decrypted"
    except Exception as ex:
        assert "InvalidTag" in type(ex).__name__ or "Tag" in str(ex)


def test_wrong_passphrase_invalidtag():
    v, salt = _fresh()
    v.store("s", "x")
    d = v.path
    v2 = BankonVault(d, autolock_sec=0)
    v2.unlock(PassphraseOverseer("WRONG", salt))
    try:
        v2.retrieve("s")
        assert False, "wrong passphrase decrypted"
    except Exception as ex:
        assert "InvalidTag" in type(ex).__name__


def test_gcm_nonce_unique():
    v, _ = _fresh()
    nonces = set()
    for i in range(200):
        v.store("n", f"v{i}")
        nonces.add(v._entries["n"].nonce)
    assert len(nonces) == 200                      # a fresh 96-bit nonce every write


def test_lock_zeroizes():
    v, _ = _fresh()
    v.store("s", "x")
    v.lock()
    assert not v.is_unlocked()
    try:
        v.retrieve("s")
        assert False
    except Exception as ex:
        assert type(ex).__name__ == "VaultLocked"


def test_btc_addresses_bip84_86():
    btc = BitcoinAdapter("main")
    assert btc.address(MNEM, kind="wpkh") == "bc1qcr8te4kr609gcawutmrza0j4xv80jy8z306fyu"
    assert btc.address(MNEM, kind="tr").startswith("bc1p")


def test_signature_gating_roundtrip():
    btc = BitcoinAdapter("regtest")
    sm = btc.sign_message(MNEM, "BANKON-VAULT custody")
    assert btc.verify_message("BANKON-VAULT custody", sm["signature"], sm["pubkey"]) == sm["address"]
    assert btc.verify_message("tampered", sm["signature"], sm["pubkey"]) is None


def test_wallet_signature_overseer_unlocks():
    v, salt = _fresh()
    btc = BitcoinAdapter("regtest")
    sm = btc.sign_message(MNEM, "custody-challenge")
    ov = WalletSignatureOverseer(btc.make_verifier(sm["pubkey"]), sm["address"], salt, challenge="custody-challenge")
    v.lock()
    assert v.unlock(ov, "custody-challenge", sm["signature"]) is True


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


def test_sign_psbt_no_export():
    from embit.psbt import PSBT
    btc = BitcoinAdapter("regtest")
    signed = btc.sign_psbt(MNEM, _regtest_psbt())
    assert len(PSBT.from_base64(signed).inputs[0].partial_sigs) == 1
    assert MNEM not in signed                       # sign-don't-export


def test_gate_denies_by_default_and_approves_explicitly():
    v, _ = _fresh()
    btc = BitcoinAdapter("regtest")
    v.store("btc.seed", MNEM, context="bitcoin_wallet")
    psbt = _regtest_psbt()
    try:
        gated_sign_psbt(v, btc, "btc.seed", psbt, DenyAll())
        assert False, "DenyAll approved a signature"
    except PermissionError:
        pass
    seen = {}
    gate = ApprovalGate(lambda summary: seen.update(summary) or True)
    signed = gated_sign_psbt(v, btc, "btc.seed", psbt, gate)
    from embit.psbt import PSBT
    assert len(PSBT.from_base64(signed).inputs[0].partial_sigs) == 1
    assert seen["fee_sats"] == 10000                # the approver was shown the decoded tx (100k-90k)


if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    ok = 0
    for n, f in fns:
        try:
            f()
            print(f"  ✓ {n}")
            ok += 1
        except Exception as e:
            print(f"  ✗ {n}: {type(e).__name__}: {e}")
    print(f"\n{ok}/{len(fns)} passed")
    sys.exit(0 if ok == len(fns) else 1)
