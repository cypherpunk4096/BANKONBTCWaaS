# SPDX-License-Identifier: GPL-3.0-or-later
# bankon-vault — the DAIO ceremony as a service: a loopback HTTP surface where the DAIO's executives
# complete a reconstitution session, one seat at a time, from wherever they sign. Shares live in this
# process's RAM only, for one session, until it is ready, expires or is closed. Once the quorum is
# complete the vault unlocks here and signs through the same single-use, payload-bound challenge as the
# signing oracle (api.VaultOracle). No key, share or master is ever returned.
#
# Stdlib only (http.server). Bind 127.0.0.1. BANKON_VAULT_TOKEN, if set, is required as a bearer token.
"""
  POST /daio/session                {manifest, proposal, action[, payload_sha256]} → {session, message, expires}
  GET  /daio/session/<id>                                                 → status
  POST /daio/session/<id>/present   {role, envelope, enrol_signature, approval_signature} → status
  POST /daio/session/<id>/unlock                                          → {unlocked, fingerprint}
  POST /daio/session/<id>/challenge {entry_id, psbt_b64}                  → {nonce, message, expires_at}
  POST /daio/session/<id>/sign      {nonce, entry_id, psbt_b64}           → {signed_psbt}
  POST /daio/session/<id>/close                                           → {closed}
"""
from __future__ import annotations

import hmac
import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .daio import DaioError, DaioManifest, DaioOverseer, Session

MAX_BODY = 1 << 20
ACTIONS = ("unlock", "sign", "rotate")


class DaioService:
    def __init__(self, vault, adapter=None, gate=None, token: str | None = None, ttl: int = 600):
        self.vault, self.adapter, self.gate = vault, adapter, gate
        self.token = token or os.environ.get("BANKON_VAULT_TOKEN")
        self.ttl = ttl
        self.sessions: dict = {}
        self.oracles: dict = {}

    def _prune(self):
        now = time.time()
        for sid in [k for k, s in self.sessions.items() if s.expires < now]:
            self.close(sid)

    def open(self, manifest: dict, proposal: int, action: str, payload: str | None = None) -> dict:
        if action not in ACTIONS:
            raise DaioError(f"action is one of {', '.join(ACTIONS)}")
        self._prune()
        s = Session(DaioManifest.from_dict(manifest), proposal, action, ttl=self.ttl, payload=payload)
        sid = os.urandom(12).hex()
        self.sessions[sid] = s
        return {"session": sid, "message": s.message(), "expires": int(s.expires)}

    def _get(self, sid) -> Session:
        self._prune()
        s = self.sessions.get(sid)
        if not s:
            raise DaioError("no such session (or it expired)")
        return s

    def status(self, sid):
        st = self._get(sid).status()
        st["unlocked"] = sid in self.oracles
        return st

    def present(self, sid, body):
        return self._get(sid).present(body["role"], body["envelope"], body["enrol_signature"], body["approval_signature"])

    def unlock(self, sid):
        s = self._get(sid)
        if s.action not in ("unlock", "sign"):
            raise DaioError("this session was approved for rotation, not unlocking: use the CLI, air-gapped")
        self.vault.unlock(DaioOverseer(s, self.vault._salt))
        s.wipe()                                     # the shares are no longer needed in RAM
        from .api import VaultOracle
        self.oracles[sid] = VaultOracle(self.vault, self.adapter, self._gate(s), token=self.token)
        return {"unlocked": True, "fingerprint": "daio:" + s.manifest.ceremony}

    def _gate(self, s: Session):
        """The vault's own policy engine (limits, allow and deny lists, cooldown, timelocks), with the
        DAIO's quorum in the place of the person at a terminal: a 'sign' session approves exactly the
        PSBT it names; an 'unlock' session approves nothing by itself, so only what the policy lets
        through without a human approval is signed."""
        import hashlib
        from .policy_engine import PolicyConfig, PolicyEngine
        path = os.path.join(self.vault.path, ".policy.json")
        cfg = PolicyConfig.from_dict(json.load(open(path))) if os.path.exists(path) else PolicyConfig()
        if s.action == "sign":
            approver = lambda summary, want=s.payload: summary.get("_psbt_sha256") == want
        else:
            approver = lambda summary: False
        engine = PolicyEngine(cfg, self.vault.path, approver=approver)
        orig = engine.evaluate

        def evaluate(req, votes=None):               # let the approver see which PSBT this is
            req.summary["_psbt_sha256"] = hashlib.sha256(req.psbt_b64.encode()).hexdigest()
            return orig(req, votes)
        engine.evaluate = evaluate
        return engine

    def close(self, sid):
        s = self.sessions.pop(sid, None)
        if s:
            s.wipe()
        if self.oracles.pop(sid, None) is not None:
            try:
                self.vault.lock()
            except Exception:
                pass
        return {"closed": True}


