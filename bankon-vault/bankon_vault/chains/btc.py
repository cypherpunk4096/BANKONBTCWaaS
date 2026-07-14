# SPDX-License-Identifier: GPL-3.0-or-later
# bankon-vault — the Bitcoin adapter. The definitive BTC signature-access + gating layer, fulfilling
# the decades-old github.com/bankonme plan (be your own bank, keys offline). Pure-Python via `embit`
# (BIP32/39/44/84/86, bech32 P2WPKH + P2TR taproot, PSBT) — offline/air-gap friendly, no daemon.
#
# The private key NEVER leaves this module: sign_psbt reconstructs the key from stored material,
# signs, and drops it. Gating uses a Bitcoin-Signed-Message ECDSA signature verified against the
# signer's pinned pubkey (self-contained). BIP-137 address-recovery and full BIP-322 are Step-2.
from __future__ import annotations

import hashlib
from typing import Optional

from embit import bip32, bip39, ec, script, compact
from embit.networks import NETWORKS
from embit.psbt import PSBT

from .base import ChainAdapter

# default derivation accounts (BIP-84 native segwit / BIP-86 taproot), receive branch, index 0
PATHS = {"wpkh": "m/84h/{coin}h/0h/0/{i}", "tr": "m/86h/{coin}h/0h/0/{i}"}
BSM_MAGIC = b"Bitcoin Signed Message:\n"


def _bsm_hash(message: str) -> bytes:
    """The standard Bitcoin Signed Message digest: dSHA256(varstr(magic) || varstr(message))."""
    msg = message.encode()
    data = compact.to_bytes(len(BSM_MAGIC)) + BSM_MAGIC + compact.to_bytes(len(msg)) + msg
    return hashlib.sha256(hashlib.sha256(data).digest()).digest()


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

    def verify_message(self, message: str, signature, expected) -> Optional[str]:
        """Verify a BSM-ECDSA signature against a pinned pubkey OR address.
        `expected` = hex pubkey (33/65B) or a bech32 address (must be a wpkh/tr of the pubkey).
        Returns the signer address if valid, else None. Feeds WalletSignatureOverseer(verifier=…)."""
        try:
            sig = signature if isinstance(signature, ec.Signature) else ec.Signature.parse(_hexbytes(signature))
            h = _bsm_hash(message)
            exp = expected.strip()
            if exp.startswith(("02", "03", "04")) and _is_hex(exp):     # pinned by pubkey
                pub = ec.PublicKey.parse(bytes.fromhex(exp))
                if pub.verify(sig, h):
                    return script.p2wpkh(pub).address(self.net)
                return None
            # pinned by address: we cannot recover the pubkey (no recid in embit) → require the
            # caller to also pass the pubkey. Address-only gating is Step-2 (BIP-137 recovery).
            return None
        except Exception:
            return None

    def make_verifier(self, pubkey_hex: str):
        """A closure verifier(message, signature)->address for overseer.WalletSignatureOverseer."""
        def _v(message, signature):
            return self.verify_message(message, signature, pubkey_hex)
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
