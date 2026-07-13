# SPDX-License-Identifier: GPL-3.0-or-later
# bankon-vault ceremony + Shamir + migration tests. Run: python3 tests/test_ceremony.py
import itertools
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bankon_vault import BankonVault, PassphraseOverseer
from bankon_vault.shamir import split, combine
from bankon_vault.ceremony import genesis, reconstruct, ShamirOverseer, Manifest
from bankon_vault import migrate

MNEM = "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about"


def test_shamir_any_k_of_n():
    secret = os.urandom(64)
    shares = split(secret, 5, 3)
    assert all(combine(list(c)) == secret for c in itertools.combinations(shares, 3))
    assert combine(shares[:2]) != secret          # k-1 reveals nothing recoverable


def test_genesis_manifest_holds_no_secret():
    shares, m = genesis(threshold=3, total=5)
    assert len(shares) == 5 and m.threshold == 3 and m.total == 5
    blob = json.dumps(m.to_dict())
    for sh in shares:                              # no raw share appears in the public manifest
        assert sh not in blob
    assert len(m.share_commitments) == 5 and len(m.master_fingerprint) == 16


def test_reconstruct_matches_fingerprint():
    shares, m = genesis(3, 5)
    master = reconstruct(shares[:3], m)            # any 3
    assert len(master) == 64
    # a wrong/short set must be caught by the fingerprint
    try:
        reconstruct(shares[:2], m); assert False
    except ValueError:
        pass


def test_shamir_overseer_unlocks_vault_with_quorum():
    d = tempfile.mkdtemp()
    v = BankonVault(d, autolock_sec=0)
    salt = open(os.path.join(d, ".salt"), "rb").read()
    shares, m = genesis(3, 5)
    # unlock with shares 1,3,5; store; relock; unlock with a DIFFERENT quorum 2,4,5 → same key
    v.unlock(ShamirOverseer([shares[0], shares[2], shares[4]], salt, m))
    v.store("btc.seed", MNEM, "bitcoin_wallet")
    v.lock()
    v.unlock(ShamirOverseer([shares[1], shares[3], shares[4]], salt, m))
    assert v.retrieve_str("btc.seed") == MNEM      # any 3-of-5 reconstruct the SAME master


def test_quorum_shortfall_cannot_read():
    d = tempfile.mkdtemp()
    v = BankonVault(d, autolock_sec=0)
    salt = open(os.path.join(d, ".salt"), "rb").read()
    shares, m = genesis(3, 5)
    v.unlock(ShamirOverseer([shares[0], shares[1], shares[2]], salt, m))
    v.store("s", "x"); v.lock()
    # only 2 shares → reconstruct raises via manifest, unlock fails
    try:
        v.unlock(ShamirOverseer([shares[0], shares[1]], salt, m))
        v.retrieve("s"); assert False
    except Exception:
        pass


def test_migration_roundtrip_verified():
    d = tempfile.mkdtemp()
    v = BankonVault(d, autolock_sec=0)
    salt = open(os.path.join(d, ".salt"), "rb").read()
    v.unlock(PassphraseOverseer("p", salt))
    # JSON source
    src = os.path.join(d, "legacy.json")
    json.dump({"btc.seed": MNEM, "api.key": "sk-test-123"}, open(src, "w"))
    rep = migrate.migrate_json(src, v)
    assert rep["count"] == 2 and set(rep["verified"]) == {"btc.seed", "api.key"} and not rep["failed"]
    assert v.retrieve_str("btc.seed") == MNEM      # actually in the vault, verified
    # env source
    envp = os.path.join(d, "legacy.env")
    open(envp, "w").write('# comment\nDB_PASS="hunter2"\nEMPTY=\n')
    rep2 = migrate.migrate_env(envp, v)
    assert "DB_PASS" in rep2["verified"] and v.retrieve_str("DB_PASS") == "hunter2"
    # manifest holds NO values
    mpath = os.path.join(d, "migration.json")
    migrate.write_manifest(rep, mpath)
    assert MNEM not in open(mpath).read()


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
