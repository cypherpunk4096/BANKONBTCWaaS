"""Exact-arithmetic formatting for BANKON's scientific surfaces (BTC.oracle, ICE).

Monetary values arrive as INTEGER SATOSHIS from Core (getblockstats) and stay integers;
display conversion is Decimal, never float — accuracy to 0.00000001 BTC is structural,
and derived metrics render to 0.000000000000000001 (18 dp) per the scientific-audit spec.
Expected proof-of-work is computed from the header `bits` field with pure integer math.
"""
from decimal import Decimal, getcontext, ROUND_DOWN

getcontext().prec = 60          # far above 18 dp — headroom so quantize never rounds up work

SAT_PER_BTC = 10 ** 8
_Q18 = Decimal(1).scaleb(-18)   # 0.000000000000000001
_DIM = "#5a6b7b"                # corporate grey-blue for the padded zero tail


def _group(intpart: str) -> str:
    neg = intpart.startswith("-")
    if neg:
        intpart = intpart[1:]
    return ("-" if neg else "") + f"{int(intpart):,}" if intpart else "0"


def btc18(sats) -> str:
    """Integer satoshis → exact 18-dp BTC string ('6.250000000000000000').
    The first 8 decimals are the satoshi-native resolution; the last 10 are exact zeros."""
    if sats is None:
        return "—"
    d = (Decimal(int(sats)) / SAT_PER_BTC).quantize(_Q18, rounding=ROUND_DOWN)
    ip, _, fp = format(d, "f").partition(".")
    return f"{_group(ip)}.{fp.ljust(18, '0')}"


def btc18_html(sats) -> str:
    """btc18 with the sub-satoshi zero tail dimmed (rich-text labels only)."""
    s = btc18(sats)
    if s == "—":
        return s
    head, tail = s[:-10], s[-10:]
    return f"{head}<span style='color:{_DIM}'>{tail}</span>"


def dec18(numer, denom=1, unit: str = "") -> str:
    """Exact Decimal division rendered to 18 dp — for derived metrics (tx/s, ratios, minutes)."""
    if numer is None or denom in (None, 0):
        return "—"
    d = (Decimal(str(numer)) / Decimal(str(denom))).quantize(_Q18, rounding=ROUND_DOWN)
    ip, _, fp = format(d, "f").partition(".")
    out = f"{_group(ip)}.{fp.ljust(18, '0')}"
    return f"{out} {unit}".rstrip()


def pct18(part, whole) -> str:
    """part/whole as an exact 18-dp percentage string."""
    if part is None or whole in (None, 0):
        return "—"
    return dec18(Decimal(str(part)) * 100, whole, "%")


def work_from_bits(bits: int) -> int:
    """Exact expected hashes to solve a block from the compact `bits` target.
    work = 2^256 // (target + 1) — pure integer math (Core's GetBlockProof)."""
    exp = bits >> 24
    mant = bits & 0xFFFFFF
    target = mant << (8 * (exp - 3)) if exp > 3 else mant >> (8 * (3 - exp))
    if target <= 0:
        return 0
    return (1 << 256) // (target + 1)


def chainwork_int(hex_str: str) -> int:
    """getblockheader.chainwork hex → exact cumulative-hashes integer."""
    try:
        return int(hex_str, 16)
    except (TypeError, ValueError):
        return 0


def sci_int(n: int, sig: int = 6) -> str:
    """Exact integer with a scientific-notation companion: '359,806,… (≈3.59806e+23)'.
    The integer is the truth; the ≈ form is orientation."""
    if n is None:
        return "—"
    n = int(n)
    approx = f"{Decimal(n):.{sig}e}".replace("E", "e")
    return f"{n:,}  (≈{approx})"
