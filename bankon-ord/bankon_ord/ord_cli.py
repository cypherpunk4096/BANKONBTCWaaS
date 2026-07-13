# SPDX-License-Identifier: CC0-1.0
# bankon-ord — a SEPARATE, OPTIONAL module wrapping the official `ord` CLI (ordinals/inscriptions/
# runes) for BANKON. Isolated from bankon-vault and BANKON core: install it only if you want ordinals.
#
# Safety is the whole point (from the ord docs, load-bearing):
#   • ordinal (inscription-bearing) and cardinal (plain-BTC) wallets are STRICTLY separate — a generic
#     bitcoin-cli/RPC spend on an ord wallet can DESTROY inscriptions. This module enforces that.
#   • "ord should not be used with wallets that contain material amounts of funds."
#   • the ord server explorer hosts untrusted HTML/JS — bind it to loopback only.
#   • inscribing needs Bitcoin Core >= 28 and txindex=1.
#
# Networks: mainnet + testnet (also signet + regtest). ord signs via Bitcoin Core's wallet; this
# wrapper never handles keys itself — gating/approval happen before it calls `ord`.
"""
OrdCli — a thin, safe wrapper around the `ord` binary. Read ops return parsed JSON; mutating ops are
gated. Degrades honestly: if `ord` isn't installed, `available()` is False and calls raise a clear
message pointing at install.sh.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Optional

MIN_ORD = (0, 18, 0)                 # runes + modern wallet; inscribing wants Core >= 28
MIN_CORE_FOR_INSCRIBE = 28


@dataclass
class NetConfig:
    """Per-network wiring: ord's --chain name, Core RPC port, cookie/datadir, ord index dir."""
    name: str                         # our name: mainnet|testnet|signet|regtest
    ord_chain: str                    # ord's --chain value
    rpc_port: int
    datadir: str
    is_test: bool = True              # everything except mainnet

    @property
    def rpc_url(self) -> str:
        return f"http://127.0.0.1:{self.rpc_port}"

    @property
    def cookie(self) -> str:
        # Core writes .cookie into the network subdir (except mainnet, which uses the root)
        sub = {"mainnet": "", "testnet": "testnet3", "signet": "signet", "regtest": "regtest"}[self.name]
        return os.path.join(self.datadir, sub, ".cookie")


def _datadir() -> str:
    return os.environ.get("BANKON_BTC_DATADIR", os.path.expanduser("~/.bitcoin"))


NETWORKS: dict[str, NetConfig] = {
    "mainnet": NetConfig("mainnet", "mainnet", 8332, _datadir(), is_test=False),
    "testnet": NetConfig("testnet", "testnet", 18332, _datadir()),
    "signet":  NetConfig("signet",  "signet",  38332, _datadir()),
    "regtest": NetConfig("regtest", "regtest", 18443, _datadir()),
}


def resolve_network(name: str) -> NetConfig:
    key = {"main": "mainnet", "test": "testnet", "test3": "testnet"}.get(name, name)
    if key not in NETWORKS:
        raise ValueError(f"unknown network {name!r} — use mainnet/testnet/signet/regtest")
    return NETWORKS[key]


class OrdError(RuntimeError):
    pass


