# SPDX-License-Identifier: GPL-3.0-or-later
# bankon-vault — the DAIO ceremony: a bankon.vault whose quorum IS the DAIO's executive council.
#
# FROM the DAIO: the council (CEO + the Seven Soldiers: CISO, CRO, CFO, CPO, COO, CTO, CLO), their
# addresses and their role weights are read from the DAIO's ExecutiveRoles contract and exported to a
# file the air-gapped ceremony reads. WITH the DAIO: the threshold is the DAIO's 2/3 supermajority of the
# share-holding seats; reconstitution needs, besides K shares, the DAIO's weighted approval (>= 2/3 of the
# council's weight) and the CISO's and CRO's veto pieces; every holder signs an approval naming the DAIO
# proposal that authorises it; and when the council changes, the master is rotated (vault.rekey), so a
# removed executive's old share opens nothing.
#
# A share is sealed to its seat: the holder signs an enrolment message with the seat's wallet
# (EIP-191 personal_sign), and the share is encrypted (AES-256-GCM) under HKDF-SHA512 of that
# signature. Wallets sign deterministically (RFC 6979), so the holder reproduces the signature to open
# it; a leaked envelope is useless without the seat's wallet.
"""
    council = Council.from_chain(rpc, roles_contract)          # online: export the DAIO council
    council.save("council.json")
    # AIR-GAPPED: each seat signs enrolment_message(...); then
    envelopes, manifest = genesis(council, enrolment_signatures)
    # hand each envelope to its seat; publish the manifest (no secret)

    s = Session(manifest, proposal_id=42, action="unlock")      # to unlock later
    s.present("CISO", envelope, enrol_sig, approval_sig) ...    # each holder, with their wallet
    vault.unlock(DaioOverseer(s, vault_salt))                   # once s.ready()
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.hashes import SHA512
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from . import shamir
from .evm import keccak256, recover_personal_sign

VERSION = "1.0.0"
MASTER_BYTES = 64
DAIO_INFO = b"bankon-overseer-daio-v1"

# DAIO ExecutiveRoles.ExecutiveRole (0 = NONE) and its default role weights in basis points
SEATS = ["CEO", "CISO", "CRO", "CFO", "CPO", "COO", "CTO", "CLO"]
DEFAULT_WEIGHTS = {"CEO": 0, "CISO": 1200, "CRO": 1200, "CFO": 1000, "CPO": 1000, "COO": 1000, "CTO": 1000, "CLO": 800}
DEFAULT_VETO = ("CISO", "CRO")             # hasVetoPower in the DAIO's defaults
SUPERMAJORITY = (2, 3)                    # the boardroom's 0.666, ExecutiveRoles' 2/3 majority


class DaioError(ValueError):
    pass


def _addr(a: str) -> str:
    a = str(a).strip()
    if not (a.startswith("0x") and len(a) == 42 and all(c in "0123456789abcdefABCDEF" for c in a[2:])):
        raise DaioError(f"not an address: {a}")
    return a


def supermajority(n: int) -> int:
    return math.ceil(n * SUPERMAJORITY[0] / SUPERMAJORITY[1])


# ── the council, from the DAIO ──────────────────────────────────────────────────────────────────────
@dataclass
class Seat:
    role: str
    address: str
    weight: int
    veto: bool = False

    def to_dict(self):
        return {"role": self.role, "address": self.address, "weight": self.weight, "veto": self.veto}


@dataclass
class Council:
    seats: List[Seat]
    source: dict = field(default_factory=dict)      # {chainId, contract, block} when read from chain

    def seat(self, role: str) -> Seat:
        for s in self.seats:
            if s.role == role:
                return s
        raise DaioError(f"no seat {role}")

    @property
    def holders(self) -> List[Seat]:
        """Seats that hold shares: those with a vote (weight > 0). The CEO's DAIO weight is 0 (an
        emergency override, not a regular vote), so by default the CEO holds no share."""
        return [s for s in self.seats if s.weight > 0]

    def to_dict(self):
        return {"version": VERSION, "source": self.source, "seats": [s.to_dict() for s in self.seats]}

    def save(self, path: str):
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2)

    @classmethod
    def from_dict(cls, d: dict) -> "Council":
        seats = []
        for s in d["seats"]:
            if s["role"] not in SEATS:
                raise DaioError(f"unknown DAIO seat {s['role']}")
            seats.append(Seat(s["role"], _addr(s["address"]), int(s["weight"]), bool(s.get("veto", False))))
        if len({s.role for s in seats}) != len(seats) or len({s.address.lower() for s in seats}) != len(seats):
            raise DaioError("a seat or an address appears twice")
        return cls(seats, d.get("source", {}))

    @classmethod
    def load(cls, path: str) -> "Council":
        with open(path) as f:
            return cls.from_dict(json.load(f))

    @classmethod
    def from_chain(cls, rpc: str, roles_contract: str, veto=DEFAULT_VETO, timeout: float = 15) -> "Council":
        """Read the council from the DAIO's ExecutiveRoles: getRoleHolder(uint8) and roleWeights(uint8)
        for each seat, at one block. Empty seats (the zero address) are left out."""
        roles_contract = _addr(roles_contract)

        def call(method: str, data: str, block="latest"):
            body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method,
                               "params": [{"to": roles_contract, "data": data}, block] if data else []}).encode()
            req = urllib.request.Request(rpc, body, {"content-type": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                out = json.loads(r.read())
            if "error" in out:
                raise DaioError(f"{method}: {out['error']}")
            return out["result"]

        sel = lambda sig: keccak256(sig.encode())[:4].hex()
        block = call("eth_blockNumber", "")
        chain = call("eth_chainId", "")
        seats = []
        for i, role in enumerate(SEATS, start=1):
            arg = format(i, "064x")
            holder = "0x" + call("eth_call", "0x" + sel("getRoleHolder(uint8)") + arg, block)[-40:]
            if int(holder, 16) == 0:
                continue
            weight = int(call("eth_call", "0x" + sel("roleWeights(uint8)") + arg, block), 16)
            seats.append(Seat(role, holder, weight, role in veto))
        return cls(seats, {"chainId": int(chain, 16), "contract": roles_contract, "block": int(block, 16)})


# ── messages a seat signs ───────────────────────────────────────────────────────────────────────────
def enrolment_message(ceremony: str, role: str, address: str) -> str:
    return (f"BANKON-VAULT DAIO ceremony {ceremony}\nseat: {role}\naddress: {address.lower()}\n"
            f"I hold this seat's share of the vault for the DAIO. Signing moves nothing.")


def approval_message(ceremony: str, proposal: int, action: str, payload: Optional[str] = None) -> str:
    """What each seat signs to approve one DAIO-authorised action. For action 'sign' the payload is the
    sha256 of the one PSBT the quorum approves: the vault signs that transaction and no other."""
    m = f"BANKON-VAULT DAIO approval\nceremony: {ceremony}\nproposal: {int(proposal)}\naction: {action}"
    return m + (f"\npayload: {payload}" if payload else "")


def _check_sig(message: str, signature: str, address: str, what: str):
    got = recover_personal_sign(message, signature)
    if not got or got.lower() != address.lower():
        raise DaioError(f"{what}: the signature is not from {address}")


# ── envelopes: a share sealed to its seat ───────────────────────────────────────────────────────────
def _wrap_key(ceremony: str, role: str, address: str, enrol_sig: str) -> bytes:
    sig = bytes.fromhex(enrol_sig[2:] if enrol_sig.startswith("0x") else enrol_sig)
    return HKDF(algorithm=SHA512(), length=32, salt=hashlib.sha256(ceremony.encode()).digest(),
                info=b"bankon-daio-envelope-v1:" + role.encode() + b":" + address.lower().encode()).derive(sig)


def _aad(ceremony, role, address) -> bytes:
    return f"{ceremony}|{role}|{address.lower()}".encode()


def seal(ceremony: str, seat: Seat, enrol_sig: str, payload: dict) -> dict:
    nonce = os.urandom(12)
    ct = AESGCM(_wrap_key(ceremony, seat.role, seat.address, enrol_sig)).encrypt(
        nonce, json.dumps(payload).encode(), _aad(ceremony, seat.role, seat.address))
    return {"version": VERSION, "ceremony": ceremony, "role": seat.role, "address": seat.address,
            "nonce": nonce.hex(), "ct": ct.hex()}


def unseal(envelope: dict, enrol_sig: str) -> dict:
    try:
        pt = AESGCM(_wrap_key(envelope["ceremony"], envelope["role"], envelope["address"], enrol_sig)).decrypt(
            bytes.fromhex(envelope["nonce"]), bytes.fromhex(envelope["ct"]),
            _aad(envelope["ceremony"], envelope["role"], envelope["address"]))
    except Exception:
        raise DaioError(f"{envelope.get('role')}: the envelope does not open with that signature")
    return json.loads(pt)


def _fp(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()[:16]


def _commit(role: str, blob: bytes) -> str:
    return hashlib.sha256(role.encode() + b"\0" + blob).hexdigest()[:16]


def _xor(a: bytes, b: bytes) -> bytes:
    return bytes(x ^ y for x, y in zip(a, b))


def _wipe(b) -> None:
    if isinstance(b, bytearray):
        for i in range(len(b)):
            b[i] = 0


# ── genesis (air-gapped) ────────────────────────────────────────────────────────────────────────────
@dataclass
class DaioManifest:
    """Public ceremony record: the council, the rules, and commitments. It holds NO secret."""
    ceremony: str
    council: dict
    threshold: int
    total: int
    weight_total: int
    weight_required: int
    veto: List[str]
    master_fingerprint: str
    share_commitments: Dict[str, str]
    veto_commitments: Dict[str, str]
    created: int
    version: str = VERSION

    def to_dict(self):
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, d):
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})


def genesis(council: Council, enrolments: Dict[str, str], ceremony: str,
            master: Optional[bytes] = None) -> Tuple[Dict[str, dict], DaioManifest]:
    """Make a 64-byte master (or take one, for rotation), split it among the council's share-holding
    seats by the DAIO's rules, and seal each seat's part to its wallet. Returns ({role: envelope},
    manifest). Run AIR-GAPPED. The master is not returned and is wiped here."""
    holders = council.holders
    if len(holders) < 3:
        raise DaioError("a DAIO ceremony needs at least three voting seats")
    if not ceremony or not all(c in "0123456789abcdef" for c in ceremony):
        raise DaioError("genesis needs the ceremony id the seats enrolled for (new_ceremony())")
    for s in holders:                                   # every holder enrolled, by its own wallet
        if s.role not in enrolments:
            raise DaioError(f"{s.role} has not enrolled")
        _check_sig(enrolment_message(ceremony, s.role, s.address), enrolments[s.role], s.address, s.role)
    veto = [s.role for s in holders if s.veto]
    m = bytearray(master if master is not None else os.urandom(MASTER_BYTES))
    if len(m) != MASTER_BYTES:
        raise DaioError(f"master must be {MASTER_BYTES} bytes")
    fingerprint = _fp(bytes(m))
    # each veto seat holds a mandatory piece: core = master ⊕ piece₁ ⊕ piece₂ …; core is what is split
    pieces = {r: os.urandom(MASTER_BYTES) for r in veto}
    core = bytes(m)
    for p in pieces.values():
        core = _xor(core, p)
    k = supermajority(len(holders))
    raw = shamir.split(core, n=len(holders), k=k)
    envelopes, commits = {}, {}
    for (idx, share), s in zip(raw, holders):
        enc = shamir.encode_share((idx, share))
        commits[s.role] = _commit(s.role, enc.encode())
        payload = {"share": enc}
        if s.role in pieces:
            payload["veto_piece"] = pieces[s.role].hex()
        envelopes[s.role] = seal(ceremony, s, enrolments[s.role], payload)
    weight_total = sum(s.weight for s in holders)
    manifest = DaioManifest(
        ceremony=ceremony, council=council.to_dict(), threshold=k, total=len(holders),
        weight_total=weight_total, weight_required=supermajority(weight_total), veto=veto,
        master_fingerprint=fingerprint, share_commitments=commits,
        veto_commitments={r: _fp(p) for r, p in pieces.items()}, created=int(time.time()))
    _wipe(m)
    return envelopes, manifest


# ── reconstitution: a session the DAIO's quorum completes ───────────────────────────────────────────
class Session:
    """Collect seats' shares for one DAIO-approved action. Each presentation proves the seat (the
    enrolment signature opens its envelope and recovers to its address) and its approval of THIS
    proposal and action. Shares live in RAM only; wipe() clears them."""

    def __init__(self, manifest: DaioManifest, proposal: int, action: str, ttl: int = 600,
                 payload: Optional[str] = None):
        if action == "sign" and not (payload and len(payload) == 64 and all(c in "0123456789abcdef" for c in payload)):
            raise DaioError("a 'sign' session names the sha256 of the one PSBT it approves")
        self.payload = payload if action == "sign" else None
        self.manifest = manifest
        self.council = Council.from_dict(manifest.council)
        self.proposal, self.action = int(proposal), action
        self.expires = time.time() + ttl
        self._shares: Dict[str, str] = {}
        self._pieces: Dict[str, bytes] = {}

    def message(self) -> str:
        return approval_message(self.manifest.ceremony, self.proposal, self.action, self.payload)

    def present(self, role: str, envelope: dict, enrol_sig: str, approval_sig: str) -> dict:
        if time.time() > self.expires:
            self.wipe()
            raise DaioError("the session has expired")
        seat = self.council.seat(role)
        if seat.weight <= 0 or role not in self.manifest.share_commitments:
            raise DaioError(f"{role} holds no share in this ceremony")
        if envelope.get("ceremony") != self.manifest.ceremony or envelope.get("role") != role \
                or envelope.get("address", "").lower() != seat.address.lower():
            raise DaioError(f"{role}: the envelope is not this seat's, for this ceremony")
        _check_sig(enrolment_message(self.manifest.ceremony, role, seat.address), enrol_sig, seat.address, f"{role} enrolment")
        _check_sig(self.message(), approval_sig, seat.address, f"{role} approval of proposal {self.proposal}")
        payload = unseal(envelope, enrol_sig)
        if _commit(role, payload["share"].encode()) != self.manifest.share_commitments[role]:
            raise DaioError(f"{role}: the share does not match the manifest")
        if role in self.manifest.veto:
            piece = bytes.fromhex(payload.get("veto_piece", ""))
            if _fp(piece) != self.manifest.veto_commitments.get(role):
                raise DaioError(f"{role}: the veto piece does not match the manifest")
            self._pieces[role] = piece
        self._shares[role] = payload["share"]
        return self.status()

    def weight(self) -> int:
        return sum(self.council.seat(r).weight for r in self._shares)

    def status(self) -> dict:
        missing_veto = [r for r in self.manifest.veto if r not in self._pieces]
        return {"ceremony": self.manifest.ceremony, "proposal": self.proposal, "action": self.action,
                "presented": sorted(self._shares), "shares": len(self._shares), "threshold": self.manifest.threshold,
                "weight": self.weight(), "weight_required": self.manifest.weight_required,
                "veto_missing": missing_veto, "ready": self.ready(), "expires": int(self.expires)}

    def ready(self) -> bool:
        return (len(self._shares) >= self.manifest.threshold and self.weight() >= self.manifest.weight_required
                and all(r in self._pieces for r in self.manifest.veto) and time.time() <= self.expires)

    def master(self) -> bytearray:
        if not self.ready():
            raise DaioError("the DAIO quorum is not complete: " + json.dumps(self.status()))
        core = shamir.combine([shamir.decode_share(s) for s in self._shares.values()])
        for p in self._pieces.values():
            core = _xor(core, p)
        m = bytearray(core)
        if _fp(bytes(m)) != self.manifest.master_fingerprint:
            _wipe(m)
            raise DaioError("the reconstruction does not match the manifest")
        return m

    def wipe(self):
        self._shares.clear()
        self._pieces.clear()


class DaioOverseer:
    """Custody by the DAIO's quorum: drops into vault.unlock() once the session is ready."""
    kind = "daio-quorum"

    def __init__(self, session: Session, salt: bytes):
        self._s, self._salt = session, salt

    def fingerprint(self) -> str:
        return "daio:" + self._s.manifest.ceremony

    def verify_evidence(self, evidence, challenge: str) -> bool:
        return self._s.ready()

    def produce_raw_key(self, challenge: str, evidence) -> bytes:
        m = self._s.master()
        try:
            return HKDF(algorithm=SHA512(), length=MASTER_BYTES, salt=self._salt, info=DAIO_INFO).derive(bytes(m))
        finally:
            _wipe(m)


