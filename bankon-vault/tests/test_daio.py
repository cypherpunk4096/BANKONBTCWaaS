# SPDX-License-Identifier: GPL-3.0-or-later
# bankon-vault DAIO ceremony tests. Run: python3 tests/test_daio.py
# The seats sign with deterministic test keys (a small RFC 6979-style signer below, test-only); the
# module under test only ever verifies.
import hashlib
import hmac
import itertools
import json
import os
import sys
import tempfile
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bankon_vault import BankonVault, PassphraseOverseer
from bankon_vault import daio, evm
from bankon_vault.daio import Council, Seat, DaioError, DaioOverseer, Session, genesis, rotate
from bankon_vault.daio_service import DaioService, make_handler


# ── test signer: EIP-191 personal_sign with a deterministic nonce ──────────────────────────────────
def _pub_addr(d: int) -> str:
    x, y = evm._mul(d, evm._G)
    return evm.checksum_address(evm.keccak256(x.to_bytes(32, "big") + y.to_bytes(32, "big"))[12:])


def sign(d: int, message: str) -> str:
    z = evm.personal_digest(message)
    k = int.from_bytes(hmac.new(d.to_bytes(32, "big"), z, hashlib.sha256).digest(), "big") % evm._N or 1
    R = evm._mul(k, evm._G)
    r = R[0] % evm._N
    s = pow(k, -1, evm._N) * (int.from_bytes(z, "big") + r * d) % evm._N
    v = R[1] & 1
    if s > evm._N // 2:
        s, v = evm._N - s, v ^ 1
    return "0x" + (r.to_bytes(32, "big") + s.to_bytes(32, "big") + bytes([27 + v])).hex()


KEYS = {role: 1000 + i for i, role in enumerate(daio.SEATS)}        # CEO … CLO
ADDR = {role: _pub_addr(d) for role, d in KEYS.items()}


# each seat's ML-KEM seed, derived from its test key so a replaced seat gets its own
def pqseed(d: int) -> bytes:
    return hashlib.sha256(b"test-daio-pq-seed" + d.to_bytes(32, "big")).digest()


def pq_ek(d: int) -> bytes:
    return daio.pq_public_key(pqseed(d))


def emsg(cid: str, role: str, keys=KEYS, address=None) -> str:
    """The v2 enrolment message: it names the seat's ML-KEM key."""
    return daio.enrolment_message(cid, role, address or _pub_addr(keys[role]), daio.pq_fingerprint(pq_ek(keys[role])))


def council(keys=KEYS):
    return Council([Seat(r, _pub_addr(d), daio.DEFAULT_WEIGHTS[r], r in daio.DEFAULT_VETO) for r, d in keys.items()],
                   {"chainId": 31337, "contract": "0x" + "11" * 20, "block": 1})


def enrol(c: Council, cid: str, keys=KEYS):
    return {s.role: {"signature": sign(keys[s.role], emsg(cid, s.role, keys, s.address)), "mlkem_ek": pq_ek(keys[s.role]).hex()}
            for s in c.holders}


def enrol_classical(c: Council, cid: str, keys=KEYS):
    return {s.role: sign(keys[s.role], daio.enrolment_message(cid, s.role, s.address)) for s in c.holders}


def present_all(session, envelopes, roles, keys=KEYS):
    m = session.manifest
    for r in roles:
        session.present(r, envelopes[r], sign(keys[r], emsg(m.ceremony, r, keys, session.council.seat(r).address)),
                        sign(keys[r], session.message()), pqseed(keys[r]))


# ── tests ──────────────────────────────────────────────────────────────────────────────────────────
def test_evm_recovers_the_test_keys():
    msg = "hello DAIO"
    assert evm.recover_personal_sign(msg, sign(KEYS["CISO"], msg)) == ADDR["CISO"]
    assert evm.keccak256(b"").hex() == "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470"


def test_genesis_follows_the_daio_rules():
    c = council(); cid = daio.new_ceremony()
    env, m = genesis(c, enrol(c, cid), cid)
    assert m.total == 7 and m.threshold == 5                       # 2/3 of the seven soldiers
    assert m.weight_total == 7200 and m.weight_required == 4800    # 2/3 of the council's weight
    assert sorted(m.veto) == ["CISO", "CRO"] and "CEO" not in env  # the CEO holds no share
    blob = json.dumps(m.to_dict())
    for e in env.values():                                         # nothing secret in the manifest
        assert e["ct"] not in blob


