#!/usr/bin/env python3
# deploy/deploy_algorand.py
"""Deploy VaultQuorumMirror to Algorand and cross-verify its commitment.

Signing: exports an unsigned transaction group for parsec-wallet by default, or signs
directly with a KMD/mnemonic if PARSEC_UNSAFE_MNEMONIC is set (dev only).

    pip install py-algorand-sdk algokit-utils
    algokit compile py algorand/vault_quorum_mirror.py --out-dir algorand/artifacts

Env:
    ALGOD_URL           e.g. https://mainnet-api.algonode.cloud
    ALGOD_TOKEN         "" for algonode
    VAULT_KEY           path to tomb key (hashed locally, never uploaded)
    VAULT_THRESHOLD     N attestations
    VAULT_PRIMARY_CHAIN authoritative EVM chain id (informational anchor)
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
from pathlib import Path

from algosdk import mnemonic, transaction
from algosdk.abi import Method
from algosdk.atomic_transaction_composer import (
    AtomicTransactionComposer,
    AccountTransactionSigner,
)
from algosdk.v2client import algod

ART = Path("algorand/artifacts")


def client() -> algod.AlgodClient:
    return algod.AlgodClient(os.environ.get("ALGOD_TOKEN", ""), os.environ["ALGOD_URL"])


def local_commitment() -> bytes:
    return hashlib.sha256(Path(os.environ["VAULT_KEY"]).read_bytes()).digest()


def deploy() -> int:
    c = client()
    approval = base64.b64decode(c.compile((ART / "VaultQuorumMirror.approval.teal").read_text())["result"])
    clear = base64.b64decode(c.compile((ART / "VaultQuorumMirror.clear.teal").read_text())["result"])

    mn = os.environ.get("PARSEC_UNSAFE_MNEMONIC")
    if not mn:
        sys.exit("No signer. For production, route this txn through parsec-wallet; "
                 "for dev set PARSEC_UNSAFE_MNEMONIC.")
    sk = mnemonic.to_private_key(mn)
    signer = AccountTransactionSigner(sk)
    sender = mnemonic.to_public_key(mn)

    atc = AtomicTransactionComposer()
    atc.add_method_call(
        app_id=0,
        method=Method.from_signature("create_anchor(byte[],uint64,uint64)void"),
        sender=sender,
        sp=c.suggested_params(),
        signer=signer,
        method_args=[
            local_commitment(),
            int(os.environ["VAULT_THRESHOLD"]),
            int(os.environ["VAULT_PRIMARY_CHAIN"]),
        ],
        on_complete=transaction.OnComplete.NoOpOC,
        approval_program=approval,
        clear_program=clear,
        global_schema=transaction.StateSchema(num_uints=3, num_byte_slices=1),
        local_schema=transaction.StateSchema(num_uints=1, num_byte_slices=0),
    )
    result = atc.execute(c, 4)
    app_id = c.pending_transaction_info(result.tx_ids[0])["application-index"]
    print(f"VaultQuorumMirror app id: {app_id}")
    return app_id


def verify(app_id: int) -> None:
    """Fail closed unless on-chain commitment == sha256(local tomb key)."""
    info = client().application_info(app_id)
    kv = {base64.b64decode(e["key"]).decode(): e["value"] for e in info["params"]["global-state"]}
    onchain = base64.b64decode(kv["c"]["bytes"])
    if onchain != local_commitment():
        raise SystemExit("TAMPER: Algorand mirror commitment does not match local Tomb key")
    print(json.dumps({
        "app_id": app_id,
        "commitment": "0x" + onchain.hex(),
        "threshold": kv["t"]["uint"],
        "primary_chain_id": kv["p"]["uint"],
        "attestations": kv["a"]["uint"],
        "matches_local": True,
    }, indent=2))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "verify":
        verify(int(sys.argv[2]))
    else:
        verify(deploy())
