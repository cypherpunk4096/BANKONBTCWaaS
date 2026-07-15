"""Geodesy for the geo map — from the 3D-globe reference, applied to the 2D map.

WGS84 constants + great-circle (slerp) arc sampling. The 2D map uses plate carrée
(EPSG:4326): x = (lon+180)/360·W, y = (90-lat)/180·H. Peer links are sampled along the
great circle by spherical linear interpolation (Shoemake 1985) and projected, so long
links curve correctly instead of drawing as straight chords. Full WGS84 ellipsoidal
ECEF (for a future 3D globe backend) is included for completeness.
"""
import math

# WGS84 (NGA TR8350.2)
WGS84_A = 6378137.0
WGS84_F = 1.0 / 298.257223563
WGS84_B = WGS84_A * (1.0 - WGS84_F)
WGS84_E2 = 2.0 * WGS84_F - WGS84_F * WGS84_F   # ≈ 0.00669437999014


def geodetic_to_ecef(lat, lon, h=0.0):
    """WGS84 geodetic (deg, deg, m) → ECEF (X, Y, Z) metres. For a 3D-globe backend."""
    p, l = math.radians(lat), math.radians(lon)
    sp = math.sin(p)
    n = WGS84_A / math.sqrt(1.0 - WGS84_E2 * sp * sp)
    return ((n + h) * math.cos(p) * math.cos(l),
            (n + h) * math.cos(p) * math.sin(l),
            (n * (1.0 - WGS84_E2) + h) * sp)


def _unit(lat, lon):
    p, l = math.radians(lat), math.radians(lon)
    return (math.cos(p) * math.cos(l), math.cos(p) * math.sin(l), math.sin(p))


def great_circle_points(lat1, lon1, lat2, lon2, n=48):
    """(lat, lon) sampled along the great circle via slerp. n+1 points end to end."""
    v0, v1 = _unit(lat1, lon1), _unit(lat2, lon2)
    dot = max(-1.0, min(1.0, sum(a * b for a, b in zip(v0, v1))))
    omega = math.acos(dot)
    if omega < 1e-9:
        return [(lat1, lon1), (lat2, lon2)]
    so = math.sin(omega)
    pts = []
    for i in range(n + 1):
        t = i / n
        a = math.sin((1 - t) * omega) / so
        b = math.sin(t * omega) / so
        x, y, z = (a * v0[0] + b * v1[0], a * v0[1] + b * v1[1], a * v0[2] + b * v1[2])
        pts.append((math.degrees(math.asin(max(-1.0, min(1.0, z)))), math.degrees(math.atan2(y, x))))
    return pts


# ── Flat-Earth map: azimuthal equidistant, North-Pole aspect ──────────────────
# The canonical "flat earth" map IS the azimuthal-equidistant projection centred
# on the North Pole (the UN-emblem / AE map). It is genuinely accurate in one
# well-defined sense: distance and azimuth *from the centre* are exact — every
# point's distance from the North Pole is proportional to its colatitude, and its
# bearing from the pole is its longitude. Area/shape distort with distance (as any
# flat map of a sphere must). Here φ,λ in degrees → screen (x, y) inside a disc of
# pixel radius R centred at (cx, cy), y-down. 0° meridian points up, east is CW.
def azimuthal_equidistant(lat, lon, cx, cy, R):
    colat = 90.0 - lat                    # 0 at N pole … 180 at S pole
    rho = R * (colat / 180.0)             # equidistant in colatitude (exact along meridians)
    lam = math.radians(lon)
    x = cx + rho * math.sin(lam)
    y = cy - rho * math.cos(lam)
    return x, y


def haversine_km(lat1, lon1, lat2, lon2):
    """Great-circle distance in km on the WGS84 mean sphere (R = 6371.0088 km)."""
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


# Nearest-city lookup now lives in world_cities.py (~800 Natural Earth populated places,
# public domain) — the old hand-picked 50-city table mislabeled anywhere far from a listed
# metro. Re-exported here so existing callers keep working unchanged.
from .world_cities import CITIES, nearest_city  # noqa: F401  (compat re-export)


# ── High precision (mpmath) — 18+ decimal AE projection & geodesy measurement ──
# Rendering uses float64 (~15–16 significant digits, visually exact); this path is
# for *measurement*: the flat-earth disc coordinate / arc length of a point to an
# arbitrary number of decimals. float64 physically saturates Earth geometry (18
# decimals of a degree ≈ 1e-13 m), so mpmath is used only when real extra digits
# are asked for.
try:
    import mpmath as _mp
except Exception:  # pragma: no cover - mpmath optional
    _mp = None

PRECISION_DPS = 18


def azimuthal_equidistant_hp(lat, lon, cx, cy, R, dps=PRECISION_DPS):
    """North-Pole azimuthal-equidistant projection at ``dps`` decimal precision.
    Returns (x, y) as mpmath mpf when available, else float64. For measurement."""
    if _mp is None:
        return azimuthal_equidistant(lat, lon, cx, cy, R)
    old = _mp.mp.dps
    _mp.mp.dps = max(18, int(dps)) + 6          # guard digits
    try:
        colat = _mp.mpf(90) - _mp.mpf(str(lat))
        rho = _mp.mpf(str(R)) * colat / _mp.mpf(180)
        lam = _mp.radians(_mp.mpf(str(lon)))
        return (_mp.mpf(str(cx)) + rho * _mp.sin(lam),
                _mp.mpf(str(cy)) - rho * _mp.cos(lam))
    finally:
        _mp.mp.dps = old


def format_hp(value, decimals=PRECISION_DPS):
    """Format an mpf/float to a fixed number of decimals as a string."""
    if _mp is not None and isinstance(value, _mp.mpf):
        return _mp.nstr(value, int(decimals) + 5, strip_zeros=False)
    return f"{float(value):.{int(decimals)}f}"


def densify_latlon(poly, max_seg_km=300.0):
    """Great-circle-densify a [(lon, lat), …] ring so its edges curve correctly
    under any projection (straight lat/lon chords are wrong on a sphere). Returns
    a denser [(lon, lat), …]. This is what makes the flat-earth continents accurate."""
    if len(poly) < 2:
        return list(poly)
    out = []
    for (lo1, la1), (lo2, la2) in zip(poly, poly[1:]):
        d = haversine_km(la1, lo1, la2, lo2)
        n = max(1, int(d / max_seg_km))
        seg = great_circle_points(la1, lo1, la2, lo2, n)   # [(lat, lon), …]
        out.extend((lo, la) for (la, lo) in seg)
    return out