def test_genesis_refuses_a_wrong_enrolment():
    c = council(); cid = daio.new_ceremony(); e = enrol(c, cid)
    e["CFO"] = dict(e["CFO"], signature=sign(KEYS["CTO"], emsg(cid, "CFO")))   # signed by the wrong seat
    try:
        genesis(c, e, cid); assert False
    except DaioError as x:
        assert "CFO" in str(x)


def test_a_quorum_with_the_vetoes_reconstitutes_and_unlocks_a_vault():
    c = council(); cid = daio.new_ceremony()
    with tempfile.TemporaryDirectory() as d:
        v = BankonVault(d)
        v.unlock(PassphraseOverseer("pp", v._salt)); v.store("btc.seed", b"seed words");
        master = bytearray(os.urandom(64))
        env, m = genesis(c, enrol(c, cid), cid, master=bytes(master))
        v.rekey(daio._MasterOverseer(master, v._salt)); v.lock()
        s = Session(m, proposal=42, action="unlock")
        present_all(s, env, ["CISO", "CRO", "CFO", "CPO", "COO"])  # 5 seats, 5400 ≥ 4800, both vetoes
        assert s.ready()
        assert v.unlock(DaioOverseer(s, v._salt)) and bytes(v.retrieve("btc.seed")) == b"seed words"


def test_every_quorum_of_five_with_both_vetoes_works_and_none_without():
    c = council(); cid = daio.new_ceremony()
    env, m = genesis(c, enrol(c, cid), cid)
    for combo in itertools.combinations([s.role for s in c.holders], 5):
        s = Session(m, 7, "unlock"); present_all(s, env, combo)
        has_veto = {"CISO", "CRO"} <= set(combo)
        assert s.ready() == has_veto, combo
        if has_veto:
            mm = s.master(); assert len(mm) == 64


def test_refusals():
    c = council(); cid = daio.new_ceremony()
    env, m = genesis(c, enrol(c, cid), cid)
    s = Session(m, 9, "unlock")
    # approval of another proposal
    try:
        s.present("CFO", env["CFO"], sign(KEYS["CFO"], emsg(cid, "CFO")),
                  sign(KEYS["CFO"], daio.approval_message(cid, 10, "unlock")), pqseed(KEYS["CFO"])); assert False
    except DaioError as x:
        assert "approval" in str(x)
    # one seat's envelope presented as another's
    try:
        s.present("CTO", env["CFO"], sign(KEYS["CTO"], emsg(cid, "CTO")),
                  sign(KEYS["CTO"], s.message()), pqseed(KEYS["CTO"])); assert False
    except DaioError as x:
        assert "not this seat" in str(x)
    # a tampered envelope does not open
    bad = dict(env["COO"]); ct = bytearray(bytes.fromhex(bad["ct"])); ct[3] ^= 1; bad["ct"] = ct.hex()
    try:
        s.present("COO", bad, sign(KEYS["COO"], emsg(cid, "COO")), sign(KEYS["COO"], s.message()), pqseed(KEYS["COO"])); assert False
    except DaioError as x:
        assert "does not open" in str(x)
    # the CEO holds no share
    try:
        s.present("CEO", env["CFO"], "0x00", "0x00"); assert False
    except DaioError as x:
        assert "holds no share" in str(x)
    assert not s.ready()
    try:
        s.master(); assert False
    except DaioError:
        pass


def test_rotation_retires_a_removed_executive():
    c = council(); cid = daio.new_ceremony()
    with tempfile.TemporaryDirectory() as d:
        v = BankonVault(d); v.unlock(PassphraseOverseer("pp", v._salt)); v.store("x", b"secret")
        master = bytearray(os.urandom(64))
        env, m = genesis(c, enrol(c, cid), cid, master=bytes(master))
        v.rekey(daio._MasterOverseer(master, v._salt)); v.lock()
        # the DAIO replaces its CTO; the current quorum approves the rotation
        keys2 = dict(KEYS); keys2["CTO"] = 4242
        c2 = council(keys2); cid2 = daio.new_ceremony()
        s = Session(m, 77, "rotate"); present_all(s, env, ["CISO", "CRO", "CFO", "CPO", "COO", "CTO"])
        env2, m2 = rotate(v, s, c2, enrol(c2, cid2, keys2), cid2); v.lock()
        # the old shares, the removed CTO's among them, no longer open the vault
        old = Session(m, 78, "unlock"); present_all(old, env, ["CISO", "CRO", "CFO", "CPO", "CTO"])
        try:
            v.unlock(DaioOverseer(old, v._salt)); assert False, "old shares must not open the rotated vault"
        except Exception:
            pass
        new = Session(m2, 79, "unlock"); present_all(new, env2, ["CISO", "CRO", "CFO", "CPO", "CTO"], keys2)
        assert v.unlock(DaioOverseer(new, v._salt)) and bytes(v.retrieve("x")) == b"secret"


