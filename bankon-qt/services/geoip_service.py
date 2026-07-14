"""GeoIP + map geometry for BANKON Qt (local GeoLite2 mmdb — no network calls).

GeoLite2-City → lat/lon/country; GeoLite2-ASN → ASN/org (for coloring nodes by network).
Shared by the Geo Map, the Net Map, and the node-native network view. Readers are opened
once (construction is expensive) and reused.
"""
import os
from pathlib import Path

_GEODIR = Path(__file__).resolve().parent.parent.parent / "geoip"   # services/ → bankon-qt/ → bankon-tools/geoip
CITY_DB = os.environ.get("BANKON_GEOIP", str(_GEODIR / "GeoLite2-City.mmdb"))
ASN_DB  = os.environ.get("BANKON_GEOIP_ASN", str(_GEODIR / "GeoLite2-ASN.mmdb"))

try:
    import maxminddb
    _city = maxminddb.open_database(CITY_DB)
except Exception:
    _city = None
try:
    import maxminddb
    _asn = maxminddb.open_database(ASN_DB)
except Exception:
    _asn = None

HAVE_GEOIP = _city is not None
HAVE_ASN = _asn is not None


def geolocate(ip):
    if not _city:
        return None
    try:
        g = _city.get(ip)
        if not g:
            return None
        loc, ctry = g.get("location", {}), g.get("country", {})
        if loc.get("latitude") is None:
            return None
        return {"lat": loc["latitude"], "lon": loc["longitude"],
                "country": ctry.get("names", {}).get("en", "?"), "iso": ctry.get("iso_code", "??")}
    except Exception:
        return None


def asn(ip):
    if not _asn:
        return None
    try:
        g = _asn.get(ip)
        if not g:
            return None
        return {"asn": g.get("autonomous_system_number"),
                "org": g.get("autonomous_system_organization")}
    except Exception:
        return None


# ACTUAL earth geography — Natural Earth 110m land polygons (public domain), vendored in
# world_geo.py: 127 coastline rings / ~3.7k points, real continent shapes AND sizes. The rough
# hand sketch below remains only as an emergency fallback if the dataset file is missing.
try:
    from .world_geo import WORLD_LAND
except Exception:                                    # pragma: no cover — dataset always ships
    WORLD_LAND = None

# Rough continent outlines (lon,lat) — a recognizable world backdrop for the geo map.
WORLD = [
  [(-168,65),(-150,70),(-120,72),(-95,70),(-82,62),(-64,60),(-52,47),(-66,44),(-80,26),(-97,18),(-110,23),(-124,40),(-140,59),(-168,65)],
  [(-81,8),(-60,10),(-50,0),(-35,-8),(-40,-23),(-55,-35),(-68,-50),(-75,-50),(-72,-30),(-78,-12),(-81,8)],
  [(-10,43),(-2,52),(8,58),(24,60),(34,60),(40,52),(28,46),(20,40),(8,38),(-4,40),(-10,43)],
  [(-17,20),(0,32),(12,34),(33,31),(43,12),(51,11),(40,-10),(35,-22),(22,-34),(15,-30),(10,-5),(-12,5),(-17,20)],
  [(40,52),(60,66),(100,72),(140,70),(165,62),(180,66),(168,50),(140,45),(122,30),(108,20),(95,12),(78,8),(68,24),(50,28),(45,40),(40,52)],
  [(114,-22),(130,-12),(142,-12),(150,-24),(153,-30),(140,-38),(128,-32),(116,-35),(114,-22)],
]
if WORLD_LAND:
    WORLD = WORLD_LAND        # real coastlines win whenever the dataset is present
