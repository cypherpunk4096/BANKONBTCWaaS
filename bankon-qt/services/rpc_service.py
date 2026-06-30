"""RPC / HTTP data plumbing for BANKON Qt (MVVM service layer).

Extracted from bankon_qt.py so the view (bankon_qt.py) and the chain adapter
(adapters/bitcoin_core.py) share one data path without a circular import. Prefers the
Console's cached proxy (resilient during IBD) and falls back to the node directly.
"""
import base64, json, os, urllib.request
from datetime import datetime
from pathlib import Path

RPC_URL     = os.environ.get("BITCOIN_RPC_URL", "http://127.0.0.1:8332")
COOKIE      = os.environ.get("BITCOIN_COOKIE", str(Path.home() / ".bitcoin" / ".cookie"))
CONSOLE_URL = os.environ.get("BANKON_CONSOLE_URL", "http://127.0.0.1:8090")

_CACHE = {}   # (method, json(params)) -> (value, ts)


def _rpc_console(method, params, timeout):
    """Go through the Console's cached, resilient proxy (the same data the web UI shows).
    Returns the full reply dict {ok, result, …}; the caller decides on fallback."""
    body = json.dumps({"node": "full", "method": method, "params": params or []}).encode()
    req = urllib.request.Request(CONSOLE_URL + "/api/rpc", data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _rpc_node(method, params, timeout):
    """Direct to the node (fallback if the Console isn't running)."""
    try:
        cred = Path(COOKIE).read_text().strip()
    except Exception:
        cred = f"{os.environ.get('RPC_USER','bitcoinrpc')}:{os.environ.get('RPC_PASS','')}"
    body = json.dumps({"jsonrpc": "1.0", "id": "bankonqt", "method": method, "params": params or []}).encode()
    req = urllib.request.Request(RPC_URL, data=body, headers={
        "Content-Type": "application/json",
        "Authorization": "Basic " + base64.b64encode(cred.encode()).decode()})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        out = json.loads(r.read())
    if out.get("error"):
        raise RuntimeError(out["error"]["message"])
    return out["result"]


def rpc(method, params=None, timeout=20):
    # Prefer the Console cache (resilient + rate-limited during IBD).
    try:
        out = _rpc_console(method, params, min(timeout, 15))
    except Exception:
        # Console UNREACHABLE (down) → only then talk to the node directly.
        return _rpc_node(method, params, timeout)
    if out.get("ok"):
        return out["result"]
    # Console reached but the node is busy (ok:false). RESPECT the backpressure — do NOT hammer the
    # node directly (that bypasses the limiter and floods it). Let the caller serve stale cache.
    raise RuntimeError(out.get("error", "node busy"))


def synctip(timeout=8):
    """Actual sync (height/progress) from debug.log via the Console — no RPC needed."""
    with urllib.request.urlopen(CONSOLE_URL + "/api/synctip", timeout=timeout) as r:
        return json.loads(r.read())


def fetch_json(path, timeout=8):
    with urllib.request.urlopen(CONSOLE_URL + path, timeout=timeout) as r:
        return json.loads(r.read())


def post_json(path, payload, timeout=20):
    """POST JSON to a BANKON service (Console/WaaS) and return the parsed reply."""
    data = json.dumps(payload).encode()
    req = urllib.request.Request(CONSOLE_URL + path if path.startswith("/") and "://" not in path else path,
                                 data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def flag(iso):
    return "".join(chr(0x1F1E6 + ord(c) - 65) for c in iso) if (iso and len(iso) == 2 and iso.isalpha()) else "🏴"


def rpc_cached(method, params=None, timeout=15):
    """Return (value, stale). Serve last-known on failure so tabs aren't blank."""
    key = (method, json.dumps(params or []))
    try:
        v = rpc(method, params, timeout)
        _CACHE[key] = (v, datetime.now())
        return v, False
    except Exception:
        if key in _CACHE:
            return _CACHE[key][0], True
        raise
