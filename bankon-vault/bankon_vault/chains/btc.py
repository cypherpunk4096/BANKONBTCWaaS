# SPDX-License-Identifier: GPL-3.0-or-later
# bankon-vault — the Bitcoin adapter. The definitive BTC signature-access + gating layer, fulfilling
# the decades-old github.com/bankonme plan (be your own bank, keys offline). Pure-Python via `embit`
# (BIP32/39/44/84/86, bech32 P2WPKH + P2TR taproot, PSBT) — offline/air-gap friendly, no daemon.
#
# The private key NEVER leaves this module: sign_psbt reconstructs the key from stored material,
# signs, and drops it. Gating uses a Bitcoin-Signed-Message ECDSA signature verified against the
# signer's pinned pubkey OR — via BIP-137 recoverable signatures — against an ADDRESS alone
# (the pubkey is recovered from the 65-byte compact signature). Full BIP-322 remains roadmap.
from __future__ import annotations

import base64
import hashlib
from typing import Optional

from embit import bip32, bip39, ec, hashes, script, compact
from embit.networks import NETWORKS
from embit.psbt import PSBT
from embit.script import Script, Witness
from embit.transaction import Transaction, TransactionInput, TransactionOutput, SIGHASH
from embit.util import secp256k1 as _secp

from .base import ChainAdapter

# default derivation accounts (BIP-84 native segwit / BIP-86 taproot), receive branch, index 0
PATHS = {"wpkh": "m/84h/{coin}h/0h/0/{i}", "tr": "m/86h/{coin}h/0h/0/{i}"}
BSM_MAGIC = b"Bitcoin Signed Message:\n"

# BIP-137 header-byte bases: header = base + recid (0..3). The base declares the address type the
# signer commits to, so verification derives THAT type from the recovered pubkey and compares.
BIP137_BASES = {27: ("pkh", False), 31: ("pkh", True), 35: ("sh-wpkh", True), 39: ("wpkh", True)}
_KIND_TO_BASE = {"pkh": 31, "sh-wpkh": 35, "wpkh": 39}   # we always sign with compressed keys


def _bsm_hash(message: str) -> bytes:
    """The standard Bitcoin Signed Message digest: dSHA256(varstr(magic) || varstr(message))."""
    msg = message.encode()
    data = compact.to_bytes(len(BSM_MAGIC)) + BSM_MAGIC + compact.to_bytes(len(msg)) + msg
    return hashlib.sha256(hashlib.sha256(data).digest()).digest()


# ---- BIP-322 generic signed messages (the "simple" variant) ----
# The message is committed to a pair of virtual transactions: `to_spend` locks a zero-value output
# to the signer's scriptPubKey with the tagged message hash in its scriptSig; `to_sign` spends it.
# The signature IS the serialized witness stack of that spend — so any address type Bitcoin can
# spend can sign a message, including taproot (which BIP-137 cannot cover). Construction verified
# byte-exact against the BIP-322 spec's basic-test-vectors.json (tx hashes + all simple/error cases).

def _bip322_to_spend(spk: Script, message: str) -> Transaction:
    mh = hashes.tagged_hash("BIP0322-signed-message", message.encode())
    return Transaction(version=0,
                       vin=[TransactionInput(b"\x00" * 32, 0xFFFFFFFF,
                                             script_sig=Script(b"\x00\x20" + mh), sequence=0)],
                       vout=[TransactionOutput(0, spk)], locktime=0)


def _bip322_to_sign(spend_txid: bytes) -> Transaction:
    return Transaction(version=0,
                       vin=[TransactionInput(spend_txid, 0, sequence=0)],
                       vout=[TransactionOutput(0, Script(b"\x6a"))], locktime=0)   # OP_RETURN


