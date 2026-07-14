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
    fp = sub.add_parser("pqc-falcon"); fp.add_argument("pqc_cmd", choices=["status", "demo"]); fp.add_argument("--variant", default="Falcon-512")
    pq = sub.add_parser("pqc"); pq.add_argument("pqc_cmd", choices=["status", "enroll"]); pq.add_argument("--variant", default="ML-KEM-768")
    for name in ("destroy", "uninstall"):                 # shred options mirror shred(1)
        sp = sub.add_parser(name)
        sp.add_argument("--passes", type=int, default=7,
                        help="shred -n overwrite iterations (anti-forensic overwrite DEPTH; power-of-2 "
                             "progression accepted …512,1024,2048,4096,8192). NOTE: overwrite depth is "
                             "not quantum resistance — that's the signature algorithm; see SECURITY.md.")
        sp.add_argument("--pow2", type=int, metavar="EXP",
                        help="set passes = 2**EXP (e.g. --pow2 13 = 8192); overrides --passes")
        sp.add_argument("--no-zero", action="store_true", help="omit shred -z (final zero pass)")
        sp.add_argument("--no-exact", action="store_true", help="omit shred -x (round to block)")
        sp.add_argument("--no-force", action="store_true", help="omit shred -f (chmod to write)")
        sp.add_argument("--remove-how", default="wipesync", choices=["unlink", "wipe", "wipesync"], help="shred -u=HOW")
        sp.add_argument("--yes", action="store_true")
    args = ap.parse_args(argv)

    if args.cmd in ("destroy", "uninstall"):
        from .core import BankonVault
        if not os.path.exists(os.path.join(args.path, ".salt")):
            print(f"no vault at {args.path}")
        else:
            if not args.yes:
                print(f"⚠  This SECURELY ERASES the vault at {args.path} ({args.passes}-pass shred) — "
                      "irreversible, leaves no trace.")
                if input("type ERASE to confirm: ").strip() != "ERASE":
                    sys.exit("aborted")
            passes = (1 << args.pow2) if args.pow2 is not None else args.passes
            passes = max(1, min(passes, 8192))            # cap at 8192 (2**13)
            rep = BankonVault(args.path).destroy(
                shred_passes=passes, zero=not args.no_zero, force=not args.no_force,
                exact=not args.no_exact, remove_how=args.remove_how)
            print(json.dumps(rep, indent=2))
        if args.cmd == "uninstall":
            # also remove the launcher + any user site copy of this module, leaving no trace
            for launcher in (os.path.expanduser("~/.local/bin/bankon-vault"),):
                try:
                    if os.path.exists(launcher):
                        os.remove(launcher); print(f"removed {launcher}")
                except OSError:
                    pass
            print("bankon-vault uninstalled. (The module source folder, if any, can now be deleted; "
                  "no keys, salt, or state remain.)")
        return

    if args.cmd == "ceremony":
        from .ceremony import genesis
        from .core import BankonVault
        if BankonVault.network_connected():
            print("⚠  NETWORK IS CONNECTED. Wallet/master creation should be done AIR-GAPPED — take "
                  "ICE AIRGAP up first (cut all radios), then run genesis. [Ctrl-C to abort]",
                  file=sys.stderr)
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

    if args.cmd == "pqc-falcon":
        from . import pqc_falcon
        if args.pqc_cmd == "status":
            print(json.dumps(pqc_falcon.status(), indent=2)); return
        if not pqc_falcon.available(args.variant):
            sys.exit(f"{args.variant} unavailable — {pqc_falcon.status()['note']}")
        kp = pqc_falcon.generate(args.variant)
        sig = pqc_falcon.sign(kp["secret_key"], b"BANKON quantum-native POC", args.variant)
        ok = pqc_falcon.verify(kp["public_key"], b"BANKON quantum-native POC", sig, args.variant)
        print(json.dumps({"variant": kp["variant"], "tier": kp["tier"], "public_key_bytes": len(kp["public_key"]) // 2,
                          "signature_bytes": len(sig) // 2, "verified": ok, "note": kp["note"]}, indent=2))
        return

    if args.cmd == "pqc":
        from . import pqc_hybrid, pqc_mldsa
        if args.pqc_cmd == "status":
            print(json.dumps({"hybrid_kem": pqc_hybrid.status(), "mldsa": pqc_mldsa.status()}, indent=2))
            return
        # enroll: write PUBLIC artifacts beside the vault, hand the decaps key to the operator
        if not pqc_hybrid.available():
            sys.exit(f"no ML-KEM backend — {pqc_hybrid.status()['note']}")
        info = pqc_hybrid.enroll(args.path, args.variant)
        print("\n❄  HYBRID-PQC ENROLLED — master will require classical custody AND this ML-KEM key.")
        print(f"   scheme  : {info['variant']} ({info['backend']})")
        print(f"   public  : {info['pqc_file']}")
        print(f"\n   DECAPS KEY (store OFFLINE, like a Shamir share — it is the quantum factor):")
        print(f"   {info['decaps_key']}\n")
        print("   Unlock with: HybridPQCOverseer(<inner overseer>, <decaps key>, <vault dir>).")
        print("   NOTE: enrolling does not re-key an EXISTING vault — rotate/re-init so the master "
              "actually depends on it.")
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
        if BankonVault.network_connected():
            print("⚠  NETWORK IS CONNECTED. New keys should be minted AIR-GAPPED — take ICE AIRGAP up "
                  "first, then generate. [Ctrl-C to abort]", file=sys.stderr)
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