def test_council_from_chain_reads_executive_roles():
    sel_holder = evm.keccak256(b"getRoleHolder(uint8)")[:4].hex()
    sel_weight = evm.keccak256(b"roleWeights(uint8)")[:4].hex()

    class RPC(BaseHTTPRequestHandler):
        def log_message(self, *a): pass

        def do_POST(self):
            req = json.loads(self.rfile.read(int(self.headers["content-length"])))
            m = req["method"]
            if m == "eth_blockNumber": res = hex(123)
            elif m == "eth_chainId": res = hex(31337)
            else:
                data = req["params"][0]["data"][2:]
                role = daio.SEATS[int(data[8:], 16) - 1]
                if data.startswith(sel_holder):
                    res = "0x" + ("0" * 64 if role == "CLO" else ADDR[role][2:].lower().rjust(64, "0"))
                elif data.startswith(sel_weight):
                    res = "0x" + format(daio.DEFAULT_WEIGHTS[role], "064x")
                else:
                    res = "0x"
            out = json.dumps({"jsonrpc": "2.0", "id": 1, "result": res}).encode()
            self.send_response(200); self.send_header("content-length", str(len(out))); self.end_headers(); self.wfile.write(out)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), RPC)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        c = Council.from_chain(f"http://127.0.0.1:{srv.server_port}", "0x" + "22" * 20)
        assert [s.role for s in c.seats] == daio.SEATS[:-1]          # the empty CLO seat is left out
        assert c.seat("CISO").address.lower() == ADDR["CISO"].lower() and c.seat("CISO").veto
        assert c.source == {"chainId": 31337, "contract": "0x" + "22" * 20, "block": 123}
    finally:
        srv.shutdown()


def test_the_service_runs_a_session_end_to_end():
    c = council(); cid = daio.new_ceremony()
    with tempfile.TemporaryDirectory() as d:
        v = BankonVault(d); v.unlock(PassphraseOverseer("pp", v._salt)); v.store("btc.seed", b"s")
        master = bytearray(os.urandom(64))
        env, m = genesis(c, enrol(c, cid), cid, master=bytes(master))
        v.rekey(daio._MasterOverseer(master, v._salt)); v.lock()
        svc = DaioService(BankonVault(d))
        srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(svc))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{srv.server_port}/daio/session"

        def post(url, body=None):
            req = urllib.request.Request(url, json.dumps(body or {}).encode(), {"content-type": "application/json"})
            try:
                with urllib.request.urlopen(req) as r:
                    return r.status, json.loads(r.read())
            except urllib.error.HTTPError as e:
                return e.code, json.loads(e.read())
        try:
            code, o = post(base, {"manifest": m.to_dict(), "proposal": 5, "action": "unlock"})
            assert code == 200
            sid, msg = o["session"], o["message"]
            assert post(f"{base}/{sid}/unlock")[0] == 400                 # not yet a quorum
            for r in ["CISO", "CRO", "CFO", "CPO", "COO"]:
                code, st = post(f"{base}/{sid}/present", {"role": r, "envelope": env[r],
                    "enrol_signature": sign(KEYS[r], emsg(cid, r)), "approval_signature": sign(KEYS[r], msg), "pq_seed": pqseed(KEYS[r]).hex()})
                assert code == 200, st
            assert st["ready"] and st["weight"] == 5400
            code, u = post(f"{base}/{sid}/unlock")
            assert code == 200 and u["unlocked"] and "key" not in json.dumps(u).lower()
            assert bytes(svc.vault.retrieve("btc.seed")) == b"s"
            assert post(f"{base}/{sid}/close")[1]["closed"]
        finally:
            srv.shutdown()


MNEM = "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about"