def make_handler(service: DaioService):
    class H(BaseHTTPRequestHandler):
        server_version = "bankon-vault-daio"

        def log_message(self, *a):                   # no request logging: bodies carry signatures
            pass

        def _send(self, code, obj):
            data = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("content-type", "application/json")
            self.send_header("cache-control", "no-store")
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _auth(self) -> bool:
            if not service.token:
                return True
            got = self.headers.get("authorization", "")
            return hmac.compare_digest(got, f"Bearer {service.token}")

        def _body(self):
            n = int(self.headers.get("content-length") or 0)
            if n > MAX_BODY:
                raise DaioError("body too large")
            return json.loads(self.rfile.read(n) or b"{}")

        def _route(self, method):
            if not self._auth():
                return self._send(401, {"error": "unauthorised"})
            parts = [p for p in self.path.split("?")[0].split("/") if p]
            try:
                if parts[:2] != ["daio", "session"]:
                    return self._send(404, {"error": "no such route"})
                if method == "POST" and len(parts) == 2:
                    b = self._body()
                    return self._send(200, service.open(b["manifest"], int(b["proposal"]), b["action"], b.get("payload_sha256")))
                sid = parts[2] if len(parts) > 2 else ""
                if method == "GET" and len(parts) == 3:
                    return self._send(200, service.status(sid))
                verb = parts[3] if len(parts) == 4 else ""
                if method == "POST" and verb == "present":
                    return self._send(200, service.present(sid, self._body()))
                if method == "POST" and verb == "unlock":
                    return self._send(200, service.unlock(sid))
                if method == "POST" and verb in ("challenge", "sign"):
                    o = service.oracles.get(sid)
                    if not o:
                        return self._send(409, {"error": "unlock the session first"})
                    b = self._body()
                    out = o.challenge(b["entry_id"], b["psbt_b64"]) if verb == "challenge" else o.sign(b["nonce"], b["entry_id"], b["psbt_b64"])
                    if verb == "sign" and service.sessions.get(sid) and service.sessions[sid].action == "sign":
                        service.close(sid)                   # one approved transaction, then the vault locks
                    return self._send(200, out)
                if method == "POST" and verb == "close":
                    return self._send(200, service.close(sid))
                return self._send(404, {"error": "no such route"})
            except (DaioError, PermissionError, KeyError, ValueError) as e:
                return self._send(400, {"error": str(e)})
            except Exception as e:                       # the vault's own refusals (locked, wrong custody …)
                return self._send(400, {"error": f"{type(e).__name__}: {e}"})

        def do_GET(self):
            self._route("GET")

        def do_POST(self):
            self._route("POST")
    return H


def serve(service: DaioService, port: int = 8098, host: str = "127.0.0.1"):
    if host not in ("127.0.0.1", "::1", "localhost"):
        raise DaioError("the DAIO ceremony service binds loopback only")
    httpd = ThreadingHTTPServer((host, port), make_handler(service))
    print(f"bankon-vault DAIO ceremony service on http://{host}:{port}/daio/session")
    httpd.serve_forever()
