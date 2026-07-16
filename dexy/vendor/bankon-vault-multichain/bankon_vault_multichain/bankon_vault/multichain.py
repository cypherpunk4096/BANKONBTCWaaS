# bankon_vault/multichain.py
"""Cross-chain integrity verification for the VaultQuorum anchor.

Confirms the SAME commitment is anchored at the SAME deterministic address on every
target chain, and that it matches the local Tomb key file. Any divergence is treated
as a tamper event and raises — integrity is the hard requirement, so we fail closed.

    pip install web3
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from web3 import Web3

_ABI = [
    {"inputs": [], "name": "commitment",
     "outputs": [{"type": "bytes32"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "threshold",
     "outputs": [{"type": "uint8"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "unlocked",
     "outputs": [{"type": "bool"}], "stateMutability": "view", "type": "function"},
]


@dataclass(frozen=True)
class ChainTarget:
    chain_id: int
    rpc: str            # sourced from agenticplace.pythai.net/allchain.html


def local_commitment(key_path: Path) -> bytes:
    """sha256 of the Tomb key file — the same value baked into every deployment."""
    return hashlib.sha256(key_path.read_bytes()).digest()


def verify_all(address: str, key_path: Path, targets: list[ChainTarget]) -> dict[int, dict]:
    """Read every chain's anchor and prove they agree with each other and the local key.

    Returns a per-chain report; raises ValueError on any divergence (fail closed).
    """
    expect = local_commitment(key_path)
    checksum = Web3.to_checksum_address(address)
    report: dict[int, dict] = {}
    seen: set[bytes] = set()

    for t in targets:
        w3 = Web3(Web3.HTTPProvider(t.rpc))
        if w3.eth.chain_id != t.chain_id:
            raise ValueError(f"RPC {t.rpc} reports chain {w3.eth.chain_id}, expected {t.chain_id}")
        c = w3.eth.contract(address=checksum, abi=_ABI)
        onchain = bytes(c.functions.commitment().call())
        seen.add(onchain)
        report[t.chain_id] = {
            "commitment": "0x" + onchain.hex(),
            "matches_local": onchain == expect,
            "unlocked": c.functions.unlocked().call(),
        }

    if len(seen) != 1:
        raise ValueError(f"TAMPER: commitment diverges across chains -> {report}")
    if seen.pop() != expect:
        raise ValueError("TAMPER: on-chain commitment does not match the local Tomb key")
    return report


if __name__ == "__main__":
    import json, os, sys

    addr = os.environ["VAULT_ADDRESS"]              # same on every chain
    key = Path(os.environ["VAULT_KEY"])             # e.g. /mnt/usb/operator.tomb.key
    targets = [ChainTarget(**t) for t in json.load(open(sys.argv[1]))]  # deploy/chains.json
    print(json.dumps(verify_all(addr, key, targets), indent=2))