def _psbt(out_sats):
    from embit import bip32, bip39, script
    from embit.psbt import PSBT, DerivationPath
    from embit.transaction import Transaction, TransactionInput, TransactionOutput
    root = bip32.HDKey.from_seed(bip39.mnemonic_to_seed(MNEM))
    path = [84 + 0x80000000, 1 + 0x80000000, 0x80000000, 0, 0]
    leaf = root.derive(path)
    prev = Transaction(vin=[], vout=[TransactionOutput(100000, script.p2wpkh(leaf))])
    tx = Transaction(vin=[TransactionInput(bytes.fromhex(prev.txid().hex()), 0)],
                     vout=[TransactionOutput(out_sats, script.p2wpkh(root.derive("m/84h/1h/0h/0/1")))])
    p = PSBT(tx)
    p.inputs[0].witness_utxo = prev.vout[0]
    p.inputs[0].bip32_derivations[leaf.key.get_public_key()] = DerivationPath(root.my_fingerprint, path)
    return p.to_base64()


def test_a_sign_session_signs_only_the_approved_transaction_then_locks():
    from bankon_vault.chains.btc import BitcoinAdapter
    c = council(); cid = daio.new_ceremony()
    approved, other = _psbt(90000), _psbt(50000)
    want = hashlib.sha256(approved.encode()).hexdigest()
    with tempfile.TemporaryDirectory() as d:
        v = BankonVault(d); v.unlock(PassphraseOverseer("pp", v._salt)); v.store("btc.seed", MNEM.encode())
        master = bytearray(os.urandom(64))
        env, m = genesis(c, enrol(c, cid), cid, master=bytes(master))
        v.rekey(daio._MasterOverseer(master, v._salt)); v.lock()
        svc = DaioService(BankonVault(d), BitcoinAdapter("regtest"))
        quorum = ["CISO", "CRO", "CFO", "CPO", "COO"]

        def run(action, payload, psbt):
            o = svc.open(m.to_dict(), 11, action, payload)
            sid, msg = o["session"], o["message"]
            if payload:
                assert f"payload: {payload}" in msg
            for r in quorum:
                svc.present(sid, {"role": r, "envelope": env[r], "enrol_signature": sign(KEYS[r], emsg(cid, r)),
                                  "approval_signature": sign(KEYS[r], msg), "pq_seed": pqseed(KEYS[r]).hex()})
            svc.unlock(sid)
            oracle = svc.oracles[sid]
            ch = oracle.challenge("btc.seed", psbt)
            try:
                return sid, oracle.sign(ch["nonce"], "btc.seed", psbt)
            except PermissionError as e:
                return sid, e

        sid, out = run("sign", want, other)                  # the quorum approved another transaction
        assert isinstance(out, PermissionError), out
        svc.close(sid)
        sid, out = run("sign", want, approved)
        assert isinstance(out, dict) and out["signed_psbt"] != approved
        svc.close(sid); assert svc.vault._vault_key is None   # locked again
        sid, out = run("unlock", None, approved)              # unlock alone approves no transaction
        assert isinstance(out, PermissionError)
        svc.close(sid)
    try:
        Session(m, 1, "sign"); assert False                   # a sign session must name its PSBT
    except DaioError:
        pass


# ── the post-quantum layer: ML-KEM-768 (FIPS 203) beside the wallet ──────────────────────────────────
# One NIST ACVP key-generation vector (ML-KEM-768, internalProjection, usnistgov/ACVP-Server): the
# backend must derive exactly this public key from d || z.


def _acvp_vector():
    path = os.environ.get("ACVP_MLKEM_KEYGEN")          # the JSON file, when the test host has it
    if not path or not os.path.exists(path):
        return None
    for g in json.load(open(path))["testGroups"]:
        if g["parameterSet"] == "ML-KEM-768":
            return g["tests"][0]
    return None


def test_mlkem_keygen_matches_the_fips203_vectors_when_given():
    t = _acvp_vector()
    if t is None:
        return                                           # set ACVP_MLKEM_KEYGEN to run it
    from cryptography.hazmat.primitives.asymmetric import mlkem
    k = mlkem.MLKEM768PrivateKey.from_seed_bytes(bytes.fromhex(t["d"] + t["z"]))
    assert k.public_key().public_bytes_raw().hex().upper() == t["ek"].upper()


def test_a_seat_key_is_reproducible_from_its_seed_and_words():
    k = daio.pq_keygen()
    assert len(bytes.fromhex(k["mlkem_ek"])) == daio.KEM_EK_BYTES
    assert daio.pq_public_key(k["seed"]).hex() == k["mlkem_ek"]
    if k["words"]:
        assert len(k["words"].split()) == 24 and daio.pq_public_key(k["words"]).hex() == k["mlkem_ek"]
    assert daio.pq_public_key(daio.pq_keygen()["seed"]).hex() != k["mlkem_ek"]