class BitcoinAdapter(ChainAdapter):
    name = "bitcoin"

    def __init__(self, network: str = "main"):
        if network not in NETWORKS:
            raise ValueError(f"unknown network {network!r} (use main/test/regtest/signet)")
        self.network = network
        self.net = NETWORKS[network]
        self.coin = 0 if network == "main" else 1

    # ---- key material ----
    def new_secret(self, strength: int = 128) -> str:
        """A fresh BIP-39 mnemonic (12 words @128 bits). STORE this string as the vault entry."""
        return bip39.mnemonic_from_bytes(_urandom(strength // 8))

    def _root(self, secret: str) -> bip32.HDKey:
        s = secret.strip()
        if s.startswith(("xprv", "tprv", "yprv", "zprv", "vprv", "uprv")):
            return bip32.HDKey.from_base58(s)
        if len(s.split()) in (12, 15, 18, 21, 24):              # a mnemonic
            return bip32.HDKey.from_seed(bip39.mnemonic_to_seed(s))
        raise ValueError("secret must be a BIP-39 mnemonic or an xprv")

    def _leaf(self, secret: str, path: Optional[str], kind: str) -> bip32.HDKey:
        p = path or PATHS.get(kind, PATHS["wpkh"]).format(coin=self.coin, i=0)
        return self._root(secret).derive(p)

    # ---- addresses ----
    def address(self, secret: str, path: Optional[str] = None, kind: str = "wpkh") -> str:
        leaf = self._leaf(secret, path, kind)
        scr = script.p2tr(leaf) if kind == "tr" else script.p2wpkh(leaf)
        return scr.address(self.net)

    def wif(self, secret: str, path: Optional[str] = None, kind: str = "wpkh") -> str:
        return self._leaf(secret, path, kind).key.wif(self.net)

    # ---- signature gating (sign-don't-export) ----
    def sign_message(self, secret: str, message: str, path: Optional[str] = None,
                     kind: str = "wpkh") -> dict:
        leaf = self._leaf(secret, path, kind)
        sig = leaf.key.sign(_bsm_hash(message))
        pub = leaf.key.get_public_key()
        return {"address": (script.p2tr(leaf) if kind == "tr" else script.p2wpkh(leaf)).address(self.net),
                "pubkey": pub.serialize().hex(), "signature": sig.serialize().hex(),
                "scheme": "bsm-ecdsa"}

    def sign_message_compact(self, secret: str, message: str, path: Optional[str] = None,
                             kind: str = "wpkh") -> dict:
        """BIP-137 recoverable signature — the 65-byte base64 format Bitcoin Core's `signmessage`
        emits. Verifiable against the ADDRESS alone (no pinned pubkey needed): the header byte
        carries the recovery id + address type, so any verifier can recover the pubkey."""
        if kind not in _KIND_TO_BASE:
            raise ValueError(f"BIP-137 covers pkh/sh-wpkh/wpkh, not {kind!r} (taproot needs BIP-322)")
        leaf = self._leaf(secret, path, kind)
        raw = _secp.ecdsa_sign_recoverable(_bsm_hash(message), leaf.key.secret)
        ser, recid = _secp.ecdsa_recoverable_signature_serialize_compact(raw)
        sig65 = bytes([_KIND_TO_BASE[kind] + recid]) + ser
        pub = leaf.key.get_public_key()
        return {"address": self._addr_for_kind(pub, kind), "pubkey": pub.serialize().hex(),
                "signature": base64.b64encode(sig65).decode(), "scheme": "bip137"}

    def recover_address(self, message: str, signature) -> Optional[str]:
        """Recover the signer's address from a BIP-137 compact signature (base64 or hex).
        Returns the address of the type the header byte declares, or None if malformed."""
        try:
            sig65 = _compact_sig_bytes(signature)
            if sig65 is None:
                return None
            base = next((b for b in BIP137_BASES if b <= sig65[0] <= b + 3), None)
            if base is None:
                return None
            kind, compressed = BIP137_BASES[base]
            rec = _secp.ecdsa_recoverable_signature_parse_compact(sig65[1:], sig65[0] - base)
            raw_pub = _secp.ecdsa_recover(rec, _bsm_hash(message))
            flag = _secp.EC_COMPRESSED if compressed else _secp.EC_UNCOMPRESSED
            pub = ec.PublicKey.parse(_secp.ec_pubkey_serialize(raw_pub, flag))
            return self._addr_for_kind(pub, kind)
        except Exception:
            return None

    def sign_message_bip322(self, secret: str, message: str, path: Optional[str] = None,
                            kind: str = "wpkh", variant: str = "simple") -> dict:
        """BIP-322 signature — covers TAPROOT (key-path) as well as native segwit.
        variant='simple' → the base64 witness stack; variant='full' → the base64 of the whole
        to_sign transaction (what btcd-style verifiers of the full variant expect)."""
        if variant not in ("simple", "full"):
            raise ValueError("variant must be 'simple' or 'full'")
        leaf = self._leaf(secret, path, kind)
        if kind == "tr":
            spk = script.p2tr(leaf)
            to_sign = _bip322_to_sign(_bip322_to_spend(spk, message).txid())
            h = to_sign.sighash_taproot(0, [spk], [0])          # SIGHASH_DEFAULT → 64-byte sig
            wit = Witness([leaf.key.taproot_tweak(b"").schnorr_sign(h).serialize()])
        elif kind == "wpkh":
            spk = script.p2wpkh(leaf)
            to_sign = _bip322_to_sign(_bip322_to_spend(spk, message).txid())
            h = to_sign.sighash_segwit(0, script.p2pkh_from_p2wpkh(spk), 0)
            wit = Witness([leaf.key.sign(h).serialize() + bytes([SIGHASH.ALL]),
                           leaf.key.get_public_key().sec()])
        else:
            raise ValueError(f"BIP-322 supports wpkh/tr here, not {kind!r}")
        if variant == "full":
            to_sign.vin[0].witness = wit
            payload = to_sign.serialize()
        else:
            payload = wit.serialize()
        return {"address": spk.address(self.net),
                "signature": base64.b64encode(payload).decode(), "scheme": f"bip322-{variant}"}

    def verify_message_bip322(self, message: str, signature, address: str) -> Optional[str]:
        """Verify a BIP-322 'simple' signature against an address (p2wpkh, p2tr key-path, or
        p2wsh K-of-N). Returns the address if valid, else None — malformed input fails closed."""
        try:
            raw = signature if isinstance(signature, (bytes, bytearray)) else \
                base64.b64decode(str(signature).strip(), validate=True)
            wit = Witness.parse(bytes(raw))
            spk = script.address_to_scriptpubkey(address.strip())
        except Exception:
            return None
        to_sign = _bip322_to_sign(_bip322_to_spend(spk, message).txid())
        return self._bip322_verify_input(to_sign, spk, None, wit.items, address.strip())

    def verify_message_bip322_full(self, message: str, signature, address: str) -> Optional[str]:
        """Verify a BIP-322 'FULL' signature: the payload is the complete to_sign TRANSACTION
        (any version/locktime/sequence — timelocked signatures are legal), whose single input
        must spend the virtual to_spend for (message, address). Adds legacy p2pkh and
        p2sh-p2wpkh to the covered types (they have no witness-only 'simple' form).
        Proof-of-funds payloads (extra inputs) need UTXO-set context → refused honestly."""
        try:
            raw = signature if isinstance(signature, (bytes, bytearray)) else \
                base64.b64decode(str(signature).strip(), validate=True)
            tx = Transaction.parse(bytes(raw))
            spk = script.address_to_scriptpubkey(address.strip())
        except Exception:
            return None
        try:
            if len(tx.vin) != 1:                                         # proof-of-funds → out of scope
                return None
            spend = _bip322_to_spend(spk, message)
            if tx.vin[0].txid != spend.txid() or tx.vin[0].vout != 0:    # must spend OUR to_spend
                return None
            if not tx.vout or tx.vout[0].value != 0 or tx.vout[0].script_pubkey.data[:1] != b"\x6a":
                return None                                              # canonical OP_RETURN output
            items = tx.vin[0].witness.items if tx.vin[0].witness else []
            return self._bip322_verify_input(tx, spk, tx.vin[0].script_sig, items, address.strip())
        except Exception:
            return None

    def _bip322_verify_input(self, to_sign, spk, script_sig, items, address) -> Optional[str]:
        """Validate input 0 of `to_sign` against the to_spend output script `spk`. Shared by the
        simple (canonical tx, witness only) and full (provided tx, may have scriptSig) variants."""
        data = spk.data
        try:
            if len(data) == 25 and data[:3] == b"\x76\xa9\x14":          # p2pkh (full variant only)
                pushes = _script_pushes(script_sig.data if script_sig else b"")
                if pushes is None or len(pushes) != 2 or items:
                    return None
                sig_all, pub = pushes
                if len(sig_all) < 9 or sig_all[-1] != SIGHASH.ALL:
                    return None
                if hashes.hash160(pub) != data[3:23]:
                    return None
                h = to_sign.sighash_legacy(0, spk)
                if ec.PublicKey.parse(pub).verify(ec.Signature.parse(sig_all[:-1]), h):
                    return address
                return None
            if len(data) == 23 and data[:2] == b"\xa9\x14":              # p2sh
                pushes = _script_pushes(script_sig.data if script_sig else b"")
                if not pushes:
                    return None
                redeem = pushes[-1]                                      # redeem is the LAST push
                if hashes.hash160(redeem) != data[2:22]:                 # redeem must own the address
                    return None
                if len(redeem) == 22 and redeem[:2] == b"\x00\x14":      # p2sh-p2wpkh → segwit rules
                    if len(pushes) != 1:
                        return None
                    return self._bip322_verify_input(to_sign, Script(redeem), None, items, address)
                if len(redeem) == 34 and redeem[:2] == b"\x00\x20":      # p2sh-p2wsh → p2wsh rules
                    if len(pushes) != 1:
                        return None
                    return self._bip322_verify_input(to_sign, Script(redeem), None, items, address)
                # legacy p2sh K-of-N multisig: scriptSig = OP_0 <sig..sig> <redeem>, no witness
                from embit import finalizer
                if items or len(pushes) < 3 or pushes[0] != b"":
                    return None
                m, pubs = finalizer.parse_multisig(Script(redeem))
                sigs = pushes[1:-1]
                if len(sigs) != m:
                    return None
                h = to_sign.sighash_legacy(0, Script(redeem))
                pi = 0
                for s in sigs:                                           # ordered, consensus-style
                    if len(s) < 9 or s[-1] != SIGHASH.ALL:
                        return None
                    sig = ec.Signature.parse(s[:-1])
                    while pi < len(pubs) and not pubs[pi].verify(sig, h):
                        pi += 1
                    if pi >= len(pubs):
                        return None
                    pi += 1
                return address
            if len(data) == 22 and data[:2] == b"\x00\x14":              # p2wpkh
                if len(items) != 2:
                    return None
                sig_all, pub = items
                if len(sig_all) < 9 or sig_all[-1] != SIGHASH.ALL:
                    return None
                if hashes.hash160(pub) != data[2:]:                      # pubkey must own the address
                    return None
                h = to_sign.sighash_segwit(0, script.p2pkh_from_p2wpkh(spk), 0)
                if ec.PublicKey.parse(pub).verify(ec.Signature.parse(sig_all[:-1]), h):
                    return address
                return None
            if len(data) == 34 and data[:2] == b"\x51\x20":              # p2tr key-path
                if len(items) != 1:
                    return None
                s = items[0]
                if len(s) == 65 and s[64] == SIGHASH.ALL:
                    h = to_sign.sighash_taproot(0, [spk], [0], sighash=SIGHASH.ALL)
                    s = s[:64]
                elif len(s) == 64:
                    h = to_sign.sighash_taproot(0, [spk], [0])
                else:
                    return None
                if ec.PublicKey.from_xonly(data[2:]).schnorr_verify(ec.SchnorrSig.parse(s), h):
                    return address
                return None
            if len(data) == 34 and data[:2] == b"\x00\x20":              # p2wsh — multisig template
                # witness = [dummy(empty, the CHECKMULTISIG bug), sig_1..sig_m, witness_script].
                # No script interpreter needed: the standard K-of-N template is parsed and its
                # CHECKMULTISIG semantics (ORDERED sigs, exactly m, all SIGHASH_ALL) applied here.
                from embit import finalizer
                if len(items) < 3 or items[0] != b"":
                    return None
                ws_raw = items[-1]
                if hashlib.sha256(ws_raw).digest() != data[2:]:          # script must own the address
                    return None
                m, pubs = finalizer.parse_multisig(Script(ws_raw))
                sigs = items[1:-1]
                if len(sigs) != m:
                    return None
                h = to_sign.sighash_segwit(0, Script(ws_raw), 0)
                pi = 0
                for s in sigs:                                           # ordered, consensus-style
                    if len(s) < 9 or s[-1] != SIGHASH.ALL:
                        return None
                    sig = ec.Signature.parse(s[:-1])
                    while pi < len(pubs) and not pubs[pi].verify(sig, h):
                        pi += 1
                    if pi >= len(pubs):
                        return None                                      # a sig matched no pubkey
                    pi += 1
                return address
        except Exception:
            return None
        return None

    # ---- BIP-322 for p2wsh multisig: each cosigner contributes a partial sig, then assemble ----
    def bip322_multisig_partial(self, secret: str, message: str, witness_script_hex: str,
                                path: Optional[str] = None) -> str:
        """One cosigner's partial BIP-322 signature over `message` for a p2wsh multisig address
        (defined by its witness script). Collect >= K of these, then bip322_multisig_assemble."""
        ws = Script(bytes.fromhex(witness_script_hex))
        to_sign = _bip322_to_sign(_bip322_to_spend(script.p2wsh(ws), message).txid())
        h = to_sign.sighash_segwit(0, ws, 0)
        leaf = self._leaf(secret, path, "wpkh")
        return (leaf.key.sign(h).serialize() + bytes([SIGHASH.ALL])).hex()

    def bip322_multisig_assemble(self, message: str, witness_script_hex: str,
                                 partial_sigs_hex: list) -> str:
        """Assemble cosigners' partial sigs into one BIP-322 signature. Sigs are matched to
        pubkeys and ORDERED per CHECKMULTISIG; invalid/duplicate sigs are dropped; raises if
        fewer than K valid signatures remain."""
        from embit import finalizer
        ws_raw = bytes.fromhex(witness_script_hex)
        ws = Script(ws_raw)
        m, pubs = finalizer.parse_multisig(ws)
        to_sign = _bip322_to_sign(_bip322_to_spend(script.p2wsh(ws), message).txid())
        h = to_sign.sighash_segwit(0, ws, 0)
        remaining = [bytes.fromhex(s) for s in partial_sigs_hex]
        ordered = []
        for pub in pubs:                                     # order by pubkey position in the script
            for s in remaining:
                try:
                    if s[-1] == SIGHASH.ALL and pub.verify(ec.Signature.parse(s[:-1]), h):
                        ordered.append(s)
                        remaining.remove(s)
                        break
                except Exception:
                    continue
        if len(ordered) < m:
            raise ValueError(f"only {len(ordered)} valid signature(s), need {m}")
        wit = Witness([b""] + ordered[:m] + [ws_raw])
        return base64.b64encode(wit.serialize()).decode()

    def _addr_for_kind(self, pub: ec.PublicKey, kind: str) -> str:
        if kind == "pkh":
            return script.p2pkh(pub).address(self.net)
        if kind == "sh-wpkh":
            return script.p2sh(script.p2wpkh(pub)).address(self.net)
        if kind == "tr":
            return script.p2tr(pub).address(self.net)
        return script.p2wpkh(pub).address(self.net)

    def verify_message(self, message: str, signature, expected) -> Optional[str]:
        """Verify a signed message against a pinned pubkey OR an address.
        `expected` = hex pubkey (33/65B) → raw BSM-ECDSA verify; anything else is treated as an
        ADDRESS: BIP-137 compact (65B) → pubkey recovery + exact address compare, any other
        signature → BIP-322 'simple' witness verify (incl. taproot key-path).
        Returns the signer address if valid, else None. Feeds WalletSignatureOverseer(verifier=…)."""
        try:
            h = _bsm_hash(message)
            exp = expected.strip()
            if exp.startswith(("02", "03", "04")) and _is_hex(exp):     # pinned by pubkey
                sig = signature if isinstance(signature, ec.Signature) else ec.Signature.parse(_hexbytes(signature))
                pub = ec.PublicKey.parse(bytes.fromhex(exp))
                if pub.verify(sig, h):
                    return script.p2wpkh(pub).address(self.net)
                return None
            # pinned by ADDRESS: a 65-byte compact signature is BIP-137 (recover the pubkey; the
            # declared-type address must match exactly); anything else is tried as a BIP-322
            # 'simple' witness (covers taproot). Both fail closed.
            if _compact_sig_bytes(signature) is not None:
                addr = self.recover_address(message, signature)
                return addr if addr is not None and addr == exp else None
            return (self.verify_message_bip322(message, signature, exp)
                    or self.verify_message_bip322_full(message, signature, exp))
        except Exception:
            return None

    def make_verifier(self, pinned: str):
        """A closure verifier(message, signature)->address for overseer.WalletSignatureOverseer.
        `pinned` = hex pubkey (raw BSM-ECDSA) or an ADDRESS (BIP-137 compact signatures)."""
        def _v(message, signature):
            return self.verify_message(message, signature, pinned)
        return _v

    # ---- PSBT signing (the key stays inside the module) ----
    def sign_psbt(self, secret: str, psbt_b64: str) -> str:
        root = self._root(secret)
        tx = PSBT.from_base64(psbt_b64.strip())
        tx.sign_with(root)                          # adds signatures for every input this root owns
        return tx.to_base64()

    # ---- multisig round: merge cosigners' partial sigs, know when N-of-M is met, finalize ----
    def combine_psbts(self, psbts_b64: list) -> str:
        """Merge partial signatures from several cosigners (each signed the SAME PSBT) into one."""
        if not psbts_b64:
            raise ValueError("no PSBTs to combine")
        base = PSBT.from_base64(psbts_b64[0].strip())
        for other_b64 in psbts_b64[1:]:
            o = PSBT.from_base64(other_b64.strip())
            for i, inp in enumerate(base.inputs):
                for pub, sig in o.inputs[i].partial_sigs.items():
                    inp.partial_sigs[pub] = sig                # union of signatures
        return base.to_base64()

    def signatures_present(self, psbt_b64: str) -> list:
        """Per-input count of partial signatures collected so far (multisig progress)."""
        tx = PSBT.from_base64(psbt_b64.strip())
        return [len(inp.partial_sigs) for inp in tx.inputs]

    def threshold(self, psbt_b64: str) -> list:
        """Per-input (M, N) for multisig inputs — reads the witness/redeem script. (1,1) for single-sig."""
        from embit import finalizer
        tx = PSBT.from_base64(psbt_b64.strip())
        out = []
        for inp in tx.inputs:
            scr = inp.witness_script or inp.redeem_script
            try:
                m, pubs = finalizer.parse_multisig(scr)
                out.append((m, len(pubs)))
            except Exception:
                out.append((1, 1))
        return out

    def is_complete(self, psbt_b64: str) -> bool:
        """True only if EVERY input has met its real M-of-N signature threshold (not just 'some sigs')."""
        present = self.signatures_present(psbt_b64)
        needed = self.threshold(psbt_b64)
        return all(p >= m for p, (m, _n) in zip(present, needed)) and len(present) > 0

    def finalize(self, psbt_b64: str) -> str:
        """Finalize a fully-signed (multisig) PSBT → network-ready raw transaction hex."""
        from embit import finalizer
        tx = finalizer.finalize_psbt(PSBT.from_base64(psbt_b64.strip()))
        return tx.serialize().hex()

    def decode_psbt(self, psbt_b64: str) -> dict:
        """Human-readable summary for the per-sign approval gate: inputs, outputs, amounts, fee.
        Iterate the TX vouts BY POSITION — PSBT output-metadata objects compare equal when empty, so
        `tx.outputs.index(o)` collapses every output onto vout[0], hiding real outputs from the gate."""
        tx = PSBT.from_base64(psbt_b64.strip())
        outs, out_total = [], 0
        for vout in tx.tx.vout:
            try:
                addr = vout.script_pubkey.address(self.net)
            except Exception:
                addr = vout.script_pubkey.data.hex()
            outs.append({"address": addr, "sats": vout.value})
            out_total += vout.value
        in_total = sum((inp.utxo.value if inp.utxo else 0) for inp in tx.inputs)
        fee = in_total - out_total if in_total else None
        return {"inputs": len(tx.inputs), "in_sats": in_total or None,
                "outputs": outs, "out_sats": out_total,
                "fee_sats": fee, "network": self.network}


def _urandom(n: int) -> bytes:
    import os
    return os.urandom(n)


def _hexbytes(x) -> bytes:
    if isinstance(x, (bytes, bytearray)):
        return bytes(x)
    return bytes.fromhex(str(x))


def _is_hex(s: str) -> bool:
    try:
        bytes.fromhex(s)
        return True
    except ValueError:
        return False


def _script_pushes(data: bytes) -> Optional[list]:
    """Parse a script that consists ONLY of data pushes (a scriptSig for p2pkh / p2sh spends).
    Returns the pushed items, or None if any non-push opcode appears (fail-closed)."""
    out, i, n = [], 0, len(data)
    while i < n:
        op = data[i]
        i += 1
        if op == 0x00:                                    # OP_0 — the CHECKMULTISIG dummy
            out.append(b"")
            continue
        if 1 <= op <= 75:
            ln = op
        elif op == 0x4c and i < n:                        # OP_PUSHDATA1
            ln = data[i]; i += 1
        elif op == 0x4d and i + 1 < n:                    # OP_PUSHDATA2
            ln = int.from_bytes(data[i:i + 2], "little"); i += 2
        else:
            return None
        if i + ln > n:
            return None
        out.append(data[i:i + ln])
        i += ln
    return out


def _compact_sig_bytes(signature) -> Optional[bytes]:
    """Coerce a BIP-137 compact signature (bytes / hex / base64) to its 65 raw bytes, else None."""
    if isinstance(signature, (bytes, bytearray)):
        return bytes(signature) if len(signature) == 65 else None
    s = str(signature).strip()
    if len(s) == 130 and _is_hex(s):
        return bytes.fromhex(s)
    try:
        raw = base64.b64decode(s, validate=True)
        return raw if len(raw) == 65 else None
    except Exception:
        return None
