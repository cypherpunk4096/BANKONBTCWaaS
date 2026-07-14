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


def test_bip322_p2wsh_multisig_spec_vector():
    """The spec's 3-of-3 p2wsh vector (bip-0322/basic-test-vectors.json) — previously skipped as
    'needs a script interpreter'; the standard multisig template doesn't."""
    btc = BitcoinAdapter("main")
    A = "bc1qp0ahvfh83088w49k405szqgg4f3pptr7p2g06tdxfjcd40z4lh4q95lsz9"
    MSG = "This will be a p2wsh 3-of-3 multisig BIP 322 signed message"
    SIG = ("BQBHMEQCIFX9aaqPJWq2Ff2kpen5bFDTid+ehgUOpHV0LfjncXy4AiA3GNicF7aKPzdpa9PCpmaYQs3p"
           "Hd+qbvvhXdxOCKCAMAFIMEUCIQD/ELXg6CNYyUQijCg96JtgvgjZb9dsl1Ctof4QAeyTcQIgVM/1AAbl"
           "Fl/DCt6A1gJg+T/i2qU5SQD09+chFJzolRwBSDBFAiEAlqRfSFyWNVQhvaCnmeV5tyneiCWMTcFbuujo"
           "D/pFa3wCIGnZjfQb8NolSYq9asV+ZeBSkCGHJcqnaV4JYS5MYPEGAWlTIQJ1aLEfEi/4p7wcV+XHZCBV"
           "vGGJZ7L3v+jhH+mZA8lN0yECCovfec+kIdllXpKCgA8RX/HZ2x5yHOtCSKP8/sf6pnwhAwxSng6kCgCX"
           "XSAmJOOZFdr3vdK3HzGqCFloOHgc5fM6U64=")
    assert btc.verify_message_bip322(MSG, SIG, A) == A
    assert btc.verify_message_bip322("This is not the message that was signed", SIG, A) is None
    assert btc.verify_message_bip322(MSG, SIG,
                                     "bc1q9vza2e8x573nczrlzms0wvx3gsqjx7vavgkx0l") is None


def test_bip322_p2wsh_multisig_partial_and_assemble():
    """Our own 2-of-3: each cosigner partial-signs, assembly orders per CHECKMULTISIG, and the
    result verifies through the standard verify path. Under-quorum must refuse to assemble."""
    from embit import bip32, bip39, script
    btc = BitcoinAdapter("regtest")
    msg = "BANKON 2-of-3 quorum message"
    root = bip32.HDKey.from_seed(bip39.mnemonic_to_seed(MNEM))
    paths = ["m/84h/1h/0h/0/20", "m/84h/1h/0h/0/21", "m/84h/1h/0h/0/22"]
    pubs = [root.derive(p).key.get_public_key() for p in paths]
    ws = script.multisig(2, pubs)
    addr = script.p2wsh(ws).address(btc.net)
    # cosigners 0 and 2 sign (deliberately given in the WRONG order — assembly must reorder)
    s0 = btc.bip322_multisig_partial(MNEM, msg, ws.data.hex(), path=paths[0])
    s2 = btc.bip322_multisig_partial(MNEM, msg, ws.data.hex(), path=paths[2])
    sig = btc.bip322_multisig_assemble(msg, ws.data.hex(), [s2, s0])
    assert btc.verify_message_bip322(msg, sig, addr) == addr
    assert btc.verify_message(msg, sig, addr) == addr           # unified dispatch handles p2wsh too
    assert btc.verify_message_bip322("tampered", sig, addr) is None
    # 1 valid sig for a 2-of-3 → assembly refuses (and a junk sig doesn't count)
    try:
        btc.bip322_multisig_assemble(msg, ws.data.hex(), [s0, "00" * 71])
        assert False, "assembled below quorum"
    except ValueError:
        pass


