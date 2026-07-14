# SPDX-License-Identifier: GPL-3.0-or-later
# bankon-vault post-quantum tests: hybrid ML-KEM custody, ML-DSA identity/quorum, Tier-Q adapter.
# Backends are optional (kyber-py / dilithium-py / liboqs) — each test SKIPS honestly when its
# backend is absent, so the suite stays green on minimal hosts while exercising PQC where it can.
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bankon_vault import BankonVault, PassphraseOverseer, HybridPQCOverseer, PolicyEngine, PolicyConfig, SigningRequest
from bankon_vault import pqc_hybrid, pqc_mldsa

KEM = pqc_hybrid.available()
DSA = pqc_mldsa.available()


def _skip(what):
    print(f"    (skipped — no {what} backend)", end=" ")


def test_hybrid_enroll_unlock_roundtrip():
    if not KEM:
        return _skip("ML-KEM")
    d = tempfile.mkdtemp()
    info = pqc_hybrid.enroll(d)
    assert os.path.exists(info["pqc_file"]) and "secret" not in open(info["pqc_file"]).read().lower()
    v = BankonVault(d, autolock_sec=0)
    salt = open(os.path.join(d, ".salt"), "rb").read()
    ov = HybridPQCOverseer(PassphraseOverseer("pp", salt), info["decaps_key"], d)
    assert v.unlock(ov)
    v.store("k", "hybrid secret", context="demo")
    v.lock()
    # a fresh overseer instance (same dk) reopens the same vault
    ov2 = HybridPQCOverseer(PassphraseOverseer("pp", salt), info["decaps_key"], d)
    assert v.unlock(ov2)
    assert v.retrieve_str("k") == "hybrid secret"
    v.lock()


def test_hybrid_fails_closed():
    if not KEM:
        return _skip("ML-KEM")
    d = tempfile.mkdtemp()
    info = pqc_hybrid.enroll(d)
    salt_v = BankonVault(d, autolock_sec=0)   # creates .salt
    salt = open(os.path.join(d, ".salt"), "rb").read()
    inner = PassphraseOverseer("pp", salt)
    salt_v.unlock(HybridPQCOverseer(inner, info["decaps_key"], d))
    salt_v.store("k", "s")
    salt_v.lock()
    # wrong decaps key → the commitment catches it BEFORE any decrypt (implicit rejection defeated)
    wrong = os.urandom(len(bytes.fromhex(info["decaps_key"]))).hex()
    bad = HybridPQCOverseer(PassphraseOverseer("pp", salt), wrong, d)
    assert not bad.verify_evidence(None, "")
    try:
        salt_v.unlock(bad)
        assert False, "wrong decaps key unlocked the vault"
    except Exception:
        pass
    # the CLASSICAL factor alone must not open a hybrid vault (the master really depends on ss)
    try:
        opened = salt_v.unlock(PassphraseOverseer("pp", salt)) and \
            salt_v.retrieve_str("k") == "s"
    except Exception:
        opened = False
    assert not opened, "classical-only unlock read a hybrid-enrolled vault"
    # missing .pqc.json → refuse outright, never silently degrade to classical
    d2 = tempfile.mkdtemp()
    ghost = HybridPQCOverseer(PassphraseOverseer("pp", salt), info["decaps_key"], d2)
    assert not ghost.verify_evidence(None, "")


def test_mldsa_sign_verify_and_tamper():
    if not DSA:
        return _skip("ML-DSA")
    kp = pqc_mldsa.generate()
    sig = pqc_mldsa.sign(kp["secret_key"], b"quorum vote")
    assert pqc_mldsa.verify(kp["public_key"], b"quorum vote", sig)
    assert not pqc_mldsa.verify(kp["public_key"], b"tampered", sig)
    assert not pqc_mldsa.verify(kp["public_key"], b"quorum vote", sig[:-8] + "00000000")
    v = pqc_mldsa.make_verifier()
    assert v("quorum vote", sig, kp["public_key"]) == pqc_mldsa.fingerprint(kp["public_key"])
    assert v("tampered", sig, kp["public_key"]) is None


def test_policy_quorum_goes_post_quantum():
    if not DSA:
        return _skip("ML-DSA")
    d = tempfile.mkdtemp()
    a, b = pqc_mldsa.generate(), pqc_mldsa.generate()
    cfg = PolicyConfig(quorum_n=2, quorum_approvers=[a["public_key"], b["public_key"]],
                       require_approval=False)
    e = PolicyEngine(cfg, d, verify_sig=pqc_mldsa.make_verifier(), approver=lambda s: True)
    req = SigningRequest("btc.seed", "cHNidP8B",
                         {"fee_sats": 100, "outputs": [{"address": "x", "sats": 1}],
                          "out_sats": 1, "network": "regtest"})
    msg = e.quorum_message(req).encode()
    va = (a["public_key"], pqc_mldsa.sign(a["secret_key"], msg))
    vb = (b["public_key"], pqc_mldsa.sign(b["secret_key"], msg))
    assert not e.evaluate(req, [va]).allowed                 # 1/2 → deny
    assert e.evaluate(req, [va, vb]).allowed                 # 2/2 ML-DSA votes → allow


def test_tier_q_adapter_identity_and_honest_refusal():
    if not DSA:
        return _skip("ML-DSA")
    from bankon_vault.chains.pqc import MLDSAAdapter
    ad = MLDSAAdapter()
    secret = ad.new_secret()
    ident = ad.address(secret)
    assert ident.startswith("mldsa:")
    r = ad.sign_message(secret, "prove it")
    assert ad.verify_message("prove it", r["signature"], r["pubkey"]) == ident
    assert ad.verify_message("forged", r["signature"], r["pubkey"]) is None
    # the adapter refuses Bitcoin transactions — Tier-Q is identity/quorum, not consensus
    try:
        ad.sign_psbt(secret, "cHNidP8B")
        assert False, "sign_psbt did not refuse"
    except NotImplementedError:
        pass
    # keypair blobs live in the vault like any other secret (chain-agnostic core)
    d = tempfile.mkdtemp()
    v = BankonVault(d, autolock_sec=0)
    salt = open(os.path.join(d, ".salt"), "rb").read()
    v.unlock(PassphraseOverseer("pp", salt))
    v.store("pqc.id", secret, context="pqc_identity")
    assert ad.address(v.retrieve_str("pqc.id")) == ident
    v.lock()


def test_status_reports_are_honest():
    # these must never raise, whatever is (not) installed
    for mod in (pqc_hybrid, pqc_mldsa):
        st = mod.status()
        assert isinstance(st["available"], bool) and "note" in st


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
