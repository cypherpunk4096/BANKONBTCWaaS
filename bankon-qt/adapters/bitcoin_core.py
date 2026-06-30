"""Bitcoin Core adapter — the canonical anchor backend.

Wraps the shared RPC service (Console-cache-routed, node fallback) for chain state and
the non-custodial WaaS API for wallet/broadcast/anchor. Keeps the wallet itself
non-custodial: balances come from watch-only descriptors and tx building/anchoring happen
in the WaaS, never holding user keys here.
"""
import json, os, urllib.request

from services.rpc_service import rpc, synctip
from .base import ChainAdapter

WAAS_URL = os.environ.get("BANKON_WAAS_URL", "http://127.0.0.1:8088")


def _waas(path, payload=None, timeout=20):
    url = WAAS_URL + path
    if payload is None:
        req = urllib.request.Request(url)
    else:
        req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


class BitcoinCoreAdapter(ChainAdapter):
    name = "bitcoin"
    caip2 = "bip122:000000000019d6689c085ae165831e93"   # Bitcoin mainnet (genesis hash)

    def health_check(self) -> dict:
        st = {}
        try: st = synctip()
        except Exception: pass
        conns = None
        try: conns = rpc("getnetworkinfo", timeout=8).get("connections")
        except Exception: pass
        prog = st.get("progress") or 0
        return {"chain": "main", "height": st.get("height"), "progress": prog,
                "connections": conns, "synced": prog >= 0.9999}

    def get_height(self) -> int:
        return synctip().get("height")

    def get_balance(self, wallet=None):
        # non-custodial: watch-only balance from the WaaS registry
        if not wallet:
            return None
        return _waas(f"/api/wallet/{wallet}/balance")

    def build_tx(self, outputs, fee_rate=None):
        # tx construction is the WaaS's non-custodial PSBT flow (sign happens client-side)
        raise NotImplementedError("use the WaaS /api/wallet/<name>/send PSBT flow")

    def broadcast_tx(self, hex_tx) -> str:
        out = _waas("/api/broadcast", {"hex": hex_tx})
        return out.get("txid")

    # --- canonical OP_RETURN anchor (implemented by the WaaS, regtest-proven) ---
    def anchor(self, hash_hex: str) -> dict:
        return _waas("/api/anchor", {"hash": hash_hex})

    def verify_anchor(self, txid: str, data) -> dict:
        return _waas("/api/verify", {"txid": txid, "data": data})