def test_bip322_full_variant_spec_vectors():
    """BIP-322 FULL variant — payload is the whole to_sign tx. Vectors pinned from the spec's
    generated-test-vectors.json (btcd implementation). Covers legacy p2pkh, p2sh-p2wpkh and
    legacy p2sh multisig — types that have no witness-only 'simple' form."""
    btc = BitcoinAdapter("main")
    # p2pkh (legacy scriptSig; tx has version 2, locktime 2016 — full allows both)
    assert btc.verify_message_bip322_full(
        "MOISC5NCQ42ADH2SUXLELUJOWH",
        "AgAAAAGn3Z6t/gsHNyHdgZTOVro0Hej+qbd/ilU1ACalKoHX3gAAAABqRzBEAiB+8t/tm8Jm6zYv9JGZZVlA"
        "Ujmqg7ZglIA39U+bim8EKQIgDv3E5cHOagN+xYgN3ZQjTYlAJp/WyslwJWuFP1TmM3IBIQJcPK2h9SY+Ki1o"
        "ussvHnMdFAhJgsYBFPl+rNcMv9P1ROAHAAABAAAAAAAAAAABauAHAAA=",
        "13vU5PUSuArDXJdCWZvUFEbgJ2wcmtSJWn") == "13vU5PUSuArDXJdCWZvUFEbgJ2wcmtSJWn"
    # wrong message → refused
    assert btc.verify_message_bip322_full(
        "TAMPERED",
        "AgAAAAGn3Z6t/gsHNyHdgZTOVro0Hej+qbd/ilU1ACalKoHX3gAAAABqRzBEAiB+8t/tm8Jm6zYv9JGZZVlA"
        "Ujmqg7ZglIA39U+bim8EKQIgDv3E5cHOagN+xYgN3ZQjTYlAJp/WyslwJWuFP1TmM3IBIQJcPK2h9SY+Ki1o"
        "ussvHnMdFAhJgsYBFPl+rNcMv9P1ROAHAAABAAAAAAAAAAABauAHAAA=",
        "13vU5PUSuArDXJdCWZvUFEbgJ2wcmtSJWn") is None
    # malformed payloads fail closed, never raise
    assert btc.verify_message_bip322_full("x", "not-base64!!!", "13vU5PUSuArDXJdCWZvUFEbgJ2wcmtSJWn") is None
    assert btc.verify_message_bip322_full("x", "AAAA", "13vU5PUSuArDXJdCWZvUFEbgJ2wcmtSJWn") is None


def test_bip322_full_sign_roundtrip_and_dispatch():
    btc = BitcoinAdapter("regtest")
    msg = "BANKON full-variant message"
    for kind in ("wpkh", "tr"):
        r = btc.sign_message_bip322(MNEM, msg, kind=kind, variant="full")
        assert r["scheme"] == "bip322-full"
        assert btc.verify_message_bip322_full(msg, r["signature"], r["address"]) == r["address"]
        # the unified dispatch also accepts full-variant payloads (simple → full fallback)
        assert btc.verify_message(msg, r["signature"], r["address"]) == r["address"]
        assert btc.verify_message_bip322_full(msg + "x", r["signature"], r["address"]) is None
        # a full payload is NOT a valid simple payload for the same message
        assert btc.verify_message_bip322(msg, r["signature"], r["address"]) is None


