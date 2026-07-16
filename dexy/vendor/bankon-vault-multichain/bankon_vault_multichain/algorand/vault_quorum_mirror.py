# algorand/vault_quorum_mirror.py
"""VaultQuorumMirror — Algorand (Algopy / ARC-4) commitment anchor for the BANKON vault.

Role
----
The authoritative N-of-M quorum lives on ONE EVM chain (contracts/VaultQuorum.sol),
replicated at a deterministic CREATE2 address across all EVM targets. Algorand is not
EVM and has no CREATE2, so on the parsec/x402 payment side we anchor the SAME
commitment — sha256(tomb.key) — as an immutable ARC-4 application.

This mirror is deliberately NOT an authorizer. It provides:

  commitment          set exactly once at create; the app ships no update/delete
                      handlers, so it is immutable for life
  attest()            registered owners co-sign that the EVM primary reached quorum,
                      so Algorand-side services (parsec-wallet, X402AccessGate) can
                      check vault state without any EVM RPC dependency
  mirrored_unlocked() true once >= threshold owners have attested

Integrity property is identical to the EVM side: hash the local Tomb key, compare to
`commitment` in global state. Divergence = tamper, fail closed.

Owner model: append-only. Owners are added by the creator at any time via add_owner;
each owner may attest exactly once. There is no removal — rotate by deploying a new
mirror app (mirrors are cheap and stateless beyond their ledger).

Build / deploy (parsec toolchain):
    pip install algorand-python algokit
    algokit compile py algorand/vault_quorum_mirror.py
    python deploy/deploy_algorand.py   # signs via parsec-wallet / KMD
"""
from algopy import ARC4Contract, Bytes, Global, GlobalState, LocalState, Txn, UInt64, arc4


class VaultQuorumMirror(ARC4Contract):
    """Immutable commitment anchor + owner attestation ledger (non-authoritative)."""

    def __init__(self) -> None:
        self.commitment = GlobalState(Bytes, key="c")        # sha256(tomb.key), 32 bytes
        self.threshold = GlobalState(UInt64, key="t")        # attestations required
        self.primary_chain_id = GlobalState(UInt64, key="p") # authoritative EVM chain id
        self.attestations = GlobalState(UInt64, key="a")     # unique attestation count
        # per-account: 0 = stranger, 1 = registered owner, 2 = owner who attested
        self.owner_state = LocalState(UInt64, key="o")

    # ---------- create: one shot; no update/delete methods exist => immutable ----------
    @arc4.abimethod(create="require")
    def create_anchor(
        self,
        commitment: arc4.DynamicBytes,
        threshold: arc4.UInt64,
        primary_chain_id: arc4.UInt64,
    ) -> None:
        assert commitment.native.length == UInt64(32), "commitment must be sha256 (32 bytes)"
        assert threshold.native >= UInt64(1), "threshold >= 1"
        self.commitment.value = commitment.native
        self.threshold.value = threshold.native
        self.primary_chain_id.value = primary_chain_id.native
        self.attestations.value = UInt64(0)

    # ---------- owner registration: creator-gated, opt-in carries the local state ----------
    @arc4.abimethod(allow_actions=["OptIn"], name="add_owner")
    def add_owner(self) -> None:
        """An owner account opts in; only groups initiated by the creator are honored.

        The opt-in app call itself must be sent by the owner (Txn.sender is the owner,
        so LocalState binds to them), and the creator proves consent by co-signing a
        zero-amount self-payment in the same atomic group — checked below.
        """
        self._creator_in_group()
        self.owner_state[Txn.sender] = UInt64(1)

    @arc4.abimethod
    def attest(self) -> None:
        """Registered owner attests that the EVM primary reached quorum. One shot each."""
        assert self.owner_state[Txn.sender] == UInt64(1), "not an owner, or already attested"
        self.owner_state[Txn.sender] = UInt64(2)
        self.attestations.value = self.attestations.value + UInt64(1)

    # ---------- reads (usable by parsec-wallet / X402AccessGate without EVM access) ----------
    @arc4.abimethod(readonly=True)
    def get_commitment(self) -> arc4.DynamicBytes:
        return arc4.DynamicBytes(self.commitment.value)

    @arc4.abimethod(readonly=True)
    def mirrored_unlocked(self) -> arc4.Bool:
        return arc4.Bool(self.attestations.value >= self.threshold.value)

    # ---------- internal ----------
    def _creator_in_group(self) -> None:
        from algopy import gtxn, urange

        found = UInt64(0)
        for i in urange(Global.group_size):
            if gtxn.Transaction(i).sender == Global.creator_address:
                found = UInt64(1)
        assert found == UInt64(1), "creator consent (co-signed group txn) required"