class OrdCli:
    def __init__(self, network: str = "mainnet", ord_bin: str = "ord",
                 index_dir: Optional[str] = None, rpc_cookie: Optional[str] = None):
        self.net = resolve_network(network)
        self.ord_bin = shutil.which(ord_bin) or ord_bin
        # keep each network's ord index separate (ord indexes are chain-specific)
        self.index_dir = index_dir or os.path.expanduser(f"~/.bankon-ord/{self.net.name}")
        self.rpc_cookie = rpc_cookie or self.net.cookie

    # ---- availability / preflight ----
    def available(self) -> bool:
        return bool(shutil.which(self.ord_bin) or os.path.exists(self.ord_bin))

    def version(self) -> Optional[tuple]:
        if not self.available():
            return None
        try:
            out = subprocess.run([self.ord_bin, "--version"], capture_output=True, text=True, timeout=10).stdout
            nums = out.strip().split()[-1].split(".")[:3]
            return tuple(int(x) for x in nums)
        except Exception:
            return None

    def preflight(self) -> dict:
        """Report readiness without doing anything — for a UI 'can I use ordinals?' check."""
        v = self.version()
        core_ok, core_ver = _core_ok(self.net)
        return {
            "ord_installed": self.available(),
            "ord_version": ".".join(map(str, v)) if v else None,
            "ord_version_ok": bool(v and v >= MIN_ORD),
            "network": self.net.name,
            "rpc_url": self.net.rpc_url,
            "cookie_present": os.path.exists(self.rpc_cookie),
            "core_reachable": core_ok,
            "core_version": core_ver,
            "inscribe_capable": bool(core_ver and core_ver >= MIN_CORE_FOR_INSCRIBE),
            "index_dir": self.index_dir,
            "notes": _preflight_notes(v, core_ver),
        }

    # ---- command runner ----
    def _base(self) -> list[str]:
        # global ord flags: chain + Core RPC wiring + a chain-specific index location.
        os.makedirs(self.index_dir, exist_ok=True)
        args = [self.ord_bin, "--chain", self.net.ord_chain,
                "--bitcoin-rpc-url", self.net.rpc_url,
                "--index", os.path.join(self.index_dir, "index.redb"),
                "--data-dir", self.index_dir]
        if os.path.exists(self.rpc_cookie):
            args += ["--cookie-file", self.rpc_cookie]
        return args

    def _run(self, *sub, timeout: int = 120, want_json: bool = True):
        if not self.available():
            raise OrdError("`ord` is not installed — run bankon-ord/install.sh (or set ord_bin)")
        cmd = self._base() + list(sub)
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if p.returncode != 0:
            raise OrdError((p.stderr or p.stdout or f"ord exited {p.returncode}").strip()[:400])
        out = (p.stdout or "").strip()
        if not want_json:
            return out
        try:
            return json.loads(out) if out else {}
        except json.JSONDecodeError:
            return {"raw": out}

    # ---- READ operations (safe) ----
    def server_status(self):
        return self._run("--version", want_json=False)             # cheap liveness

    def wallet_balance(self, wallet: str):
        return self._run("wallet", "--name", wallet, "balance")

    def wallet_inscriptions(self, wallet: str):
        return self._run("wallet", "--name", wallet, "inscriptions")

    def wallet_outputs(self, wallet: str):
        return self._run("wallet", "--name", wallet, "outputs")

    def find_sat(self, sat: str):
        return self._run("find", str(sat))

    def inscription(self, inscription_id: str):
        return self._run("--index-sats", "list", str(inscription_id))

    def create_ordinal_wallet(self, name: str):
        """Create a dedicated ORDINAL wallet (name is enforced by the isolation guard first)."""
        from .isolation import assert_ordinal_wallet
        assert_ordinal_wallet(name)
        return self._run("wallet", "--name", name, "create")

    def receive(self, wallet: str):
        from .isolation import assert_ordinal_wallet
        assert_ordinal_wallet(wallet)
        return self._run("wallet", "--name", wallet, "receive")

    # ---- MUTATING operations (gated: isolation + not-material + approval) ----
    def inscribe_gated(self, wallet: str, file_path: str, fee_rate: float, approve, *,
                       balance_sats: Optional[int] = None, dry_run: bool = True):
        """Inscribe `file_path`. Fail-closed: ordinal-wallet only, no material funds, human approval.
        Inscribing needs Bitcoin Core >= 28. `dry_run` returns the guard decision without calling ord."""
        from .isolation import guard_mutation
        g = guard_mutation(wallet, balance_sats, approve, action="inscribe",
                           details={"file": file_path, "fee_rate": fee_rate, "network": self.net.name})
        if not g.ok:
            raise OrdError(f"inscribe blocked: {g.reason}")
        if dry_run:
            return {"gated": True, "would_run": ["wallet", "--name", wallet, "inscribe",
                                                 "--fee-rate", str(fee_rate), "--file", file_path]}
        return self._run("wallet", "--name", wallet, "inscribe", "--fee-rate", str(fee_rate),
                         "--file", file_path, timeout=600)

    def send_gated(self, wallet: str, address: str, outgoing: str, fee_rate: float, approve, *,
                   balance_sats: Optional[int] = None, dry_run: bool = True):
        """Send an inscription or rune (`outgoing` = inscription id / sat / rune amount). Gated."""
        from .isolation import guard_mutation
        g = guard_mutation(wallet, balance_sats, approve, action="send",
                           details={"to": address, "outgoing": outgoing, "fee_rate": fee_rate,
                                    "network": self.net.name})
        if not g.ok:
            raise OrdError(f"send blocked: {g.reason}")
        if dry_run:
            return {"gated": True, "would_run": ["wallet", "--name", wallet, "send",
                                                 "--fee-rate", str(fee_rate), address, outgoing]}
        return self._run("wallet", "--name", wallet, "send", "--fee-rate", str(fee_rate),
                         address, outgoing, timeout=300)


def _core_ok(net: NetConfig):
    """(reachable, version_int) via bitcoin-cli against this network — read-only."""
    btc = os.path.join(os.environ.get("BANKON_BTC_BIN", os.path.expanduser("~/bitcoin-31.0/bin")), "bitcoin-cli")
    flag = {"mainnet": [], "testnet": ["-testnet"], "signet": ["-signet"], "regtest": ["-regtest"]}[net.name]
    try:
        out = subprocess.run([btc, *flag, "-getinfo"], capture_output=True, text=True, timeout=8)
        if out.returncode != 0:
            return (False, None)
        vout = subprocess.run([btc, "-version"], capture_output=True, text=True, timeout=8).stdout
        import re
        m = re.search(r"v(\d+)\.", vout)
        return (True, int(m.group(1)) if m else None)
    except Exception:
        return (False, None)


def _preflight_notes(ordv, corev) -> list[str]:
    n = []
    if not ordv:
        n.append("ord not installed — read/inscribe unavailable until installed")
    elif ordv < MIN_ORD:
        n.append(f"ord {'.'.join(map(str, ordv))} < {'.'.join(map(str, MIN_ORD))} — upgrade recommended")
    if corev and corev < MIN_CORE_FOR_INSCRIBE:
        n.append(f"Bitcoin Core v{corev} < {MIN_CORE_FOR_INSCRIBE} — inscribing needs a newer Core (reads still fine)")
    n.append("keep ORDINAL and CARDINAL wallets separate — never spend inscription UTXOs from a normal wallet")
    return n
