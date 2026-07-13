# SPDX-License-Identifier: GPL-3.0-or-later
# bankon-vault — command-line interface. Passphrase-gated by default; getpass never echoes.
#   bankon-vault init [--path DIR]
#   bankon-vault gen-btc [--net main|test|regtest] [--id btc.seed]      # mint + store a BTC mnemonic
#   bankon-vault import-btc --id btc.seed                                # store an existing mnemonic/xprv
#   bankon-vault address --id btc.seed [--kind wpkh|tr] [--net …]
#   bankon-vault list
#   bankon-vault sign --id btc.seed --psbt <base64|-@file>               # per-sign approval; prints signed PSBT
#   bankon-vault serve [--port 8099]                                     # signing oracle (loopback)
from __future__ import annotations

import argparse
import getpass
import json
import os
import sys

from .core import BankonVault
from .overseer import PassphraseOverseer
from .chains.btc import BitcoinAdapter
from .policy import ApprovalGate, gated_sign_psbt

DEFAULT_PATH = os.environ.get("BANKON_VAULT_PATH", os.path.expanduser("~/.bankon-vault"))


def _open(path, create=False):
    v = BankonVault(path)
    salt = open(os.path.join(path, ".salt"), "rb").read()
    pp = getpass.getpass("vault passphrase: ")
    if create:
        if getpass.getpass("confirm passphrase: ") != pp:
            sys.exit("passphrases differ")
    v.unlock(PassphraseOverseer(pp, salt))
    return v


def _cli_approve(summary: dict) -> bool:
    print("\n── REVIEW & APPROVE SIGNATURE ──", file=sys.stderr)
    print(f"  network : {summary.get('network')}", file=sys.stderr)
    print(f"  inputs  : {summary.get('inputs')}  ({summary.get('in_sats')} sats)", file=sys.stderr)
    for o in summary.get("outputs", []):
        print(f"  → pay   : {o['sats']} sats  {o['address']}", file=sys.stderr)
    print(f"  fee     : {summary.get('fee_sats')} sats", file=sys.stderr)
    return input("sign this transaction? [y/N] ").strip().lower() == "y"


