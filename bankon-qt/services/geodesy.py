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


# Major world cities (name, country ISO, lat, lon) — compact, public-domain facts,
# enough to name the nearest populated place for any peer/point. No network calls.
CITIES = [
    ("New York", "US", 40.7128, -74.0060), ("Los Angeles", "US", 34.0522, -118.2437),
    ("Chicago", "US", 41.8781, -87.6298), ("Toronto", "CA", 43.6532, -79.3832),
    ("Mexico City", "MX", 19.4326, -99.1332), ("Bogotá", "CO", 4.7110, -74.0721),
    ("São Paulo", "BR", -23.5505, -46.6333), ("Buenos Aires", "AR", -34.6037, -58.3816),
    ("Santiago", "CL", -33.4489, -70.6693), ("Lima", "PE", -12.0464, -77.0428),
    ("London", "GB", 51.5074, -0.1278), ("Paris", "FR", 48.8566, 2.3522),
    ("Madrid", "ES", 40.4168, -3.7038), ("Amsterdam", "NL", 52.3676, 4.9041),
    ("Frankfurt", "DE", 50.1109, 8.6821), ("Berlin", "DE", 52.5200, 13.4050),
    ("Zurich", "CH", 47.3769, 8.5417), ("Milan", "IT", 45.4642, 9.1900),
    ("Stockholm", "SE", 59.3293, 18.0686), ("Helsinki", "FI", 60.1699, 24.9384),
    ("Warsaw", "PL", 52.2297, 21.0122), ("Kyiv", "UA", 50.4501, 30.5234),
    ("Moscow", "RU", 55.7558, 37.6173), ("Istanbul", "TR", 41.0082, 28.9784),
    ("Dubai", "AE", 25.2048, 55.2708), ("Tel Aviv", "IL", 32.0853, 34.7818),
    ("Cape Town", "ZA", -33.9249, 18.4241), ("Johannesburg", "ZA", -26.2041, 28.0473),
    ("Lagos", "NG", 6.5244, 3.3792), ("Nairobi", "KE", -1.2921, 36.8219),
    ("Cairo", "EG", 30.0444, 31.2357), ("Mumbai", "IN", 19.0760, 72.8777),
    ("Bengaluru", "IN", 12.9716, 77.5946), ("Singapore", "SG", 1.3521, 103.8198),
    ("Bangkok", "TH", 13.7563, 100.5018), ("Jakarta", "ID", -6.2088, 106.8456),
    ("Hong Kong", "HK", 22.3193, 114.1694), ("Shanghai", "CN", 31.2304, 121.4737),
    ("Beijing", "CN", 39.9042, 116.4074), ("Seoul", "KR", 37.5665, 126.9780),
    ("Tokyo", "JP", 35.6762, 139.6503), ("Osaka", "JP", 34.6937, 135.5023),
    ("Sydney", "AU", -33.8688, 151.2093), ("Melbourne", "AU", -37.8136, 144.9631),
    ("Auckland", "NZ", -36.8485, 174.7633), ("Honolulu", "US", 21.3069, -157.8583),
    ("Reykjavik", "IS", 64.1466, -21.9426), ("Anchorage", "US", 61.2181, -149.9003),
    ("Vancouver", "CA", 49.2827, -123.1207), ("Seattle", "US", 47.6062, -122.3321),
]


def nearest_city(lat, lon):
    """Nearest known city to (lat, lon). Returns (name, iso, distance_km)."""
    best = None
    for name, iso, clat, clon in CITIES:
        d = haversine_km(lat, lon, clat, clon)
        if best is None or d < best[2]:
            best = (name, iso, d)
    return best


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
