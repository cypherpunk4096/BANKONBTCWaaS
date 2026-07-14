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


def test_bip137_kat_and_core_interop():
    """Known-answer + external-implementation vectors for BIP-137 recoverable signatures.
    Both were cross-verified live against Bitcoin Core v31 `signmessage`/`verifymessage`."""
    btc = BitcoinAdapter("regtest")
    msg = "BANKON BIP-137 cross-check"
    # KAT: our deterministic (RFC-6979) signature — Bitcoin Core `verifymessage` returned true.
    r = btc.sign_message_compact(MNEM, msg, path="m/44h/1h/0h/0/0", kind="pkh")
    assert r["address"] == "mkpZhYtJu2r87Js3pDiWJDmPte2NRZ8bJV"
    assert r["signature"] == ("H097d2Hj4qZKXS4Wmp+5svsKgwAwTDGkrEPOL377jlH5FyX0xaI2"
                              "B9HxYU5Rj1jnG68MsMdCguCGNMRGJIOcofc=")
    # external vector: a signature PRODUCED BY Bitcoin Core v31 → our recovery must match.
    core_addr = "mwETGghMEQ8FrSQdEuHBkiqz3qRcTsnywA"
    core_sig = ("H+bxPxz1HPg5YNu5RmcXuVebL3pk3rzTRzhFCbm5kkUneVIagy3ukGLq7OJls"
                "OKlSXURXw2bjMM9zcHMq1DcMbI=")
    assert btc.recover_address(msg, core_sig) == core_addr
    assert btc.verify_message(msg, core_sig, core_addr) == core_addr


def test_bip137_address_pinning_all_kinds_and_fail_closed():
    btc = BitcoinAdapter("regtest")
    msg = "gate challenge"
    for kind in ("pkh", "sh-wpkh", "wpkh"):
        r = btc.sign_message_compact(MNEM, msg, path="m/84h/1h/0h/0/5", kind=kind)
        # address-only pinning verifies (no pubkey supplied anywhere)
        assert btc.verify_message(msg, r["signature"], r["address"]) == r["address"], kind
        # fail-closed: tampered message, wrong address, malformed signature
        assert btc.verify_message(msg + "x", r["signature"], r["address"]) is None
        other = btc.sign_message_compact(MNEM, msg, path="m/84h/1h/0h/0/6", kind=kind)["address"]
        assert btc.verify_message(msg, r["signature"], other) is None
        assert btc.verify_message(msg, "AAAA", r["address"]) is None
    # taproot has no BIP-137 header range → must refuse, not mis-sign
    try:
        btc.sign_message_compact(MNEM, msg, kind="tr")
        assert False, "tr accepted"
    except ValueError:
        pass
    # a BIP-137 verifier plugs straight into the overseer path
    r = btc.sign_message_compact(MNEM, msg, kind="wpkh")
    assert btc.make_verifier(r["address"])(msg, r["signature"]) == r["address"]


def test_bip322_spec_vectors():
    """BIP-322 'simple' vectors pinned from bitcoin/bips bip-0322/basic-test-vectors.json."""
    from bankon_vault.chains.btc import _bip322_to_spend, _bip322_to_sign
    from embit.script import address_to_scriptpubkey
    from embit import hashes
    btc = BitcoinAdapter("main")
    A = "bc1q9vza2e8x573nczrlzms0wvx3gsqjx7vavgkx0l"
    # tagged message hash + virtual tx ids are byte-exact per spec
    assert hashes.tagged_hash("BIP0322-signed-message", b"").hex() == \
        "c90c269c4f8fcbe6880f72a721ddfbf1914268a794cbb21cfafee13770ae19f1"
    spend = _bip322_to_spend(address_to_scriptpubkey(A), "Hello World")
    assert spend.txid().hex() == "b79d196740ad5217771c1098fc4a4b51e0535c32236c71f1ea4d61a2d603352b"
    assert _bip322_to_sign(spend.txid()).txid().hex() == \
        "88737ae86f2077145f93cc4b153ae9a1cb8d56afa511988c149c5c8c9d93bddf"
    # spec p2wpkh signatures verify; wrong message / wrong address fail closed
    sig_empty = ("AkcwRAIgM2gBAQqvZX15ZiysmKmQpDrG83avLIT492QBzLnQIxYCIBaTpOaD20qRlEylyxFSeEA2"
                 "ba9YOixpX8z46TSDtS40ASECx/EgAxlkQpQ9hYjgGu6EBCPMVPwVIVJqO4XCsMvViHI=")
    assert btc.verify_message_bip322("", sig_empty, A) == A
    assert btc.verify_message_bip322("Wrong message that was not signed", sig_empty, A) is None
    assert btc.verify_message_bip322("", sig_empty,
                                     "bc1qp0ahvfh83088w49k405szqgg4f3pptr7p2g0f0") is None
    # spec p2tr key-path signature (the address type BIP-137 cannot cover)
    T = "bc1pss0zhytly75awhm6x2hhvd5lnzv3vssgrf9axfheq8ldyzn88ges79fler"
    sig_tr = ("AUCJYOwOjxYAvatTAGYaVlNXBVyFuc4MwNQkOuK2tl8xhfKDONd0NjfYyNSYcRqeCp8hsAnCEPHA"
              "VEkO9h6vbQ/R")
    assert btc.verify_message_bip322("No prefix fallback", sig_tr, T) == T
    assert btc.verify_message_bip322("tampered", sig_tr, T) is None
    # malformed inputs fail closed, never raise
    assert btc.verify_message_bip322("", "not-valid-base64!!!", A) is None
    assert btc.verify_message_bip322("", "AA==", A) is None          # empty witness stack


def test_bip322_sign_roundtrip_and_dispatch():
    btc = BitcoinAdapter("regtest")
    msg = "BANKON taproot gate"
    # taproot — the whole point of BIP-322 here
    r = btc.sign_message_bip322(MNEM, msg, kind="tr")
    assert r["address"].startswith("bcrt1p") and r["scheme"] == "bip322-simple"
    assert btc.verify_message_bip322(msg, r["signature"], r["address"]) == r["address"]
    # native segwit roundtrip
    w = btc.sign_message_bip322(MNEM, msg, kind="wpkh")
    assert btc.verify_message_bip322(msg, w["signature"], w["address"]) == w["address"]
    # the unified verify_message dispatch routes an address + witness sig to BIP-322 …
    assert btc.verify_message(msg, r["signature"], r["address"]) == r["address"]
    assert btc.verify_message(msg + "x", r["signature"], r["address"]) is None
    # … while BIP-137 compact sigs still take the recovery path (no regression)
    c = btc.sign_message_compact(MNEM, msg, kind="wpkh")
    assert btc.verify_message(msg, c["signature"], c["address"]) == c["address"]
    # a BIP-322 verifier feeds the overseer path, taproot included
    assert btc.make_verifier(r["address"])(msg, r["signature"]) == r["address"]


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
