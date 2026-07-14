# SPDX-License-Identifier: CC0-1.0
# bankon-ord LIVE integration test — a real `ord` binary against a THROWAWAY regtest bitcoind.
#
#   • Self-skipping: exits 0 with a skip notice unless BOTH `ord` and `bitcoind` are on PATH.
#   • Fully isolated: its own datadir under /tmp (BANKON_BTC_DATADIR), default regtest ports
#     (18443/18444) — it can run BESIDE a live mainnet node (8332/8333) and never touches
#     ~/.bitcoin or the external-drive blockchain.
#   • Exercises the REAL gated path end-to-end: create ordinal wallet → fund → inscribe_gated
#     (dry_run=False) → confirm → list inscription → send_gated → confirm.
#
# Run:  python3 bankon-ord/tests/test_live_regtest.py
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

DATADIR = tempfile.mkdtemp(prefix="bankon-ord-regtest-")
os.environ["BANKON_BTC_DATADIR"] = DATADIR          # must be set BEFORE bankon_ord import
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bankon_ord import OrdCli, OrdError  # noqa: E402

ORD = shutil.which("ord")
BITCOIND = shutil.which("bitcoind") or (os.path.expanduser("/home/luvai/bitcoin-31.0/bin/bitcoind")
                                        if os.path.exists("/home/luvai/bitcoin-31.0/bin/bitcoind") else None)
BITCOIN_CLI = shutil.which("bitcoin-cli") or (BITCOIND and BITCOIND.replace("bitcoind", "bitcoin-cli"))
SERVER_PORT = 18980
WALLET = "ord-live-test"


def _cli(*args, wallet=None):
    cmd = [BITCOIN_CLI, "-regtest", f"-datadir={DATADIR}"]
    if wallet:
        cmd.append(f"-rpcwallet={wallet}")
    cmd += list(args)
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if p.returncode != 0:
        raise RuntimeError(p.stderr.strip()[:300])
    return p.stdout.strip()


def main():
    if not (ORD and BITCOIND):
        print(f"  (skipped — needs ord + bitcoind on PATH; ord={bool(ORD)} bitcoind={bool(BITCOIND)})")
        print("\n0 failures (live test skipped)")
        return 0
    server = None
    try:
        # ---- throwaway node (regtest ports; the live mainnet node is untouched) ----
        subprocess.run([BITCOIND, "-regtest", f"-datadir={DATADIR}", "-daemon", "-txindex=1",
                        "-listen=0", "-fallbackfee=0.0001"], check=True, capture_output=True)
        for _ in range(40):
            try:
                _cli("getblockcount")
                break
            except Exception:
                time.sleep(0.5)
        else:
            raise RuntimeError("regtest bitcoind did not come up")

        o = OrdCli("regtest", server_url=f"http://127.0.0.1:{SERVER_PORT}")
        pf = o.preflight()
        assert pf["ord_installed"] and pf["core_reachable"], f"preflight not ready: {pf}"

        # ---- ord server (indexes the fresh chain; loopback only) ----
        server = subprocess.Popen(
            o._base() + ["server", "--http-port", str(SERVER_PORT), "--address", "127.0.0.1"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(3)
        assert server.poll() is None, "ord server exited immediately"

        # ---- ordinal wallet (isolation-approved name) + funding ----
        o.create_ordinal_wallet(WALLET)
        addr = o.receive(WALLET)
        addr = addr.get("addresses", [addr])[0] if isinstance(addr, dict) else addr
        print(f"  ✓ wallet {WALLET!r} created, receive {str(addr)[:24]}…")
        _cli("generatetoaddress", "101", str(addr))
        time.sleep(2)                                            # let the ord index catch up
        bal = o.wallet_balance(WALLET)
        sats = bal.get("cardinal", 0) if isinstance(bal, dict) else 0
        assert sats > 0, f"wallet not funded: {bal}"
        print(f"  ✓ funded: {sats} cardinal sats")

        # ---- LIVE gated inscribe (the real path — no dry run) ----
        art = os.path.join(DATADIR, "bankon.txt")
        with open(art, "w") as f:
            f.write("BANKON ordinals live test — non-custodial, gated, isolated.")
        res = o.inscribe_gated(WALLET, art, 1, approve=lambda p: True,
                               balance_sats=sats if sats < 10_000_000 else 0, dry_run=False)
        insc = (res.get("inscriptions") or [{}])[0].get("id") if isinstance(res, dict) else None
        assert insc, f"no inscription id in {res}"
        _cli("generatetoaddress", "1", str(addr))
        time.sleep(2)
        print(f"  ✓ inscribed LIVE: {insc}")

        listed = o.wallet_inscriptions(WALLET)
        ids = [i.get("inscription") or i.get("id") for i in listed] if isinstance(listed, list) else []
        assert insc in ids, f"inscription not listed: {listed}"
        print(f"  ✓ listed in wallet ({len(ids)} inscription)")

        # ---- LIVE gated send (to our own fresh address) ----
        dest = o.receive(WALLET)
        dest = dest.get("addresses", [dest])[0] if isinstance(dest, dict) else dest
        sent = o.send_gated(WALLET, str(dest), insc, 1, approve=lambda p: True,
                            balance_sats=0, dry_run=False)
        txid = sent.get("txid") if isinstance(sent, dict) else None
        assert txid, f"no txid in {sent}"
        _cli("generatetoaddress", "1", str(addr))
        print(f"  ✓ sent LIVE: {txid}")

        # ---- gates still bite on the live path: cardinal wallet refused ----
        try:
            o.inscribe_gated("savings", art, 1, approve=lambda p: True, balance_sats=0, dry_run=False)
            raise AssertionError("cardinal wallet was allowed to inscribe LIVE")
        except OrdError:
            print("  ✓ cardinal wallet refused on the live path")

        print("\nLIVE regtest flow PASSED (create → fund → inscribe → list → send, all gated)")
        return 0
    finally:
        if server and server.poll() is None:
            server.terminate()
            try:
                server.wait(10)
            except subprocess.TimeoutExpired:
                server.kill()
        try:
            _cli("stop")
            time.sleep(2)
        except Exception:
            pass
        shutil.rmtree(DATADIR, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