def main(argv=None):
    ap = argparse.ArgumentParser(prog="bankon-vault", description="chain-agnostic vault (BTC-first)")
    ap.add_argument("--path", default=DEFAULT_PATH)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")
    g = sub.add_parser("gen-btc"); g.add_argument("--net", default="main"); g.add_argument("--id", default="btc.seed")
    im = sub.add_parser("import-btc"); im.add_argument("--id", default="btc.seed")
    a = sub.add_parser("address"); a.add_argument("--id", default="btc.seed"); a.add_argument("--kind", default="wpkh"); a.add_argument("--net", default="main")
    sub.add_parser("list")
    s = sub.add_parser("sign"); s.add_argument("--id", default="btc.seed"); s.add_argument("--net", default="main"); s.add_argument("--psbt", required=True)
    sv = sub.add_parser("serve"); sv.add_argument("--port", type=int, default=8099); sv.add_argument("--id", default="btc.seed"); sv.add_argument("--net", default="main")
    pol = sub.add_parser("policy"); pol.add_argument("policy_cmd", choices=["show", "set"])
    pol.add_argument("--max-fee-sats", type=int); pol.add_argument("--max-total-out-sats", type=int)
    pol.add_argument("--cooldown-sec", type=int); pol.add_argument("--allow", action="append", default=[])
    pol.add_argument("--not-before-height", type=int); pol.add_argument("--no-approval", action="store_true")
    tb = sub.add_parser("tomb"); tb.add_argument("tomb_cmd", choices=["status", "open", "close"])
    tb.add_argument("--file", default=os.path.expanduser("~/.bankon-vault.tomb")); tb.add_argument("--mount", default=DEFAULT_PATH)
    ce = sub.add_parser("ceremony"); ce.add_argument("--threshold", type=int, default=3); ce.add_argument("--total", type=int, default=5)
    mg = sub.add_parser("migrate"); mg.add_argument("--json"); mg.add_argument("--env"); mg.add_argument("--context", default="imported")
    args = ap.parse_args(argv)

    if args.cmd == "ceremony":
        from .ceremony import genesis
        shares, manifest = genesis(threshold=args.threshold, total=args.total)
        mpath = os.path.join(args.path, "ceremony-manifest.json")
        os.makedirs(args.path, exist_ok=True)
        with open(mpath, "w") as f:
            json.dump(manifest.to_dict(), f, indent=2)
        print(f"\n⚰️  GENESIS CEREMONY — {args.threshold}-of-{args.total}. Run this AIR-GAPPED.")
        print(f"   Give ONE share to each of {args.total} operators; any {args.threshold} reconstruct the master.")
        print(f"   Manifest (public, no secret) → {mpath}\n")
        for i, sh in enumerate(shares, 1):
            print(f"   operator {i}:  {sh}")
        print("\n   Write these down, distribute, and DESTROY this screen. Unlock later with any "
              f"{args.threshold} shares.")
        return

    if args.cmd == "migrate":
        from . import migrate
        v = _open(args.path)
        try:
            if args.json:
                rep = migrate.migrate_json(args.json, v, args.context)
            elif args.env:
                rep = migrate.migrate_env(args.env, v, args.context)
            else:
                sys.exit("migrate needs --json PATH or --env PATH")
            print(json.dumps({k: rep[k] for k in ("count", "imported", "failed")}, indent=2))
        finally:
            v.lock()
        return

    if args.cmd == "policy":
        from .policy import PolicyEngine, PolicyConfig
        if args.policy_cmd == "show":
            print(json.dumps(PolicyEngine.load_config(args.path).to_dict(), indent=2)); return
        cfg = PolicyEngine.load_config(args.path)
        if args.max_fee_sats is not None: cfg.max_fee_sats = args.max_fee_sats
        if args.max_total_out_sats is not None: cfg.max_total_out_sats = args.max_total_out_sats
        if args.cooldown_sec is not None: cfg.cooldown_sec = args.cooldown_sec
        if args.not_before_height is not None: cfg.not_before_height = args.not_before_height
        if args.allow: cfg.allowlist = args.allow
        if args.no_approval: cfg.require_approval = False
        PolicyEngine(cfg, args.path).save_config()
        print("policy saved:\n" + json.dumps(cfg.to_dict(), indent=2)); return

    if args.cmd == "tomb":
        from .tomb import TombVault
        t = TombVault(args.file)
        if args.tomb_cmd == "status": print(json.dumps(t.status(), indent=2))
        elif args.tomb_cmd == "open": print("opened at", t.open(args.mount))
        elif args.tomb_cmd == "close": t.close(); print("tomb buried (closed)")
        return

    if args.cmd == "init":
        v = _open(args.path, create=True)
        print(f"vault ready at {v.path}")
        return

    if args.cmd == "gen-btc":
        v = _open(args.path)
        m = BitcoinAdapter(args.net).new_secret()
        v.store(args.id, m, context="bitcoin_wallet")
        print(f"stored a new BTC mnemonic under {args.id!r} (write it down — the vault is your backup):\n\n  {m}\n")
        v.lock(); return

    if args.cmd == "import-btc":
        v = _open(args.path)
        secret = getpass.getpass("mnemonic or xprv: ").strip()
        BitcoinAdapter("main")._root(secret)               # validate before storing
        v.store(args.id, secret, context="bitcoin_wallet")
        print(f"stored under {args.id!r}"); v.lock(); return

    if args.cmd == "address":
        v = _open(args.path)
        print(BitcoinAdapter(args.net).address(v.retrieve_str(args.id), kind=args.kind))
        v.lock(); return

    if args.cmd == "list":
        v = _open(args.path)
        for e in v.list_entries():
            print(f"  {e['id']:<28} {e['context']:<18} accessed×{e['access_count']}")
        v.lock(); return

    if args.cmd == "sign":
        v = _open(args.path)
        psbt = open(args.psbt[2:]).read().strip() if args.psbt.startswith("-@") else args.psbt
        try:
            signed = gated_sign_psbt(v, BitcoinAdapter(args.net), args.id, psbt, ApprovalGate(_cli_approve))
            print(signed)
        except PermissionError:
            sys.exit("signature denied")
        finally:
            v.lock()
        return

    if args.cmd == "serve":
        from .api import VaultOracle
        v = _open(args.path)
        VaultOracle(v, BitcoinAdapter(args.net), ApprovalGate(_cli_approve)).serve(port=args.port)


if __name__ == "__main__":
    main()
