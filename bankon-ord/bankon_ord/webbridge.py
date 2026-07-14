# SPDX-License-Identifier: CC0-1.0
# bankon-ord — WEB BRIDGE for the BANKON Console (:8090). One JSON request on stdin → one JSON
# reply on stdout, so the Node server never links Python and the module stays isolated.
#
# CONTRACT (mirrors the CLI's safety posture, adapted to a browser):
#   • Reads are always allowed. Every reply carries timing + the exact context (extensive feedback).
#   • Mutations (inscribe/send/mint/etch) run through the SAME fail-closed guard_mutation gates
#     (ordinal wallet · no material funds · KNOWN balance · approval) and are DRY-RUN unless the
#     request carries BOTH confirm=true AND approved=true — the browser's explicit second click IS
#     the human approval, and the dry-run it saw first is echoed back in the receipt.
#   • The bridge never touches keys; ord signs via Bitcoin Core's wallet, exactly like the CLI.
"""Run:  echo '{"op":"status","net":"regtest"}' | python3 -m bankon_ord.webbridge"""
from __future__ import annotations

import json
import sys
import time
import traceback

from .isolation import IsolationError, is_ordinal_wallet
from .ord_cli import OrdCli, OrdError


def _ord(req) -> OrdCli:
    return OrdCli(req.get("net", "mainnet"), server_url=req.get("server_url") or None)


def _balance_sats(o: OrdCli, wallet: str):
    try:
        b = o.wallet_balance(wallet)
        return b.get("cardinal") if isinstance(b, dict) else None
    except Exception:
        return None                                   # unknown → guard fails closed (by design)


def _mutation(req, o: OrdCli, runner):
    """Shared two-step protocol: dry-run unless confirm+approved; receipt shows everything."""
    live = bool(req.get("confirm")) and bool(req.get("approved"))
    wallet = req["wallet"]
    bal = req.get("balance_sats", _balance_sats(o, wallet))
    # approve() records the payload and passes: on dry-run that lets the preview through; on live
    # the browser's explicit confirm click (confirm+approved) WAS the human approval for this act.
    approved_payloads = []
    result = runner(dry_run=not live, balance_sats=bal,
                    approve=lambda p: approved_payloads.append(p) or True)
    return {"mode": "LIVE BROADCAST" if live else "dry-run (nothing sent)",
            "wallet": wallet, "wallet_is_ordinal": is_ordinal_wallet(wallet),
            "balance_sats_seen": bal, "gate_payload": approved_payloads[-1] if approved_payloads else None,
            "result": result,
            "next": None if live else "re-send with confirm=true & approved=true to broadcast"}


def handle(req: dict) -> dict:
    op = req.get("op")
    o = _ord(req)
    if op == "status":
        pf = o.preflight()
        from . import __version__
        return {"preflight": pf, "module_version": __version__,
                "server_url": o.server_url or "(not set — wallet mutations need a running `ord server`)",
                "index_dir": o.index_dir,
                "guardrails": ["ordinal/cardinal wallet isolation (name-declared)",
                               "material-funds refusal ≥ 0.1 BTC", "unknown balance fails closed",
                               "mutations dry-run unless confirmed", "loopback-only explorer"]}
    if op == "wallet":
        w = req["wallet"]
        out = {"wallet": w, "is_ordinal": is_ordinal_wallet(w),
               "isolation": "🜚 ordinal — mutations allowed (gated)" if is_ordinal_wallet(w)
                            else "⛔ cardinal — mutations will be refused"}
        for key, fn in (("balance", o.wallet_balance), ("inscriptions", o.wallet_inscriptions),
                        ("outputs", o.wallet_outputs)):
            try:
                out[key] = fn(w)
            except Exception as e:                     # per-section honesty, not all-or-nothing
                out[key] = {"error": str(e)[:300]}
        return out
    if op == "inscription":
        return {"inscription": o.inscription(req["id"])}
    if op == "output":
        return {"output": o.output(req["outpoint"])}
    if op == "sat":
        return {"sat": o.find_sat(req["sat"])}
    if op == "create_wallet":
        return {"created": o.create_ordinal_wallet(req["wallet"]),
                "note": "descriptor wallet lives in Bitcoin Core; SAVE the mnemonic shown once"}
    if op == "receive":
        return {"receive": o.receive(req["wallet"])}
    if op == "inscribe":
        return _mutation(req, o, lambda **kw: o.inscribe_gated(
            req["wallet"], req["file"], float(req["fee_rate"]), kw.pop("approve"), **kw))
    if op == "send":
        return _mutation(req, o, lambda **kw: o.send_gated(
            req["wallet"], req["to"], req["outgoing"], float(req["fee_rate"]), kw.pop("approve"), **kw))
    if op == "mint":
        return _mutation(req, o, lambda **kw: o.mint_gated(
            req["wallet"], req["rune"], float(req["fee_rate"]), kw.pop("approve"), **kw))
    if op == "etch":
        return _mutation(req, o, lambda **kw: o.etch_gated(
            req["wallet"], req["rune"], float(req["fee_rate"]), kw.pop("approve"),
            divisibility=int(req.get("divisibility", 0)), supply=str(req.get("supply", "0")),
            symbol=str(req.get("symbol", "¤")), premine=str(req.get("premine", "0")), **kw))
    raise ValueError(f"unknown op {op!r}")


def main() -> int:
    t0 = time.time()
    try:
        req = json.loads(sys.stdin.read() or "{}")
        body = handle(req)
        out = {"ok": True, "op": req.get("op"), "net": req.get("net", "mainnet"), **body}
    except (OrdError, IsolationError, ValueError, KeyError) as e:
        out = {"ok": False, "error": f"{type(e).__name__}: {e}"[:600]}
    except Exception as e:
        out = {"ok": False, "error": f"{type(e).__name__}: {e}"[:300],
               "trace": traceback.format_exc(limit=3)[:800]}
    out["elapsed_ms"] = int((time.time() - t0) * 1000)
    print(json.dumps(out, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
