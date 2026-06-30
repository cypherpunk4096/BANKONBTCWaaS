"""Whole-network view from a SINGLE Bitcoin Core instance — no external API.

bitnodes.io is a `getaddr` crawler that gossips the reachable network; our own node
already accumulates exactly that in its addrman, exposed by `getnodeaddresses`. So we
render the whole known network self-sourced from one node (the bitnodes.io service is
unreliable/down, and this needs no third party). Each address is geolocated locally with
GeoLite2. Edges are NOT exposed by Bitcoin Core, so peer arcs (drawn elsewhere) are the
*connected* peers only — the broader set here is "known", not "connected" (disclosed).
"""
import time

from services.rpc_service import rpc
from services.geoip_service import geolocate, asn as asn_lookup

_cache = {"ts": 0, "nodes": []}
TTL = 180   # getnodeaddresses is moderately heavy; cache ~3 min


def known_nodes(limit=5000, max_age=TTL):
    """[{ip,lat,lon,iso,country,asn,org,net}] from the node's addrman. Cached; stale on error."""
    now = time.time()
    if _cache["nodes"] and now - _cache["ts"] < max_age:
        return _cache["nodes"]
    try:
        addrs = rpc("getnodeaddresses", [limit], timeout=20)
    except Exception:
        return _cache["nodes"]   # serve last-known on failure (node busy during IBD)
    out = []
    for a in addrs or []:
        net = a.get("network")
        if net in ("onion", "i2p", "cjdns"):   # no geolocation for these
            continue
        ip = a.get("address", "")
        g = geolocate(ip)
        if not g:
            continue
        an = asn_lookup(ip) or {}
        out.append({"ip": ip, "lat": g["lat"], "lon": g["lon"], "iso": g["iso"],
                    "country": g["country"], "asn": an.get("asn"), "org": an.get("org"), "net": net})
    if out:
        _cache.update(ts=now, nodes=out)
    return out


def network_asof():
    return _cache["ts"]
