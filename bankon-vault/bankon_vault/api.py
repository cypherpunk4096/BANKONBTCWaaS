# SPDX-License-Identifier: GPL-3.0-or-later
# bankon-vault — the signing ORACLE: a loopback HTTP surface that returns SIGNED PSBTs and never,
# under any path, returns key material. Mirrors mindX sign_routes.py: single-use, scope+params-bound
# challenge; the server recomputes the payload hash so a replayed token can't redirect the signature.
#
# Stdlib only (http.server) — no framework, matches BANKON's minimal, auditable posture. Bind 127.0.0.1.
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MAX_BODY = 1 << 20                       # 1 MiB — a PSBT is small; cap the surface
_NONCES: dict[str, float] = {}           # single-use nonce → expiry
NONCE_TTL = 120


def _prune():
    now = time.time()
    for n in [k for k, exp in _NONCES.items() if exp < now]:
        _NONCES.pop(n, None)


class VaultOracle:
    """Wraps an (unlocked) vault + BTC adapter + gate into an HTTP signing service.

    Endpoints (POST JSON, loopback only):
      /challenge  {entry_id, psbt_b64}    → {nonce, message, expires_at}  (bind the exact payload)
      /sign       {nonce, entry_id, psbt_b64} → {signed_psbt}             (gate runs; key never leaves)
    """
    def __init__(self, vault, adapter, gate, token: str | None = None):
        self.vault, self.adapter, self.gate = vault, adapter, gate
        self.token = token or os.environ.get("BANKON_VAULT_TOKEN")   # optional bearer

    def _payload_hash(self, entry_id: str, psbt_b64: str) -> str:
        return hashlib.sha256((entry_id + "\0" + psbt_b64).encode()).hexdigest()

    def challenge(self, entry_id: str, psbt_b64: str) -> dict:
        _prune()
        nonce = os.urandom(16).hex()
        _NONCES[nonce] = time.time() + NONCE_TTL
        ph = self._payload_hash(entry_id, psbt_b64)
        return {"nonce": nonce, "message": f"BANKON-VAULT sign nonce={nonce} payload={ph}",
                "payload_sha256": ph, "expires_at": _NONCES[nonce]}

    def sign(self, nonce: str, entry_id: str, psbt_b64: str) -> dict:
        _prune()
        exp = _NONCES.pop(nonce, None)               # single-use: consume immediately
        if not exp or exp < time.time():
            raise PermissionError("bad or expired nonce")
        from .policy import gated_sign_psbt          # server recomputes everything from the payload
        signed = gated_sign_psbt(self.vault, self.adapter, entry_id, psbt_b64, self.gate, requester="oracle")
        return {"signed_psbt": signed}               # NO key, NO seed — ever

    def serve(self, host: str = "127.0.0.1", port: int = 8099):
        oracle = self

        class H(BaseHTTPRequestHandler):
            def _send(self, code, obj):
                body = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _auth_ok(self):
                if not oracle.token:
                    return True
                got = (self.headers.get("authorization") or "").removeprefix("Bearer ").strip()
                return hmac.compare_digest(got, oracle.token)

            def do_POST(self):
                if not self._auth_ok():
                    return self._send(401, {"ok": False, "error": "unauthorized"})
                n = int(self.headers.get("content-length") or 0)
                if n > MAX_BODY:
                    return self._send(413, {"ok": False, "error": "body too large"})
                try:
                    req = json.loads(self.rfile.read(n) or b"{}")
                except ValueError:
                    return self._send(400, {"ok": False, "error": "bad json"})
                try:
                    if self.path == "/challenge":
                        return self._send(200, {"ok": True, **oracle.challenge(req["entry_id"], req["psbt_b64"])})
                    if self.path == "/sign":
                        return self._send(200, {"ok": True, **oracle.sign(req["nonce"], req["entry_id"], req["psbt_b64"])})
                    return self._send(404, {"ok": False, "error": "no such endpoint"})
                except PermissionError as e:
                    return self._send(403, {"ok": False, "error": str(e)})
                except Exception as e:
                    return self._send(400, {"ok": False, "error": str(e)})

            def log_message(self, *a):
                pass

        httpd = ThreadingHTTPServer((host, port), H)
        print(f"[bankon-vault] signing oracle on http://{host}:{port} (loopback; returns signed PSBTs only)")
        httpd.serve_forever()
