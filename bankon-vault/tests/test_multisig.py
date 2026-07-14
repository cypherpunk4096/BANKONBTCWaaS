# SPDX-License-Identifier: GPL-3.0-or-later
# bankon-vault multisig round tests (sign → combine → threshold → finalize). Run: python3 tests/test_multisig.py
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bankon_vault.chains.btc import BitcoinAdapter

A = "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about"
B = "legal winner thank year wave sausage worth useful legal winner thank yellow"
C = "letter advice cage absurd amount doctor acoustic avoid letter advice cage above"


def _ms_psbt(mnemonics, m):
    from embit import bip32, bip39, script
    from embit.psbt import PSBT, DerivationPath
    from embit.transaction import Transaction, TransactionInput, TransactionOutput
    roots = [bip32.HDKey.from_seed(bip39.mnemonic_to_seed(x)) for x in mnemonics]
    path = [84 + 0x80000000, 1 + 0x80000000, 0x80000000, 0, 0]
    leaves = [r.derive(path) for r in roots]
    ms = script.multisig(m, [l.key.get_public_key() for l in leaves])
    wsh = script.p2wsh(ms)
    prev = Transaction(vin=[], vout=[TransactionOutput(100000, wsh)])
    tx = Transaction(vin=[TransactionInput(bytes.fromhex(prev.txid().hex()), 0)],
                     vout=[TransactionOutput(90000, script.p2wpkh(roots[0].derive("m/84h/1h/0h/0/1")))])
    p = PSBT(tx)
    p.inputs[0].witness_utxo = prev.vout[0]
    p.inputs[0].witness_script = ms
    for r, l in zip(roots, leaves):
        p.inputs[0].bip32_derivations[l.key.get_public_key()] = DerivationPath(r.my_fingerprint, path)
    return p.to_base64()


def test_2of3_threshold_and_finalize():
    btc = BitcoinAdapter("regtest")
    psbt = _ms_psbt([A, B, C], m=2)
    assert btc.threshold(psbt) == [(2, 3)]
    sa = btc.sign_psbt(A, psbt)
    assert not btc.is_complete(sa)                    # 1 of 2 → NOT complete
    sb = btc.sign_psbt(B, psbt)
    combined = btc.combine_psbts([sa, sb])
    assert btc.signatures_present(combined) == [2]
    assert btc.is_complete(combined)                  # 2 of 2 threshold met
    final = btc.finalize(combined)
    assert len(final) > 40                             # a real raw tx hex


def test_single_sig_threshold_is_1of1():
    from embit import bip32, bip39, script
    from embit.psbt import PSBT, DerivationPath
    from embit.transaction import Transaction, TransactionInput, TransactionOutput
    btc = BitcoinAdapter("regtest")
    r = bip32.HDKey.from_seed(bip39.mnemonic_to_seed(A))
    path = [84 + 0x80000000, 1 + 0x80000000, 0x80000000, 0, 0]
    leaf = r.derive(path)
    prev = Transaction(vin=[], vout=[TransactionOutput(100000, script.p2wpkh(leaf))])
    tx = Transaction(vin=[TransactionInput(bytes.fromhex(prev.txid().hex()), 0)],
                     vout=[TransactionOutput(90000, script.p2wpkh(r.derive("m/84h/1h/0h/0/1")))])
    p = PSBT(tx); p.inputs[0].witness_utxo = prev.vout[0]
    p.inputs[0].bip32_derivations[leaf.key.get_public_key()] = DerivationPath(r.my_fingerprint, path)
    signed = btc.sign_psbt(A, p.to_base64())
    assert btc.threshold(signed) == [(1, 1)] and btc.is_complete(signed)


def test_decode_shows_all_outputs():
    # regression: decode_psbt must not collapse every output onto vout[0]
    from embit import bip32, bip39, script
    from embit.psbt import PSBT, DerivationPath
    from embit.transaction import Transaction, TransactionInput, TransactionOutput
    btc = BitcoinAdapter("regtest")
    r = bip32.HDKey.from_seed(bip39.mnemonic_to_seed(A))
    path = [84 + 0x80000000, 1 + 0x80000000, 0x80000000, 0, 0]
    leaf = r.derive(path)
    prev = Transaction(vin=[], vout=[TransactionOutput(20000000, script.p2wpkh(leaf))])
    a1 = script.p2wpkh(r.derive("m/84h/1h/0h/0/1"))
    a2 = script.p2wpkh(r.derive("m/84h/1h/0h/0/2"))
    tx = Transaction(vin=[TransactionInput(bytes.fromhex(prev.txid().hex()), 0)],
                     vout=[TransactionOutput(1000, a1), TransactionOutput(10000000, a2)])
    p = PSBT(tx); p.inputs[0].witness_utxo = prev.vout[0]
    p.inputs[0].bip32_derivations[leaf.key.get_public_key()] = DerivationPath(r.my_fingerprint, path)
    s = btc.decode_psbt(p.to_base64())
    assert len(s["outputs"]) == 2, "both outputs must be visible to the gate"
    assert {o["sats"] for o in s["outputs"]} == {1000, 10000000}
    assert len({o["address"] for o in s["outputs"]}) == 2   # two DISTINCT addresses
    assert s["out_sats"] == 10001000


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