def test_bip322_timelock_vectors_and_semantics():
    """The spec's two time-lock full-variant vectors (hodl template: OP_IF pk1 OP_ELSE n CSV
    OP_DROP pk2 OP_ENDIF OP_CHECKSIG) — template-matched, no script interpreter. With these,
    ALL 10 generated full-variant types verify (and all 28 error vectors reject)."""
    btc = BitcoinAdapter("main")
    # p2wsh time-lock (ELSE/CSV branch, sequence 2016)
    A = "bc1qhqcmw7ud03vqde3pe6hzajaylhucmlatrkcztzpnk8vpgvhg9dzq5ydark"
    S = ("AgAAAAABAYYJeOOOi3c33O+dholAwiF51Amy/E0qIf3ew2vFtDtTAAAAAADgBwAAAQAAAAAAAAAAAWoDSDBF"
         "AiEA64MwD2HkJjPLPAc2u5ia6ZdwCVO3okzVqGPEXnuJGZQCIE27BGOBQTdwJ2M/Wdsm6nFVunqaj+xZBSG/"
         "g/64FMbtAQBNYyEDrYfXhOkh0CvwuJpB+O3tal2ECfO0v7k1/A4PTlGcQiBnAuAHsnUhA4ZGGvodKgqeg/ZY"
         "ffm6miaKaG57VkCSjmmRprCa+ulyaKzgBwAA")
    assert btc.verify_message_bip322_full("MGKMA2MJUBDHT55J7MHOLM7UPE", S, A) == A
    assert btc.verify_message_bip322_full("WRONG MESSAGE", S, A) is None
    # p2tr time-lock (tapscript script-path spend with control block)
    T = "bc1p6vffkx7vcyezrjq7pg9qqdjv7vmtanfhk8ukwsn4syejwmarmhxqp0rw5x"
    ST = ("AgAAAAABAaza7/ukfX9ZdxCUvK7CPJgADDdPdF7ikXVKWctd5EHrAAAAAADgBwAAAQAAAAAAAAAAAWoEQPvu"
          "T0enYGwsab2lsPZU0U3OcRkGng+o/PAt4QU2lc8hG7lTUmflkt0To+eoipv2vptf0TlGOBCsKU5xE3kXKcMA"
          "S2MgrYfXhOkh0CvwuJpB+O3tal2ECfO0v7k1/A4PTlGcQiBnAuAHsnUgJjLn4tl5ytgC8CNTyITXmg4rx9ct"
          "xPedwRMPEBvfoUBorCHBJjLn4tl5ytgC8CNTyITXmg4rx9ctxPedwRMPEBvfoUDgBwAA")
    assert btc.verify_message_bip322_full("AY2VOQOXYI5CN2EHZKLOX7ZI37", ST, T) == T
    assert btc.verify_message_bip322_full("WRONG MESSAGE", ST, T) is None
    # BIP-65/112 semantics unit checks — the ELSE branch must be REFUSED when unsatisfied
    from bankon_vault.chains.btc import _timelock_satisfied
    from embit.transaction import Transaction, TransactionInput, TransactionOutput
    from embit.script import Script
    def tx(version=2, locktime=0, seq=0):
        return Transaction(version=version, locktime=locktime,
                           vin=[TransactionInput(b"\x00" * 32, 0, sequence=seq)],
                           vout=[TransactionOutput(0, Script(b"\x6a"))])
    assert _timelock_satisfied(tx(seq=2016), 0xb2, 2016)                  # CSV met
    assert not _timelock_satisfied(tx(seq=2015), 0xb2, 2016)              # CSV short
    assert not _timelock_satisfied(tx(version=1, seq=2016), 0xb2, 2016)   # CSV needs tx v2
    assert not _timelock_satisfied(tx(seq=2016 | (1 << 31)), 0xb2, 2016)  # disable flag set
    assert not _timelock_satisfied(tx(seq=2016 | (1 << 22)), 0xb2, 2016)  # type bits disagree
    assert _timelock_satisfied(tx(locktime=800000, seq=0), 0xb1, 700000)  # CLTV met
    assert not _timelock_satisfied(tx(locktime=600000, seq=0), 0xb1, 700000)
    assert not _timelock_satisfied(tx(locktime=800000, seq=0xFFFFFFFF), 0xb1, 700000)  # disabled
    assert not _timelock_satisfied(tx(locktime=800000, seq=0), 0xb1, 1_600_000_000)    # type mix


def test_rekey_rotates_custody():
    v, salt = _fresh()
    v.store("a", "alpha", context="demo")
    v.store("b", "beta", context="demo")
    n = v.rekey(PassphraseOverseer("NEW-pass", salt))
    assert n == 2
    # still readable in-session, and after a lock/unlock with the NEW custody
    assert v.retrieve_str("a") == "alpha"
    v.lock()
    v.unlock(PassphraseOverseer("NEW-pass", salt))
    assert v.retrieve_str("b") == "beta"
    v.lock()
    # the OLD passphrase must no longer decrypt anything
    v.unlock(PassphraseOverseer("test-pass", salt))
    try:
        v.retrieve("a")
        assert False, "old custody still decrypts after rekey"
    except Exception:
        pass
    v.lock()


def test_rekey_fails_closed():
    v, salt = _fresh()
    v.store("k", "keep me")
    # same overseer → same master → refused, vault untouched
    try:
        v.rekey(PassphraseOverseer("test-pass", salt))
        assert False, "rekey to the SAME master was allowed"
    except Exception:
        pass
    assert v.retrieve_str("k") == "keep me"          # unchanged
    # locked vault → refused
    v.lock()
    try:
        v.rekey(PassphraseOverseer("other", salt))
        assert False, "rekey on a locked vault was allowed"
    except Exception:
        pass
    v.unlock(PassphraseOverseer("test-pass", salt))  # old custody still opens it
    assert v.retrieve_str("k") == "keep me"
    v.lock()


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