class _MasterOverseer:
    """Used once, inside rotate(): the freshly made master, for vault.rekey()."""
    kind = "daio-quorum"

    def __init__(self, master: bytearray, salt: bytes):
        self._m, self._salt = master, salt

    def fingerprint(self) -> str:
        return "daio:rotating"

    def verify_evidence(self, evidence, challenge: str) -> bool:
        return True

    def produce_raw_key(self, challenge: str, evidence) -> bytes:
        return HKDF(algorithm=SHA512(), length=MASTER_BYTES, salt=self._salt, info=DAIO_INFO).derive(bytes(self._m))


def new_ceremony() -> str:
    """A fresh ceremony id: the seats sign enrolment messages naming it before genesis."""
    return os.urandom(8).hex()


def rotate(vault, session: Session, new_council: Council, new_enrolments: Dict[str, str], ceremony: str):
    """The council changed: with the CURRENT quorum's session ready, make a new master for the new
    council and rekey the vault to it, so the old shares (a removed executive's included) open nothing.
    Returns (envelopes, manifest) for the new council. AIR-GAPPED."""
    if session.action != "rotate":
        raise DaioError("rotation needs a session approved for action 'rotate'")
    vault.unlock(DaioOverseer(session, vault._salt))
    new_master = bytearray(os.urandom(MASTER_BYTES))
    try:
        envelopes, manifest = genesis(new_council, new_enrolments, ceremony=ceremony, master=bytes(new_master))
        vault.rekey(_MasterOverseer(new_master, vault._salt))
        return envelopes, manifest
    finally:
        _wipe(new_master)
        session.wipe()
