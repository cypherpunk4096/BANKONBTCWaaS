# SPDX-License-Identifier: CC0-1.0
# bankon-ord CLI. Reads are open; mutations are gated (ordinal-wallet + no-material-funds + approval)
# and DRY-RUN by default (pass --yes to actually broadcast).
#   bankon-ord preflight --net testnet
#   bankon-ord wallet-balance --net testnet --wallet ord-test
#   bankon-ord inscriptions  --net testnet --wallet ord-test
#   bankon-ord create-wallet --net testnet --wallet ord-test
#   bankon-ord inscribe --net testnet --wallet ord-test --file art.png --fee-rate 5 [--yes]
#   bankon-ord send     --net testnet --wallet ord-test --to <addr> --outgoing <inscription_id> --fee-rate 5 [--yes]
from __future__ import annotations

import argparse
import json
import sys

from .isolation import IsolationError
from .ord_cli import OrdCli, OrdError


def _approve(payload: dict) -> bool:
    print("\n── APPROVE ORDINALS ACTION ──", file=sys.stderr)
    for k, v in payload.items():
        print(f"  {k}: {v}", file=sys.stderr)
    return input("proceed? [y/N] ").strip().lower() == "y"


def main(argv=None):
    ap = argparse.ArgumentParser(prog="bankon-ord", description="optional ordinals module (wraps ordinals/ord)")
    ap.add_argument("--net", default="mainnet", help="mainnet|testnet|signet|regtest")
    ap.add_argument("--server-url", default=None,
                    help="running `ord server` URL (modern ord wallet commands require one)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("preflight")
    for c in ("wallet-balance", "inscriptions", "outputs", "create-wallet", "receive"):
        sp = sub.add_parser(c); sp.add_argument("--wallet", required=True)
    ins = sub.add_parser("inscribe")
    ins.add_argument("--wallet", required=True); ins.add_argument("--file", required=True)
    ins.add_argument("--fee-rate", type=float, required=True); ins.add_argument("--yes", action="store_true")
    snd = sub.add_parser("send")
    snd.add_argument("--wallet", required=True); snd.add_argument("--to", required=True)
    snd.add_argument("--outgoing", required=True); snd.add_argument("--fee-rate", type=float, required=True)
    snd.add_argument("--yes", action="store_true")
    mnt = sub.add_parser("mint")
    mnt.add_argument("--wallet", required=True); mnt.add_argument("--rune", required=True)
    mnt.add_argument("--fee-rate", type=float, required=True); mnt.add_argument("--yes", action="store_true")
    et = sub.add_parser("etch")
    et.add_argument("--wallet", required=True); et.add_argument("--rune", required=True)
    et.add_argument("--fee-rate", type=float, required=True)
    et.add_argument("--divisibility", type=int, default=0); et.add_argument("--supply", default="0")
    et.add_argument("--symbol", default="¤"); et.add_argument("--premine", default="0")
    et.add_argument("--yes", action="store_true")
    args = ap.parse_args(argv)

    ord = OrdCli(args.net, server_url=args.server_url)
    try:
        if args.cmd == "preflight":
            print(json.dumps(ord.preflight(), indent=2)); return
        if args.cmd == "wallet-balance":
            print(json.dumps(ord.wallet_balance(args.wallet), indent=2)); return
        if args.cmd == "inscriptions":
            print(json.dumps(ord.wallet_inscriptions(args.wallet), indent=2)); return
        if args.cmd == "outputs":
            print(json.dumps(ord.wallet_outputs(args.wallet), indent=2)); return
        if args.cmd == "create-wallet":
            print(json.dumps(ord.create_ordinal_wallet(args.wallet), indent=2)); return
        if args.cmd == "receive":
            print(json.dumps(ord.receive(args.wallet), indent=2)); return
        if args.cmd == "inscribe":
            bal = _try_balance(ord, args.wallet)
            print(json.dumps(ord.inscribe_gated(args.wallet, args.file, args.fee_rate, _approve,
                                                balance_sats=bal, dry_run=not args.yes), indent=2)); return
        if args.cmd == "send":
            bal = _try_balance(ord, args.wallet)
            print(json.dumps(ord.send_gated(args.wallet, args.to, args.outgoing, args.fee_rate, _approve,
                                            balance_sats=bal, dry_run=not args.yes), indent=2)); return
        if args.cmd == "mint":
            bal = _try_balance(ord, args.wallet)
            print(json.dumps(ord.mint_gated(args.wallet, args.rune, args.fee_rate, _approve,
                                            balance_sats=bal, dry_run=not args.yes), indent=2)); return
        if args.cmd == "etch":
            bal = _try_balance(ord, args.wallet)
            print(json.dumps(ord.etch_gated(args.wallet, args.rune, args.fee_rate, _approve,
                                            divisibility=args.divisibility, supply=args.supply,
                                            symbol=args.symbol, premine=args.premine,
                                            balance_sats=bal, dry_run=not args.yes), indent=2)); return
    except (OrdError, IsolationError) as e:
        sys.exit(f"ord error: {e}")


def _try_balance(ord, wallet):
    try:
        b = ord.wallet_balance(wallet)
        return b.get("total") if isinstance(b, dict) else None
    except Exception:
        return None


if __name__ == "__main__":
    main()
