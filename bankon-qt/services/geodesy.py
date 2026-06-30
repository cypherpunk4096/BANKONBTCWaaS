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