def test_genesis_seals_hybrid_envelopes_and_records_the_keys():
    c = council(); cid = daio.new_ceremony()
    env, m = genesis(c, enrol(c, cid), cid)
    assert m.kem == "ML-KEM-768" and set(m.kem_fingerprints) == {s.role for s in c.holders}
    for r, e in env.items():
        assert e["version"] == daio.ENVELOPE_V2 and e["mlkem_ek_fp"] == m.kem_fingerprints[r] and len(bytes.fromhex(e["kem_ct"])) == 1088


def test_genesis_refuses_a_classical_seat_unless_asked():
    c = council(); cid = daio.new_ceremony(); e = enrol(c, cid)
    e["CFO"] = sign(KEYS["CFO"], daio.enrolment_message(cid, "CFO", ADDR["CFO"]))    # no ML-KEM key
    try:
        genesis(c, e, cid); assert False
    except DaioError as x:
        assert "CFO" in str(x) and "ML-KEM" in str(x)
    env, m = genesis(c, e, cid, allow_classical=True)                               # only when asked
    assert env["CFO"]["version"] == daio.ENVELOPE_V1 and "CFO" not in m.kem_fingerprints


def test_an_attacker_with_the_wallet_key_but_not_the_seed_cannot_open_a_hybrid_envelope():
    # the quantum adversary of a v1 envelope: it has recovered the seat's secp256k1 key from its public
    # key, so it can reproduce the deterministic enrolment signature
    c = council(); cid = daio.new_ceremony()
    env, _ = genesis(c, enrol(c, cid), cid)
    forged = sign(KEYS["CFO"], emsg(cid, "CFO"))
    for seed in (None, os.urandom(32)):
        try:
            daio.unseal(env["CFO"], forged, seed); assert False
        except DaioError as x:
            assert "ML-KEM" in str(x)
    assert "share" in daio.unseal(env["CFO"], forged, pqseed(KEYS["CFO"]))          # the holder, with both
    # the same adversary DOES open a classical (v1) envelope: the weakness this layer removes
    envc, _ = genesis(c, enrol_classical(c, cid), cid, allow_classical=True)
    assert "share" in daio.unseal(envc["CFO"], sign(KEYS["CFO"], daio.enrolment_message(cid, "CFO", ADDR["CFO"])))


def test_genesis_refuses_a_substituted_mlkem_key():
    # whoever runs genesis swaps a seat's ML-KEM key for its own: the seat signed the fingerprint of the
    # real key, so the enrolment no longer checks
    c = council(); cid = daio.new_ceremony(); e = enrol(c, cid)
    e["CRO"] = dict(e["CRO"], mlkem_ek=daio.pq_keygen()["mlkem_ek"])
    try:
        genesis(c, e, cid); assert False
    except DaioError as x:
        assert "CRO" in str(x)


def test_a_hybrid_seat_cannot_be_downgraded_to_a_classical_envelope():
    c = council(); cid = daio.new_ceremony()
    env, m = genesis(c, enrol(c, cid), cid)
    # an attacker substitutes a v1 envelope for the CTO, sealed under the reproducible signature alone
    v1 = daio.seal(cid, c.seat("CTO"), sign(KEYS["CTO"], daio.enrolment_message(cid, "CTO", ADDR["CTO"])), {"share": "x"})
    s = Session(m, 5, "unlock")
    try:
        s.present("CTO", v1, sign(KEYS["CTO"], daio.enrolment_message(cid, "CTO", ADDR["CTO"])), sign(KEYS["CTO"], s.message())); assert False
    except DaioError as x:
        assert "ML-KEM" in str(x)


def test_classical_ceremonies_still_reconstitute():
    c = council(); cid = daio.new_ceremony()
    env, m = genesis(c, enrol_classical(c, cid), cid, allow_classical=True)
    assert m.kem == "" and m.kem_fingerprints == {}
    s = Session(m, 3, "unlock")
    for r in ["CISO", "CRO", "CFO", "CPO", "COO"]:
        s.present(r, env[r], sign(KEYS[r], daio.enrolment_message(cid, r, ADDR[r])), sign(KEYS[r], s.message()))
    assert s.ready() and len(s.master()) == 64 and "classical" in s.status()["kem"]


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    ok = 0
    for t in tests:
        try:
            t(); ok += 1; print(f"  ✓ {t.__name__}")
        except Exception as e:
            print(f"  ✗ {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{ok}/{len(tests)} passed")
    sys.exit(0 if ok == len(tests) else 1)
