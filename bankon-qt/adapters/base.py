"""ChainAdapter — the chain-agnostic interface from the master Qt architecture guide.

The UI and business logic stay chain-agnostic; each backend implements this contract.
Only the Bitcoin Core adapter exists today (the "ship the Bitcoin anchor first" phase);
EVM and Algorand/x402 adapters are future work behind the same interface. A registry of
adapters keyed by CAIP-2 network id is the intended growth path (the doc's `chainmapping`).
"""
from abc import ABC, abstractmethod


class ChainAdapter(ABC):
    name = "chain"
    caip2 = None   # CAIP-2 network id, e.g. "bip122:000000000019d6689c085ae165831e93"

    @abstractmethod
    def health_check(self) -> dict:
        """Uniform health: sync status, connections, latest height, latency."""

    @abstractmethod
    def get_height(self) -> int:
        ...

    @abstractmethod
    def get_balance(self, wallet=None):
        ...

    @abstractmethod
    def build_tx(self, outputs, fee_rate=None):
        ...

    @abstractmethod
    def broadcast_tx(self, hex_tx) -> str:
        ...

    # --- canonical anchor (Bitcoin-specific; optional on other chains) ---
    def anchor(self, hash_hex: str) -> dict:
        raise NotImplementedError("anchoring not supported on this chain")

    def verify_anchor(self, txid: str, data) -> dict:
        raise NotImplementedError("anchor verification not supported on this chain")
