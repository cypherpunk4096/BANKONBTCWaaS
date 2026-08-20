"""₿TC price via the CoinGecko FREE public API — strictly OPT-IN (the Geo Map's 🪙 toggle).

Free tier, no API key: /simple/price for the spot quote, /coins/bitcoin/market_chart for
the hourly backfill. Cadence is casual — one poll an hour — far inside the free tier's
rate limit. Nothing in this module runs unless the participant enables the toggle, and
nothing but the two GET requests below ever touches the network.
"""
import json
import time
import urllib.request

API = "https://api.coingecko.com/api/v3"
_HDRS = {"User-Agent": "BANKON-tools (local diagnostics; opt-in hourly price ticker)",
         "Accept": "application/json"}


def _get(url, timeout=15):
    req = urllib.request.Request(url, headers=_HDRS)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read(1 << 20).decode())


def spot_usd():
    """Current BTC/USD spot → {"usd": float, "at": epoch_s}."""
    d = _get(f"{API}/simple/price?ids=bitcoin&vs_currencies=usd&include_last_updated_at=true")
    b = d.get("bitcoin") or {}
    return {"usd": b.get("usd"), "at": b.get("last_updated_at") or int(time.time())}


def hourly_usd(days=1):
    """Backfill: [(epoch_s snapped ON THE HOUR, usd)] ascending. days=1 gives the free
    tier's 5-minutely series; the first sample at/after each hour boundary IS the
    on-the-hour price (≤5 min skew, marked approximate by the caller)."""
    d = _get(f"{API}/coins/bitcoin/market_chart?vs_currency=usd&days={days}")
    out, seen = [], set()
    for ms, px in d.get("prices") or []:
        hr = int(ms // 1000 // 3600) * 3600
        if hr not in seen:
            seen.add(hr)
            out.append((hr, float(px)))
    return out
