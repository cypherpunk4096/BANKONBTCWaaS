#!/usr/bin/env python3
"""
₿ANKON Qt — native diagnostics & node-control UI for ₿itcoin Core (PySide6).

Parity with the web Console: live tabs (Overview / Node / Network / Mempool /
₿locks / Indexes / RPC Console), a variable refresh rate (1-min default), node
recognition + Start/Stop, a live debug.log bootup stream, and last-known caching
so tabs keep showing data while the node is lock-bound during IBD.

Launch via bankon-qt.sh (installs PySide6, forces software rendering for HD 3000).
"""
import json, math, os, re, socket, subprocess, sys, time, urllib.request, webbrowser
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from PySide6 import QtWidgets, QtCore, QtGui
except ImportError:
    sys.exit("PySide6 not installed. Run: pip install --user pyside6  (or use bankon-qt.sh)")

# Data plumbing lives in the service layer (shared with adapters/, no circular import).
from services.rpc_service import (RPC_URL, COOKIE, CONSOLE_URL, rpc, rpc_cached, rpc_direct,
                                  synctip, fetch_json, post_json, flag)
from services.zmq_service import ZmqService
from services.txparse import parse_tx

BTC_BIN  = os.environ.get("BANKON_BTC_BIN", str(Path.home() / "bitcoin-31.0" / "bin"))
DATADIR  = os.environ.get("BANKON_BTC_DATADIR", str(Path.home() / ".bitcoin"))
DEBUG_LOG = Path(DATADIR) / "debug.log"
WAAS_URL = os.environ.get("BANKON_WAAS_URL", "http://127.0.0.1:8088")

# GeoIP + map geometry + node-native network view live in the service layer.
from services.geoip_service import geolocate, asn as asn_lookup, WORLD, HAVE_GEOIP, HAVE_ASN
from services.geodesy import (great_circle_points, azimuthal_equidistant, nearest_city,
                              densify_latlon, azimuthal_equidistant_hp, format_hp)
from services.world_cities import nearest_city_entry, ensure_full as cities_ensure_full, \
    dataset_stats as cities_stats
try:
    from services.world_borders import BORDERS as WORLD_BORDERS   # political overlay (NE 110m admin_0)
except Exception:                                                 # pragma: no cover
    WORLD_BORDERS = []
from services.network_view import known_nodes, network_asof
# Exact-arithmetic scientific formatting (₿TC.oracle / ICE): integer satoshis + Decimal,
# 18-decimal display, exact expected work from the compact target.
from decimal import Decimal
from services.precision import btc18, btc18_html, dec18, pct18, sci_int, work_from_bits, chainwork_int
_geo = HAVE_GEOIP   # back-compat flag used by the Geo Map tab

class Worker(QtCore.QThread):
    done = QtCore.Signal(object, bool)   # value, stale
    fail = QtCore.Signal(str)
    def __init__(self, method, params=None, timeout=15):
        super().__init__(); self.method, self.params, self.timeout = method, params, timeout
    def run(self):
        try:
            v, stale = rpc_cached(self.method, self.params, self.timeout)
            self.done.emit(v, stale)
        except Exception as e:
            self.fail.emit(str(e))

# Keep a strong reference to every running worker so Qt can't GC a live QThread
# (the cause of "QThread: Destroyed while thread is still running"). Dropped on finish.
_ACTIVE = set()
def spawn(method, on_done=None, on_fail=None, params=None, timeout=15):
    w = Worker(method, params, timeout)
    _ACTIVE.add(w)
    if on_done: w.done.connect(on_done)
    if on_fail: w.fail.connect(on_fail)
    def _fin():
        _ACTIVE.discard(w)
        w.deleteLater()          # let Qt delete it safely AFTER the thread is fully done
    w.finished.connect(_fin)
    w.start()
    return w

def scrub_memory():
    """Exit hygiene — clear ALL transient state from memory. BANKON Qt is non-custodial and
    holds no private keys by design, but we scrub any key/signature-shaped material regardless,
    empty the RPC cache completely, and drop cached datasets. Insisted on at every exit."""
    try:
        from services.rpc_service import clear_cache
        clear_cache()
    except Exception:
        pass
    # any module-level dict/list whose name hints at keys/signatures/PSBTs → overwrite then clear
    import sys as _sys
    for modname, mod in list(_sys.modules.items()):
        if not modname.startswith(("services", "adapters")) and modname != "__main__":
            continue
        for attr in list(vars(mod) if hasattr(mod, "__dict__") else []):
            low = attr.lower()
            if any(s in low for s in ("privkey", "xprv", "mnemonic", "seed", "signature", "psbt", "passphrase")):
                try:
                    v = getattr(mod, attr)
                    if isinstance(v, dict): v.clear()
                    elif isinstance(v, list): v.clear()
                    elif isinstance(v, (bytes, bytearray, str)): setattr(mod, attr, None)
                except Exception:
                    pass
    import gc as _gc; _gc.collect()


def shutdown_workers():
    # Bounded join so the X-button exit stays snappy: brief wait per worker (they're one-shot RPC
    # calls); any still in-flight are reclaimed when the process exits. ~1s worst case overall.
    for w in list(_ACTIVE):
        try: w.wait(250)
        except Exception: pass
    _ACTIVE.clear()

class FnWorker(QtCore.QThread):
    done = QtCore.Signal(object); fail = QtCore.Signal(str)
    def __init__(self, fn): super().__init__(); self.fn = fn
    def run(self):
        try: self.done.emit(self.fn())
        except Exception as e: self.fail.emit(str(e))
def spawn_fn(fn, on_done=None, on_fail=None):
    w = FnWorker(fn); _ACTIVE.add(w)
    if on_done: w.done.connect(on_done)
    if on_fail: w.fail.connect(on_fail)
    def _fin(): _ACTIVE.discard(w); w.deleteLater()
    w.finished.connect(_fin); w.start(); return w


class HoldDrag(QtCore.QObject):
    """PRESS-AND-HOLD drag-to-dock (shared): hold ~½ s on a grip widget to arm, drag, release —
    the drop zone over `zone_widget` (diagonal split: low = "bottom", rightward = "right") goes
    to on_drop. A quick click or an immediate move never arms, so child buttons keep working."""
    def __init__(self, grip, zone_widget, on_drop, on_msg=None, hold_ms=550):
        super().__init__(grip)
        self.grip, self.zonew, self.on_drop = grip, zone_widget, on_drop
        self.on_msg = on_msg or (lambda s: None)
        self.timer = QtCore.QTimer(self); self.timer.setSingleShot(True); self.timer.setInterval(hold_ms)
        self.timer.timeout.connect(self._arm)
        self.press = None; self.armed = False
        grip.setCursor(QtCore.Qt.OpenHandCursor)
        grip.installEventFilter(self)
    def _zone(self, gpos):
        p = self.zonew.mapFromGlobal(gpos)
        w, h = max(1, self.zonew.width()), max(1, self.zonew.height())
        return "bottom" if (p.y() / h) > (p.x() / w) else "right"
    def _arm(self):
        if self.press is None: return                       # released before the hold → plain click
        self.armed = True
        self.grip.setCursor(QtCore.Qt.ClosedHandCursor)
        self.on_msg("⇢ drag armed — release LOW to dock below · RIGHT to dock beside")
    def eventFilter(self, obj, ev):
        t = ev.type()
        if t == QtCore.QEvent.MouseButtonPress and ev.button() == QtCore.Qt.LeftButton:
            self.press = ev.globalPosition().toPoint(); self.armed = False; self.timer.start()
        elif t == QtCore.QEvent.MouseMove and self.press is not None:
            pos = ev.globalPosition().toPoint()
            if not self.armed:
                if (pos - self.press).manhattanLength() > 12:   # moved before the hold → not a drag
                    self.timer.stop(); self.press = None
            else:
                self.on_msg("⇢ release to dock: " + ("BOTTOM (below)" if self._zone(pos) == "bottom"
                                                     else "RIGHT (beside)"))
        elif t == QtCore.QEvent.MouseButtonRelease:
            self.timer.stop()
            if self.armed:
                z = self._zone(ev.globalPosition().toPoint())
                self.on_drop(z)
                self.on_msg("✓ docked " + ("below" if z == "bottom" else "beside (right)"))
            self.grip.setCursor(QtCore.Qt.OpenHandCursor)
            self.armed = False; self.press = None
        return super().eventFilter(obj, ev)


def anim_on(w):
    """True only when animating `w` can actually be seen. THERMAL: every animation tick must gate
    on this — under software rendering (HD 3000) a hidden/minimized 20-25 fps repaint is pure CPU
    heat. isVisible() alone is not enough: Qt keeps it True while the window is minimized."""
    try:
        return w.isVisible() and not (w.window().windowState() & QtCore.Qt.WindowMinimized)
    except Exception:
        return w.isVisible()


def link_quality(p):
    """Link QUALITY 0.25..1.0 from the peer's measured ping — one law for every map:
    ≤60 ms → 1.0 (crisp, bright), ≥600 ms → 0.25 (dim, diffuse). Unknown ping = 0.6."""
    pt = p.get("pingtime") if isinstance(p, dict) else None
    if not pt:
        return 0.6
    return max(0.25, min(1.0, 1.0 - (pt * 1000 - 60) / 720))


CANDLE = "#16C784"
def sync_color(p):
    # <51% dark; 51%→99% dark green → lighter; ≥99% candle green (held to 100%)
    if p >= 99: return CANDLE
    if p < 51: return "#0a3d27"
    t = (p - 51) / 48.0
    L = lambda a, b: int(a + (b - a) * t)
    return f"rgb({L(11,22)},{L(93,199)},{L(52,132)})"

GIB = 1073741824
_MONTH_S = 30.44 * 86400
_RUNWAY_FLOOR = 2 * GIB                     # < 2 GiB free → Core can't write (same threshold as the disk bar)
_TX_GROWTH_YR = 1.10                        # annual chain growth itself grows ~+10%/yr as tx/inscription volume rises
_RATE_CAP = 210e9 / (365.25 * 86400)        # full-blocks ceiling: 4M weight × 52,560 blocks/yr — compounding stops here
_RUNWAY_MAX_M = 480                         # projection horizon: 40 years

def runway_projection(total_bytes, avail_bytes):
    """Disk runway model for the datadir device → dict for text + chart, or None.

    The growth RATE is observed from size-on-device samples persisted across sessions
    (QSettings, ≤4 samples/day, ≥1 day span before trusting); until then a ~55 GB/yr
    full-node baseline is used and labeled as an estimate. Local samples can only see
    a linear rate — they know nothing about next year's tx volume — so the quoted
    runway COMPOUNDS that rate +10%/yr (the historical trend of the chain's annual
    growth), capped at the full-blocks ceiling: once every block is full, growth
    physically can't accelerate further. Runway ends at the 2 GiB floor where Core
    can't write, not at 0. Both curves (compounding + naive linear) are returned so
    the chart can show what ignoring tx growth would have promised."""
    if not avail_bytes or avail_bytes <= _RUNWAY_FLOOR:
        return None
    rate = None
    try:
        st = QtCore.QSettings("BANKON", "bankon-qt")
        hist = json.loads(st.value("datadir/growth", "[]"))
        now = time.time()
        if total_bytes and (not hist or now - hist[-1][0] > 21600):
            hist = (hist + [[now, int(total_bytes)]])[-64:]
            st.setValue("datadir/growth", json.dumps(hist))
        if len(hist) >= 2 and hist[-1][0] - hist[0][0] > 86400 and hist[-1][1] > hist[0][1]:
            rate = (hist[-1][1] - hist[0][1]) / (hist[-1][0] - hist[0][0])
    except Exception:
        pass
    basis = "observed" if rate else "est. 55 GB/yr"
    rate = rate or (55e9 / (365.25 * 86400))
    # compounding curve: monthly steps, mid-month rate, until the floor or the horizon.
    # The full-blocks cap NEVER projects below today's observed rate — when observation
    # exceeds the ceiling (datadir migration, reindex), hold at observed instead of
    # "predicting" a slowdown, so compounding always hits the floor before linear.
    cap = max(_RATE_CAP, rate)
    pts, free, months, capped = [(0.0, float(avail_bytes))], float(avail_bytes), None, False
    for m in range(1, _RUNWAY_MAX_M + 1):
        r = min(rate * _TX_GROWTH_YR ** ((m - 0.5) / 12.0), cap)
        nfree = free - r * _MONTH_S
        if nfree <= _RUNWAY_FLOOR:
            months = m - 1 + (free - _RUNWAY_FLOOR) / (free - nfree)
            pts.append((months, float(_RUNWAY_FLOOR))); break
        free = nfree; pts.append((float(m), free))
    else:
        months, capped = float(_RUNWAY_MAX_M), True
    # naive linear curve (what the old text quoted) — sampled too: a straight line
    # in free-space is a CURVE on the log axis
    lin_m = min((avail_bytes - _RUNWAY_FLOOR) / rate / _MONTH_S, float(_RUNWAY_MAX_M))
    n = 48
    pts_lin = [(lin_m * i / n, max(avail_bytes - rate * (lin_m * i / n) * _MONTH_S,
                                   float(_RUNWAY_FLOOR))) for i in range(n + 1)]
    return {"avail": float(avail_bytes), "rate": rate, "basis": basis, "capped": capped,
            "months": months, "months_lin": lin_m, "pts": pts, "pts_lin": pts_lin,
            "floor": float(_RUNWAY_FLOOR)}

def runway_text(proj):
    """(text, color) headline from a runway_projection — quoted from the COMPOUNDING curve."""
    if not proj:
        return None, None
    months = proj["months"]
    col = "#f85149" if months < 3 else ("#F7931A" if months < 12 else "#16C784")
    basis = f"{proj['basis']} · +10%/yr tx"
    txt = ("runway > 40 years" if proj["capped"]
           else f"runway ≈ {months:.1f} months ({basis})" if months < 24
           else f"runway ≈ {months/12:.1f} years ({basis})")
    return txt, col

def disk_runway(total_bytes, avail_bytes):
    return runway_text(runway_projection(total_bytes, avail_bytes))


_IP_RE = re.compile(r"^(\d{1,3}\.){3}\d{1,3}$|^[0-9a-fA-F:]+$")
def is_ip_literal(s):
    """True for IPv4/IPv6 literals; False for DNS names (addnode'd seed hostnames appear
    verbatim as getpeerinfo.addr and can't be geolocated without resolving)."""
    return bool(s) and bool(_IP_RE.match(s)) and (":" in s or s.count(".") == 3)


def emoji_icon(ch, px=64):
    """High-quality tab icon from a color-emoji glyph: rendered big (Noto Color Emoji),
    handed to QIcon which downscales smoothly — crisp at any tab-bar icon size, instead
    of the tiny fuzzy inline-text emoji."""
    pm = QtGui.QPixmap(px, px); pm.fill(QtCore.Qt.transparent)
    p = QtGui.QPainter(pm)
    p.setRenderHints(QtGui.QPainter.Antialiasing | QtGui.QPainter.TextAntialiasing |
                     QtGui.QPainter.SmoothPixmapTransform)
    f = QtGui.QFont("Noto Color Emoji"); f.setPixelSize(int(px * 0.82))
    p.setFont(f)
    p.drawText(pm.rect(), QtCore.Qt.AlignCenter, ch)
    p.end()
    return QtGui.QIcon(pm)


def cardgrid(fields):
    w = QtWidgets.QWidget(); form = QtWidgets.QFormLayout(w)
    labels = {}
    for k in fields:
        lab = QtWidgets.QLabel("…"); labels[k] = lab; form.addRow(k + ":", lab)
    return w, labels


class Pow2SpinBox(QtWidgets.QSpinBox):
    """Accepts ANY value, but the ▲/▼ arrows jump between powers of two (8 → 16 → 32 …).
    Type 12 if you want 12; stepping up snaps to 16, down snaps to 8."""
    def stepBy(self, steps):
        v = max(1, self.value())
        if steps > 0:
            nv = 1 << v.bit_length()                     # smallest power of two strictly greater than v
        else:
            p = 1 << (v.bit_length() - 1)                # largest power of two ≤ v
            nv = (p >> 1) if p == v else p               # strictly less than v
        self.setValue(max(self.minimum(), min(self.maximum(), max(1, nv))))


class RunwayChart(QtWidgets.QWidget):
    """Runway projection — free GiB on the datadir device vs calendar time, LOG y-axis.

    Two curves from runway_projection: the compounding model the runway figure is
    quoted from (₿ amber, +10%/yr tx growth, full-blocks cap) and the naive linear
    extrapolation (blue) for comparison. The log axis is the point: free space spans
    three decades on its way down, and a linear axis would pin the interesting last
    year against the x-axis — on log, the approach to the 2 GiB can't-write floor
    stays readable. Static paint: repaints only when a /api/filesystem tick delivers
    new data — no timers, no idle heat (HD 3000 rule)."""
    SURFACE, BORDER, GRID = "#070d14", "#14405c", "#0e3d57"
    COMP, LIN = "#D97706", "#0284C7"        # CVD-validated pair on SURFACE (dark, all 6 checks)
    INK, MUTED, FLOORC = "#c9d4e0", "#8aa0b4", "#f85149"
    def __init__(self):
        super().__init__()
        self.setFixedHeight(128)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        self.setMouseTracking(True)
        self._proj = None
        self.setVisible(False)
    def set_proj(self, proj):
        vis = bool(proj and proj["avail"] > 2 * proj["floor"])
        self._proj = proj if vis else None
        self.setVisible(vis)
        if vis and anim_on(self):
            self.update()
    # ---- shared geometry (paint + hover): x months→px, y bytes→px on log10 ----
    def _geom(self):
        p = self._proj
        r = QtCore.QRectF(46, 8, max(10.0, self.width() - 46 - 10), max(10.0, self.height() - 8 - 17))
        xmax = max(p["pts"][-1][0], p["pts_lin"][-1][0], 1.0)
        lymin, lymax = math.log10(GIB), math.log10(p["avail"] * 1.15)
        X = lambda m: r.left() + m / xmax * r.width()
        Y = lambda v: r.bottom() - (math.log10(max(v, GIB)) - lymin) / (lymax - lymin) * r.height()
        return r, xmax, X, Y
    @staticmethod
    def _at(pts, m):
        if m <= pts[0][0]: return pts[0][1]
        for (a, av), (b, bv) in zip(pts, pts[1:]):
            if m <= b: return av + (bv - av) * (m - a) / (b - a) if b > a else bv
        return pts[-1][1]
    @staticmethod
    def _when(m):
        return (datetime.now() + timedelta(days=m * 30.44)).strftime("%b %Y")
    def paintEvent(self, _ev):
        if not self._proj: return
        p = self._proj
        qp = QtGui.QPainter(self); qp.setRenderHint(QtGui.QPainter.Antialiasing)
        f = qp.font(); f.setPixelSize(9); qp.setFont(f)
        qp.setPen(QtGui.QPen(QtGui.QColor(self.BORDER), 1)); qp.setBrush(QtGui.QColor(self.SURFACE))
        qp.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 4, 4)
        r, xmax, X, Y = self._geom()
        # decade grid: 1 / 10 / 100 / 1000 GiB — recessive lines, muted right-aligned labels
        qp.setBrush(QtCore.Qt.NoBrush)
        for dec in (1, 10, 100, 1000):
            v = dec * GIB
            if not GIB <= v <= p["avail"] * 1.15: continue
            y = Y(v)
            qp.setPen(QtGui.QPen(QtGui.QColor(self.GRID), 1)); qp.drawLine(QtCore.QPointF(r.left(), y), QtCore.QPointF(r.right(), y))
            qp.setPen(QtGui.QColor(self.MUTED)); qp.drawText(QtCore.QRectF(0, y - 6, 42, 12),
                                                             QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter, f"{dec:,}")
        # opaque surface backing so text stays readable where it crosses a curve/grid line
        backing = QtGui.QColor(self.SURFACE); backing.setAlphaF(0.85)
        placed = []
        def label(text, x, y, color=self.INK):
            w = qp.fontMetrics().horizontalAdvance(text)
            rect = QtCore.QRectF(max(r.left(), min(x, r.right() - w - 4)), y - 9, w + 4, 11)
            while any(rect.intersects(o) for o in placed) and rect.top() > r.top():
                rect.translate(0, -11)
            qp.fillRect(rect, backing)
            qp.setPen(QtGui.QColor(color)); qp.drawText(QtCore.QPointF(rect.left() + 2, rect.bottom() - 2), text)
            placed.append(rect)
        label("GiB free · log scale", r.left() + 4, r.top() + 9, self.MUTED)
        # x ticks: "+Nm" while the span is short, calendar years once it isn't
        step = next((s for s in (3, 6, 12, 24, 60, 120) if xmax / s <= 7), 240)
        m = 0
        while m <= xmax:
            x = X(m)
            qp.setPen(QtGui.QPen(QtGui.QColor(self.GRID), 1)); qp.drawLine(QtCore.QPointF(x, r.bottom()), QtCore.QPointF(x, r.bottom() + 3))
            lab = "now" if m == 0 else (f"+{m}m" if step < 12 else self._when(m)[-4:])
            qp.setPen(QtGui.QColor(self.MUTED)); qp.drawText(QtCore.QRectF(x - 24, r.bottom() + 3, 48, 12), QtCore.Qt.AlignHCenter, lab)
            m += step
        # the 2 GiB floor — Core can't write below it; status red, dashed, labeled
        pen = QtGui.QPen(QtGui.QColor(self.FLOORC), 1, QtCore.Qt.DashLine); qp.setPen(pen)
        yf = Y(p["floor"]); qp.drawLine(QtCore.QPointF(r.left(), yf), QtCore.QPointF(r.right(), yf))
        label("⚠ 2 GiB — Core can't write", r.left() + 4, yf - 3, self.FLOORC)
        # curves: 2 px, compounding on top (it's the quoted model); floor-crossing dot
        # gets a 2 px surface ring so it reads over the floor line
        for pts, col, name, months in ((p["pts_lin"], self.LIN, "linear", p["months_lin"]),
                                       (p["pts"], self.COMP, "+10%/yr tx", p["months"])):
            path = QtGui.QPainterPath(QtCore.QPointF(X(pts[0][0]), Y(pts[0][1])))
            for mm, v in pts[1:]: path.lineTo(X(mm), Y(v))
            qp.setPen(QtGui.QPen(QtGui.QColor(col), 2)); qp.drawPath(path)
            ex, ey = X(pts[-1][0]), Y(pts[-1][1])
            qp.setPen(QtGui.QPen(QtGui.QColor(self.SURFACE), 2)); qp.setBrush(QtGui.QColor(col))
            qp.drawEllipse(QtCore.QPointF(ex, ey), 3.5, 3.5); qp.setBrush(QtCore.Qt.NoBrush)
            # direct label: series name + the date it hits the floor (text ink, not series
            # color); label() stacks it upward if the other series landed in the same spot
            lab = f"{name} · {'>40 y' if months >= _RUNWAY_MAX_M else self._when(months)}"
            w = qp.fontMetrics().horizontalAdvance(lab)
            label(lab, ex - w / 2, max(ey - 8, r.top() + 20))
        # legend (2 series → always present), top-right, text ink beside colored dashes
        lx = r.right() - 4
        for name, col in (("linear", self.LIN), ("+10%/yr tx", self.COMP)):
            w = qp.fontMetrics().horizontalAdvance(name)
            lx -= w; qp.setPen(QtGui.QColor(self.INK)); qp.drawText(QtCore.QPointF(lx, r.top() + 9), name)
            lx -= 14; qp.setPen(QtGui.QPen(QtGui.QColor(col), 2)); qp.drawLine(QtCore.QPointF(lx, r.top() + 6), QtCore.QPointF(lx + 10, r.top() + 6))
            lx -= 12
        qp.end()
    def mouseMoveEvent(self, ev):
        if not self._proj: return
        p = self._proj
        r, xmax, _X, _Y = self._geom()
        pos = ev.position() if hasattr(ev, "position") else QtCore.QPointF(ev.pos())
        m = max(0.0, min((pos.x() - r.left()) / r.width() * xmax, xmax))
        g = lambda v: f"{v / GIB:,.0f} GiB"
        QtWidgets.QToolTip.showText(ev.globalPosition().toPoint() if hasattr(ev, "globalPosition") else ev.globalPos(),
                                    f"{self._when(m)}\n+10%/yr tx: {g(self._at(p['pts'], m))} free\n"
                                    f"linear: {g(self._at(p['pts_lin'], m))} free", self)


class OverviewTab(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        outer = QtWidgets.QVBoxLayout(self); outer.setContentsMargins(0, 0, 0, 0)
        topw = QtWidgets.QWidget(); v = QtWidgets.QVBoxLayout(topw)
        self.bar = QtWidgets.QProgressBar(); self.bar.setMaximum(100000)
        v.addWidget(QtWidgets.QLabel("<b>Sync</b>")); v.addWidget(self.bar)
        box, self.f = cardgrid(["chain", "height", "headers", "verify %", "peers", "mempool txs",
                                "size on disk", "IBD", "CPU %", "memory %", "load / temp"])
        v.addWidget(box)
        # --- Datadir diagnostic: the disk ₿ANKON is attached to (works even when the node is down) ---
        fs = QtWidgets.QFrame(); fs.setStyleSheet("QFrame{border:1px solid #0e3d57;border-radius:6px}")
        fl = QtWidgets.QVBoxLayout(fs)
        hh = QtWidgets.QHBoxLayout()
        fh = QtWidgets.QLabel("💾 Datadir — the disk ₿ANKON is attached to"); fh.setStyleSheet("color:#F7931A;font-weight:700;border:0")
        hh.addWidget(fh, 1)
        self.fsopen = QtWidgets.QPushButton("Open folder"); self.fsopen.setObjectName("secondary")
        self.fsopen.setToolTip("Open the datadir in the file manager"); self.fsopen.clicked.connect(self._open_datadir); hh.addWidget(self.fsopen)
        fl.addLayout(hh)
        self.fspath = QtWidgets.QLabel("path: …"); self.fspath.setStyleSheet("color:#8aa0b4;font-family:monospace;font-size:10px;border:0")
        self.fspath.setWordWrap(True); self.fspath.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse); fl.addWidget(self.fspath)
        self.fsbar = QtWidgets.QProgressBar(); self.fsbar.setMaximum(1000); self.fsbar.setFormat("disk …"); fl.addWidget(self.fsbar)
        self.fscomp = QtWidgets.QLabel("measuring…"); self.fscomp.setStyleSheet("color:#c9d4e0;font-family:monospace;font-size:11px;border:0")
        self.fscomp.setWordWrap(True); fl.addWidget(self.fscomp)
        self.runchart = RunwayChart(); fl.addWidget(self.runchart)   # hidden until a projection exists
        # the ACTUAL files at the datadir path
        self.fsfiles = QtWidgets.QTreeWidget(); self.fsfiles.setColumnCount(3)
        self.fsfiles.setHeaderLabels(["file / dir", "size", "modified"]); self.fsfiles.setMaximumHeight(180)
        self.fsfiles.setRootIsDecorated(False); self.fsfiles.setAlternatingRowColors(True)
        self.fsfiles.setStyleSheet("QTreeWidget{border:1px solid #14405c;border-radius:4px;background:#05080d;"
                                   "color:#d6e3ef;font-family:monospace;font-size:11px}")
        self.fsfiles.itemDoubleClicked.connect(self._reveal_file); fl.addWidget(self.fsfiles)
        self._datadir_real = None
        v.addWidget(fs)
        # footer: dock the GTK launcher + copyright
        foot = QtWidgets.QHBoxLayout()
        self.launchbtn = QtWidgets.QPushButton("⧉ Launcher")
        self.launchbtn.setToolTip("Open the ₿ANKON launcher (start/stop Core + ₿ANKON, live logs, ICE)")
        self.launchbtn.clicked.connect(self._open_launcher); foot.addWidget(self.launchbtn)
        foot.addStretch(1)
        cpr = QtWidgets.QLabel("© 2026 ₿ANKON — all rights preserved")
        cpr.setStyleSheet("color:#5a6b7b;font-size:10px"); foot.addWidget(cpr)
        v.addLayout(foot); v.addStretch()
        # --- ₿itcoin Core log — RESIZABLE (drag the splitter divider) + verbose toggle + copy/export ---
        logw = QtWidgets.QWidget(); ll = QtWidgets.QVBoxLayout(logw); ll.setContentsMargins(0, 4, 0, 0); ll.setSpacing(3)
        lh = QtWidgets.QHBoxLayout()
        lt = QtWidgets.QLabel("<b>₿itcoin Core log</b> — debug.log")
        lt.setToolTip("live debug.log tail · drag the splitter divider to trade width with the overview")
        # compressible: without this the label's text width becomes the pane's MINIMUM width and
        # the splitter steals space from the overview to honour it
        lt.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
        lt.setStyleSheet("color:#c9d4e0"); lh.addWidget(lt, 1)
        self.dockbtn = QtWidgets.QPushButton("⤓"); self.dockbtn.setObjectName("secondary")
        self.dockbtn.setFixedWidth(30); self.dockbtn.clicked.connect(self._toggle_logdock)
        lh.addWidget(self.dockbtn)
        self.logverb = QtWidgets.QCheckBox("verbose (net)")
        self.logverb.setToolTip("₿itcoin Core's net debug category — logs every peer connect/disconnect/message.\n"
                                "Runtime `logging` RPC: instant, no restart, no config write (reverts when Core restarts).")
        self.logverb.toggled.connect(self._verb_toggle); lh.addWidget(self.logverb)
        for text, fn, tip in [("⧉ Copy", self._log_copy, "copy the visible log to the clipboard"),
                              ("⬇ Save", self._log_save, "save the visible log as a file"),
                              ("⬇ 20k", self._log_export, "export the last 20,000 debug.log lines to a file")]:
            b = QtWidgets.QPushButton(text); b.setObjectName("secondary"); b.setToolTip(tip)
            b.clicked.connect(fn); lh.addWidget(b)
        # header row = PRESS-AND-HOLD drag grip: hold ~½ s on the title, then drag and release
        # toward the bottom (dock below) or the right (dock beside). Buttons still click normally.
        hdrw = QtWidgets.QWidget(); hdrw.setLayout(lh)
        hdrw.setCursor(QtCore.Qt.OpenHandCursor)
        hdrw.setToolTip("press & HOLD the title, then drag — release LOW to dock the log at the BOTTOM,\n"
                        "release toward the RIGHT to dock it beside the overview")
        ll.addWidget(hdrw)
        self._draghdr = hdrw; hdrw.installEventFilter(self)
        self._drag = {"press": None, "armed": False, "timer": QtCore.QTimer(self)}
        self._drag["timer"].setSingleShot(True); self._drag["timer"].setInterval(550)
        self._drag["timer"].timeout.connect(self._drag_arm)
        self.logmsg = QtWidgets.QLabel(""); self.logmsg.setStyleSheet("color:#8aa0b4;font-size:11px"); ll.addWidget(self.logmsg)
        self.corelog = QtWidgets.QPlainTextEdit(); self.corelog.setReadOnly(True)
        self.corelog.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse | QtCore.Qt.TextSelectableByKeyboard)
        self.corelog.setStyleSheet("font-family:monospace;font-size:12px;background:#010409;color:#d6e3ef;"
                                   "border:2px solid #F7931A;border-radius:6px;padding:5px;"
                                   "selection-background-color:#00BFFF;selection-color:#001018;")
        ll.addWidget(self.corelog, 1)
        # The log rides to the RIGHT of the overview (horizontal splitter) so it never squeezes
        # the overview's readable height — drag the divider to trade width between the two.
        split = QtWidgets.QSplitter(QtCore.Qt.Horizontal); split.setHandleWidth(6); split.setChildrenCollapsible(False)
        split.addWidget(topw); split.addWidget(logw)
        split.setStretchFactor(0, 1); split.setStretchFactor(1, 0)
        self._ovsplit = split
        _st = QtCore.QSettings("BANKON", "bankon-qt")
        if _st.value("overview/logdock", "right") == "bottom":          # restore chosen dock side
            split.setOrientation(QtCore.Qt.Vertical)
        self._style_dockbtn()
        self._split_settled = False                                     # gate: only persist USER drags, not layout shuffles
        self._split_applied = False                                     # sizes applied on first showEvent (real geometry)
        split.splitterMoved.connect(self._persist_logsplit)
        self._logbusy = False
        outer.addWidget(split)
        self._verb_known = None                        # last known net-category state (None = unknown)
        self._logtimer = QtCore.QTimer(self); self._logtimer.timeout.connect(self._tick_log); self._logtimer.start(4000)
        QtCore.QTimer.singleShot(700, self._tick_log)
        QtCore.QTimer.singleShot(900, self._verb_load)
        # near-realtime sync: /api/synctip is a cheap debug.log tail (no node RPC), so poll it
        # every 3s while this tab is visible — the % ticks up as the node validates blocks.
        self._synctimer = QtCore.QTimer(self); self._synctimer.timeout.connect(self._tick_sync); self._synctimer.start(3000)
        self._fstimer = QtCore.QTimer(self); self._fstimer.timeout.connect(self._tick_fs); self._fstimer.start(10000)
        QtCore.QTimer.singleShot(500, self._tick_fs)
    def _tick_sync(self):
        if self.isVisible():
            spawn_fn(synctip, self._sync)
            # peers churn faster than the global refresh (default 1 min) — ride this 3s tick
            # so the card tracks getnetworkinfo.connections near-realtime (cheap, warm-cached)
            spawn("getnetworkinfo", self._n, timeout=8)
    def refresh(self):
        spawn("getblockchaininfo", self._c)
        spawn("getnetworkinfo", self._n)
        spawn("getmempoolinfo", self._m)
        spawn_fn(synctip, self._sync)            # live sync from debug.log (accurate)
    @staticmethod
    def _verified(pct):
        return pct >= 99.99995            # rounds to 100.0000 at 4 decimals → fully verified
    def _paint_sync(self, pct, stale=False):
        done = self._verified(pct)
        self.bar.setMaximum(100000000); self.bar.setValue(int(pct * 1000000))
        self.bar.setFormat("🎉 100.0000% VERIFIED ✓" if done
                           else f"{pct:.4f}%{' (cached)' if stale else ''}")
        col = "#F7931A" if done else sync_color(pct)     # 100% verified → celebrate in ₿ orange
        self.bar.setStyleSheet("QProgressBar{border:1px solid #0e3d57;border-radius:6px;text-align:center;"
                               "background:#070d14;color:#eef3f8;font-weight:%s;} "
                               "QProgressBar::chunk{background:%s;border-radius:5px;}"
                               % ("800" if done else "400", col))
        self.f["verify %"].setText("💯 100.0000 ✓" if done else f"{pct:.4f}")
    def _sync(self, st):
        p = st.get("progress")
        if p is None: return
        self._paint_sync(p * 100)
        if st.get("height"): self.f["height"].setText(f"{st['height']:,}")
    def _c(self, c, stale):
        pct = (c.get("verificationprogress", 0) or 0) * 100
        self._paint_sync(pct, stale)
        synced = (not c.get("initialblockdownload")) and pct >= 99.99
        self.f["chain"].setText(str(c.get("chain"))); self.f["height"].setText(f"{c.get('blocks',0):,}")
        self.f["headers"].setText(f"{c.get('headers',0):,}")
        self.f["size on disk"].setText(f"{c.get('size_on_disk',0)/1073741824:.1f} GB")
        self.f["IBD"].setText("● FULL NODE" if synced else "IBD (syncing)")
    def _n(self, n, stale):
        c = n.get("connections")
        o, i = n.get("connections_out"), n.get("connections_in")
        detail = f"  ({o} out · {i} in)" if (o is not None and i is not None) else ""
        # honesty: a stale value is last-known, not current — say so instead of passing it off
        self.f["peers"].setText(("—" if c is None else f"{c}{detail}") + ("   (cached)" if stale else ""))
        self.f["peers"].setToolTip(
            "Live connection count from getnetworkinfo — the authoritative number.\n"
            "The Network-log tallies count log EVENTS over a window (connects, disconnects,\n"
            "failed dials) — after an airgap toggle they exceed this without either being wrong.")
    def _m(self, m, stale):
        # mempool truth straight from getmempoolinfo — count + weight + admission floor,
        # so the landing tab states the SET, not just a bare number that invites doubt
        size = m.get("size", 0)
        txt = f"{size:,}"
        if m.get("bytes"):
            txt += f" · {m['bytes']/1e6:,.2f} MvB"
        if m.get("mempoolminfee") is not None:
            txt += f" · min {Decimal(str(m['mempoolminfee'])) * 100000:,.2f} sat/vB"
        self.f["mempool txs"].setText(txt + ("   (cached)" if stale else ""))
        self.f["mempool txs"].setToolTip(
            f"getmempoolinfo (RPC, authoritative):\n"
            f"unconfirmed txs: {size:,}\n"
            f"virtual size: {m.get('bytes', 0):,} vB\n"
            f"RAM usage: {m.get('usage', 0):,} of {m.get('maxmempool', 0):,} B\n"
            f"total fees waiting: {m.get('total_fee', 0)} ₿\n"
            f"unbroadcast: {m.get('unbroadcastcount', 0)}\n"
            f"blackICE ⛓ tx monitor counts ZMQ ARRIVALS — an event stream, not this set.")
    # ---- datadir diagnostic (external disk ₿ANKON is attached to; works with node down) ----
    def _tick_fs(self):
        if self.isVisible():
            # Console down ≠ blind: the datadir is LOCAL — measure it ourselves as fallback
            spawn_fn(lambda: fetch_json("/api/filesystem?files=1"), self._fill_fs,
                     lambda _e: spawn_fn(self._local_fs, self._fill_fs))
            spawn_fn(lambda: fetch_json("/api/system"), self._fill_sys)
    @staticmethod
    def _local_fs():
        """Local datadir measurement (same reply shape as the Console's /api/filesystem)."""
        real = os.path.realpath(DATADIR)
        st = os.statvfs(real)
        size = st.f_frsize * st.f_blocks; avail = st.f_frsize * st.f_bavail
        used = size - st.f_frsize * st.f_bfree
        comp = {}
        for name in ("blocks", "chainstate", "indexes"):
            p = os.path.join(real, name)
            tot = 0
            try:
                for root, _dirs, files in os.walk(p):
                    for f in files:
                        try: tot += os.stat(os.path.join(root, f)).st_size
                        except OSError: pass
            except OSError:
                continue
            comp[name] = tot
        comp["total"] = sum(comp.values())
        files = []
        try:
            for e in sorted(os.scandir(real), key=lambda x: x.name):
                try:
                    s = e.stat()
                    files.append({"name": e.name, "isDir": e.is_dir(),
                                  "bytes": None if e.is_dir() else s.st_size, "mtime": s.st_mtime})
                except OSError:
                    pass
        except OSError:
            pass
        return {"ok": True, "realPath": real, "datadir": DATADIR, "local": True,
                "df": {"size": size, "used": used, "avail": avail,
                       "pcent": f"{used / size * 100:.0f}%" if size else "?", "source": "statvfs (local)"},
                "components": comp, "files": files}
    def _fill_sys(self, d):
        if not d or not d.get("ok"): return
        cpu = d.get("cpuPct")
        self.f["CPU %"].setText(f"{cpu:.0f}%" if cpu is not None else "—")
        mem = d.get("memUsedPct")
        self.f["memory %"].setText(f"{mem:.0f}%  of {d.get('memTotalGB','?')} GB" if mem is not None else "—")
        load, temp = d.get("load1"), d.get("tempC")
        self.f["load / temp"].setText((f"{load:.2f}" if load is not None else "—")
                                      + (f"  ·  {temp:.0f}°C" if temp is not None else ""))
    @staticmethod
    def _gib(n): return f"{n/1073741824:.1f} GiB" if n else "—"
    def _fill_fs(self, d):
        if not d or not d.get("ok"): return
        self._datadir_real = d.get("realPath") or d.get("datadir")
        self.fspath.setText("path:  " + (self._datadir_real or "—"))
        df = d.get("df") or {}
        if df.get("size"):
            used, size, avail = df.get("used", 0), df.get("size", 1), df.get("avail", 0)
            try: pct = int(str(df.get("pcent", "0")).rstrip("%"))
            except Exception: pct = int(used / size * 100)
            self.fsbar.setValue(min(1000, pct * 10))
            full = avail < 2 * 1073741824            # < 2 GiB free → the node can't write (real "full")
            low = avail < 20 * 1073741824
            col = "#f85149" if full else ("#F7931A" if low else "#16C784")
            self.fsbar.setStyleSheet("QProgressBar{border:1px solid #0e3d57;border-radius:5px;text-align:center;background:#070d14;color:#eef3f8}"
                                     "QProgressBar::chunk{background:%s;border-radius:4px}" % col)
            self.fsbar.setFormat(f"disk {df.get('pcent','?')} · {self._gib(avail)} free of {self._gib(size)}"
                                 + ("  ⚠ FULL — ₿itcoin Core can't write" if full else (" — low" if low else "")))
        c = d.get("components") or {}
        proj = runway_projection(c.get("total"), (d.get("df") or {}).get("avail"))
        rw, rwcol = runway_text(proj)
        self.runchart.set_proj(proj)
        self.fscomp.setText(f"blocks {self._gib(c.get('blocks'))}  ·  indexes {self._gib(c.get('indexes'))}  ·  "
                            f"chainstate {self._gib(c.get('chainstate'))}  ·  total on device {self._gib(c.get('total'))}"
                            + (f"  ·  <span style='color:{rwcol};font-weight:700'>{rw}</span>" if rw else ""))
        files = d.get("files")
        if files is not None:
            self.fsfiles.clear()
            for fi in files:
                sz = self._gib(fi.get("bytes")) if (fi.get("bytes") or 0) >= 1073741824 else \
                     (f"{fi['bytes']/1048576:.0f} MiB" if fi.get("bytes") else ("dir" if fi.get("isDir") else "—"))
                when = datetime.fromtimestamp(fi["mtime"], timezone.utc).strftime("%Y-%m-%d %H:%M") if fi.get("mtime") else ""
                it = QtWidgets.QTreeWidgetItem([("📁 " if fi.get("isDir") else "📄 ") + fi["name"], sz, when])
                if fi.get("isDir"): it.setForeground(0, QtGui.QColor("#00BFFF"))
                self.fsfiles.addTopLevelItem(it)
            self.fsfiles.resizeColumnToContents(0)
    def _open_datadir(self):
        p = self._datadir_real
        if not p: return
        import shutil as _sh
        opener = _sh.which("xdg-open") or _sh.which("nautilus") or _sh.which("nemo") or _sh.which("thunar")
        if opener: subprocess.Popen([opener, p], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    def _reveal_file(self, item, col):
        if not self._datadir_real: return
        # the name originates from an HTTP response — sanitise so a compromised service can't make
        # us open an arbitrary path (basename strips ../ and absolute prefixes; dot-names rejected;
        # NOTE: no realpath containment here — datadir components like chainstate are symlinks out
        # of the datadir by design)
        name = os.path.basename(item.text(0).split(" ", 1)[-1])
        if name in ("", ".", ".."): return
        target = os.path.join(self._datadir_real, name)
        import shutil as _sh
        opener = _sh.which("xdg-open")
        if opener: subprocess.Popen([opener, target],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    def _open_launcher(self):
        launcher = os.path.expanduser("~/bankon-tools/bankon-launcher.py")
        if os.path.exists(launcher):
            subprocess.Popen(["python3", launcher], start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # ---- ₿itcoin Core log widget — tail, verbose(net) toggle, copy/export ----
    def showEvent(self, e):
        super().showEvent(e)
        if not self._split_applied:            # first show = geometry is real; earlier setSizes gets recomputed away
            self._split_applied = True
            QtCore.QTimer.singleShot(50, self._apply_logsplit)
    def _logsplit_key(self):
        return "overview/logsplit-h2" if self._ovsplit.orientation() == QtCore.Qt.Horizontal \
            else "overview/logsplit-v"
    def _apply_logsplit(self):
        sp = self._ovsplit; horiz = sp.orientation() == QtCore.Qt.Horizontal
        saved = QtCore.QSettings("BANKON", "bankon-qt").value(self._logsplit_key())
        applied = False
        try:
            if saved: sp.setSizes([int(x) for x in saved]); applied = True
        except Exception: pass
        if not applied:                        # defaults: right dock 62/38 width · bottom dock 70/30 height
            tot = max(sp.width() if horiz else sp.height(), 600)
            r = 0.62 if horiz else 0.70
            sp.setSizes([int(tot * r), int(tot * (1 - r))])
        QtCore.QTimer.singleShot(1500, lambda: setattr(self, "_split_settled", True))
    def _persist_logsplit(self, *_):
        if self._split_settled:
            QtCore.QSettings("BANKON", "bankon-qt").setValue(self._logsplit_key(), self._ovsplit.sizes())
    def _style_dockbtn(self):
        horiz = self._ovsplit.orientation() == QtCore.Qt.Horizontal
        self.dockbtn.setText("⤓" if horiz else "⇥")
        self.dockbtn.setToolTip("dock the log to the BOTTOM (below the overview)" if horiz
                                else "dock the log back to the RIGHT (beside the overview)")
    def _toggle_logdock(self):
        self._set_logdock("bottom" if self._ovsplit.orientation() == QtCore.Qt.Horizontal else "right")
    def _set_logdock(self, side):
        self._ovsplit.setOrientation(QtCore.Qt.Vertical if side == "bottom" else QtCore.Qt.Horizontal)
        QtCore.QSettings("BANKON", "bankon-qt").setValue("overview/logdock", side)
        self._style_dockbtn(); self._apply_logsplit()
    # ---- press-and-hold drag-to-dock (header grip) ----
    def eventFilter(self, obj, ev):
        if obj is getattr(self, "_draghdr", None):
            t = ev.type()
            if t == QtCore.QEvent.MouseButtonPress and ev.button() == QtCore.Qt.LeftButton:
                self._drag["press"] = ev.globalPosition().toPoint()
                self._drag["armed"] = False
                self._drag["timer"].start()
            elif t == QtCore.QEvent.MouseMove and self._drag["press"] is not None:
                pos = ev.globalPosition().toPoint()
                if not self._drag["armed"]:
                    if (pos - self._drag["press"]).manhattanLength() > 12:   # moved before the hold → not a drag
                        self._drag["timer"].stop(); self._drag["press"] = None
                else:
                    self.logmsg.setText("⇢ release to dock: "
                                        + ("BOTTOM (below the overview)" if self._drag_zone(pos) == "bottom"
                                           else "RIGHT (beside the overview)"))
            elif t == QtCore.QEvent.MouseButtonRelease:
                self._drag["timer"].stop()
                if self._drag["armed"]:
                    zone = self._drag_zone(ev.globalPosition().toPoint())
                    self._set_logdock(zone)
                    self.logmsg.setText("✓ log docked " + ("below the overview" if zone == "bottom"
                                                           else "beside the overview (right)"))
                self._draghdr.setCursor(QtCore.Qt.OpenHandCursor)
                self._drag["armed"] = False; self._drag["press"] = None
        return super().eventFilter(obj, ev)
    def _drag_arm(self):
        if self._drag["press"] is None: return               # button already released → plain click, no drag
        self._drag["armed"] = True
        self._draghdr.setCursor(QtCore.Qt.ClosedHandCursor)
        self.logmsg.setText("⇢ drag armed — release LOW to dock the log at the bottom · "
                            "release RIGHT to dock it beside the overview")
    def _drag_zone(self, gpos):
        p = self.mapFromGlobal(gpos)
        w, h = max(1, self.width()), max(1, self.height())
        return "bottom" if (p.y() / h) > (p.x() / w) else "right"   # diagonal split: down = bottom, right = beside
    def _tick_log(self):
        # THREADED (spawn_fn): a synchronous tail on the GUI thread stalls the whole app whenever
        # the datadir disk is busy — the log read must never block clicks/tab switches.
        if not self.isVisible() or self._logbusy or self.corelog.textCursor().hasSelection(): return
        if not DEBUG_LOG.exists(): self.corelog.setPlainText("no debug.log yet"); return
        self._logbusy = True
        def _tail():
            return subprocess.run(["tail", "-n", "250", str(DEBUG_LOG)],
                                  capture_output=True, text=True, timeout=8).stdout
        def _fill(out):
            self._logbusy = False
            if self.corelog.textCursor().hasSelection(): return
            sb = self.corelog.verticalScrollBar(); atBottom = sb.value() >= sb.maximum() - 4
            self.corelog.setPlainText(out)
            if atBottom: sb.setValue(sb.maximum())
        def _err(e):
            self._logbusy = False; self.corelog.setPlainText(f"log error: {e}")
        spawn_fn(_tail, _fill, _err)
    def _verb_load(self):
        """Reflect the node's ACTUAL current net-category state (console first, direct-RPC fallback)."""
        def _get():
            try: return fetch_json("/api/log/verbosity")
            except Exception: return {"ok": True, "categories": rpc_direct("logging")}
        def _got(r):
            if not (r and r.get("ok")): return
            on = bool((r.get("categories") or {}).get("net"))
            self._verb_known = on
            self.logverb.blockSignals(True); self.logverb.setChecked(on); self.logverb.blockSignals(False)
        spawn_fn(_get, _got, lambda _e: None)
    def _verb_toggle(self, on):
        inc, exc = (["net"], []) if on else ([], ["net"])
        def _set():
            try: return post_json("/api/log/verbosity", {"include": inc, "exclude": exc})
            except Exception: return {"ok": True, "categories": rpc_direct("logging", [inc, exc])}
        def _done(r):
            if r and r.get("ok"):
                self._verb_known = bool((r.get("categories") or {}).get("net"))
                self.logmsg.setText("✓ verbose (net) " + ("ON — full per-peer detail" if self._verb_known
                                                          else "off — default verbosity"))
            else:
                self._verb_fail(str((r or {}).get("error", "failed")))
        spawn_fn(_set, _done, self._verb_fail)
    def _verb_fail(self, err):
        self.logmsg.setText(f"✗ verbosity: {err}")     # honest revert — the checkbox never lies about node state
        self.logverb.blockSignals(True); self.logverb.setChecked(bool(self._verb_known)); self.logverb.blockSignals(False)
    def _log_copy(self):
        txt = self.corelog.toPlainText()
        QtWidgets.QApplication.clipboard().setText(txt)
        self.logmsg.setText(f"✓ copied {len(txt.splitlines()):,} lines to the clipboard")
    def _log_save(self):
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Save log view",
            str(Path.home() / f"bankon-debug-view-{datetime.now().strftime('%Y-%m-%d-%H%M%S')}.log"),
            "Log files (*.log *.txt)")
        if not path: return
        try: Path(path).write_text(self.corelog.toPlainText()); self.logmsg.setText(f"✓ saved {path}")
        except Exception as e: self.logmsg.setText(f"✗ save failed: {e}")
    def _log_export(self):
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Export last 20,000 log lines",
            str(Path.home() / f"bankon-debug-{datetime.now().strftime('%Y-%m-%d')}-last20000.log"),
            "Log files (*.log *.txt)")
        if not path: return
        def _dump():
            out = subprocess.run(["tail", "-n", "20000", str(DEBUG_LOG)],
                                 capture_output=True, text=True, timeout=30).stdout
            Path(path).write_text(out); return len(out.splitlines())
        spawn_fn(_dump, lambda n: self.logmsg.setText(f"✓ exported {n:,} lines → {path}"),
                 lambda e: self.logmsg.setText(f"✗ export failed: {e}"))


class NodeTab(QtWidgets.QWidget):
    def __init__(self):
        super().__init__(); v = QtWidgets.QVBoxLayout(self)
        # Top (status + controls) and the log live in a vertical splitter you can drag.
        top = QtWidgets.QWidget(); tl = QtWidgets.QVBoxLayout(top); tl.setContentsMargins(0, 0, 0, 0)
        box, self.f = cardgrid(["state", "height", "headers", "sync %"]); tl.addWidget(box)
        row = QtWidgets.QHBoxLayout()
        self.start = QtWidgets.QPushButton("▶ Start node"); self.stop = QtWidgets.QPushButton("■ Stop node")
        self.stop.setObjectName("danger")
        self.start.clicked.connect(self.do_start); self.stop.clicked.connect(self.do_stop)
        row.addWidget(self.start); row.addWidget(self.stop); row.addStretch(); tl.addLayout(row)
        self.msg = QtWidgets.QLabel(""); self.msg.setStyleSheet("color:#8aa0b4"); tl.addWidget(self.msg)
        tl.addWidget(QtWidgets.QLabel("<b>Live boot / sync log</b> (debug.log) — drag the divider to resize"))
        self.log = QtWidgets.QPlainTextEdit(); self.log.setReadOnly(True)
        self.log.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse | QtCore.Qt.TextSelectableByKeyboard)
        self.log.setStyleSheet("font-family:monospace;font-size:12px;background:#010409;color:#d6e3ef;"
                               "border:2px solid #F7931A;border-radius:6px;padding:5px;"
                               "selection-background-color:#00BFFF;selection-color:#001018;")
        split = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        split.setHandleWidth(6); split.setChildrenCollapsible(False)
        split.addWidget(top); split.addWidget(self.log)
        split.setStretchFactor(0, 0); split.setStretchFactor(1, 1); split.setSizes([180, 420])
        v.addWidget(split)
    def refresh(self):
        spawn("getblockcount", self._ok, self._busy, timeout=6)
        self.load_log()
    def _ok(self, blocks, stale):
        self.f["state"].setText("● running" + (" (cached)" if stale else "")); self.f["height"].setText(f"{blocks:,}")
        self.start.setEnabled(False); self.stop.setEnabled(True)
        spawn("getblockchaininfo", self._chain, timeout=6)
    def _chain(self, c, stale):
        self.f["headers"].setText(f"{c.get('headers',0):,}")
        self.f["sync %"].setText(f"{(c.get('verificationprogress',0) or 0)*100:.2f}%")
    def _busy(self, err):
        state = "● booting…" if ("-28" in err or "warm" in err.lower() or "loading" in err.lower()) \
            else "● stopped" if ("refus" in err.lower() or "connect" in err.lower()) else "● validating (busy)"
        self.f["state"].setText(state)
        self.start.setEnabled("stopped" in state); self.stop.setEnabled("stopped" not in state)
    def load_log(self):
        if self.log.textCursor().hasSelection(): return   # don't clobber a selection being copied
        if not DEBUG_LOG.exists():
            self.log.setPlainText("no debug.log yet"); return
        try:
            out = subprocess.run(["tail", "-n", "120", str(DEBUG_LOG)], capture_output=True, text=True, timeout=5).stdout
            atBottom = self.log.verticalScrollBar().value() >= self.log.verticalScrollBar().maximum() - 4
            self.log.setPlainText(out)
            if atBottom: self.log.verticalScrollBar().setValue(self.log.verticalScrollBar().maximum())
        except Exception as e:
            self.log.setPlainText(f"log error: {e}")
    def do_start(self):
        self.msg.setText("starting bitcoind…")
        try:
            subprocess.Popen([str(Path(BTC_BIN)/"bitcoind"), f"-datadir={DATADIR}", "-daemon"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            self.msg.setText("bitcoind launching — watch the log below")
        except Exception as e: self.msg.setText(f"start failed: {e}")
    def do_stop(self):
        if QtWidgets.QMessageBox.question(self, "Stop", "Stop ₿itcoin Core?") != QtWidgets.QMessageBox.Yes: return
        try:
            r = subprocess.run([str(Path(BTC_BIN)/"bitcoin-cli"), f"-datadir={DATADIR}", "stop"],
                               capture_output=True, text=True, timeout=15)
            self.msg.setText(r.stdout.strip() or r.stderr.strip() or "stopping")
        except Exception as e: self.msg.setText(f"stop failed: {e}")


class LogsTab(QtWidgets.QWidget):
    """₿itcoin Core logs — live debug.log tail with filters, WHOLE-FILE search, runtime
    verbosity (Core's `logging` RPC — no restart, no config write), copy/export. The
    correlation strip quotes BOTH peer truths: the live getpeerinfo set (actual, in/out —
    same truth as Overview) vs log-window EVENTS. All log reads are local + read-only."""
    FILTERS = [("all lines", None),
               ("peers / net", re.compile(r"peer|connect|disconnect|socket|addrman|dns|Bound to|AddLocal", re.I)),
               ("blocks (UpdateTip)", re.compile(r"UpdateTip|new best|Saw new", re.I)),
               ("warnings + errors", re.compile(r"warning|error|corrupt|fatal", re.I))]
    def __init__(self):
        super().__init__(); v = QtWidgets.QVBoxLayout(self)
        self.corr = QtWidgets.QLabel("peer correlation loading…")
        self.corr.setStyleSheet("color:#c9d4e0;border:1px solid #5a3a0a;border-radius:6px;padding:6px;background:#0e1116")
        self.corr.setWordWrap(True); v.addWidget(self.corr)
        row = QtWidgets.QHBoxLayout()
        self.q = QtWidgets.QLineEdit(); self.q.setPlaceholderText("search the WHOLE debug.log (not just the tail)…")
        self.q.returnPressed.connect(self.do_search); row.addWidget(self.q, 1)
        self.regex = QtWidgets.QCheckBox("regex"); self.regex.setToolTip("POSIX extended regex"); row.addWidget(self.regex)
        sb = QtWidgets.QPushButton("🔍 Search"); sb.clicked.connect(self.do_search); row.addWidget(sb)
        lb = QtWidgets.QPushButton("● Live"); lb.setToolTip("resume the auto-refreshing live tail")
        lb.clicked.connect(self.go_live); row.addWidget(lb)
        self.lines = QtWidgets.QComboBox()
        for x in ("200", "400", "1000", "3000", "5000"): self.lines.addItem(x)
        self.lines.setCurrentText("400"); self.lines.setToolTip("tail length")
        self.lines.currentIndexChanged.connect(lambda *_: self._tick(force=True)); row.addWidget(self.lines)
        self.filter = QtWidgets.QComboBox()
        for name, _rx in self.FILTERS: self.filter.addItem(name)
        self.filter.setToolTip("filter the live tail")
        self.filter.currentIndexChanged.connect(lambda *_: self._tick(force=True)); row.addWidget(self.filter)
        for text, fn, tip in [("⧉ Copy", self._copy, "copy the visible log to the clipboard"),
                              ("⬇ Save", self._save, "save the visible log as a file"),
                              ("⬇ 20k", self._export, "export the last 20,000 debug.log lines to a file")]:
            b = QtWidgets.QPushButton(text); b.setObjectName("secondary"); b.setToolTip(tip)
            b.clicked.connect(fn); row.addWidget(b)
        v.addLayout(row)
        self.verb = Collapsible("Verbosity — debug categories (runtime `logging` RPC · no restart, no config write)",
                                self._build_verb)
        v.addWidget(self.verb)
        self.stat = QtWidgets.QLabel("—"); self.stat.setStyleSheet("color:#8aa0b4"); v.addWidget(self.stat)
        self.pane = QtWidgets.QPlainTextEdit(); self.pane.setReadOnly(True)
        self.pane.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse | QtCore.Qt.TextSelectableByKeyboard)
        self.pane.setStyleSheet("font-family:monospace;font-size:12px;background:#010409;color:#d6e3ef;"
                                "border:2px solid #F7931A;border-radius:6px;padding:5px;"
                                "selection-background-color:#00BFFF;selection-color:#001018;")
        v.addWidget(self.pane, 1)
        self.mode = "live"; self._checks = {}; self._searching = False; self._corrtick = 0
        self._tailbusy = False; self._corrbusy = False       # never stack workers on a slow disk
        self.t = QtCore.QTimer(self); self.t.timeout.connect(self._tick); self.t.start(3000)
    def refresh(self):
        self._tick(force=True); self._corr()
    # ---- live tail (filtered) ----
    def _tick(self, force=False):
        if not self.isVisible() and not force: return
        self._corrtick += 1
        if self._corrtick % 2 == 1: self._corr()
        if self.mode != "live" or self._tailbusy: return
        if self.pane.textCursor().hasSelection(): return   # don't clobber a selection being copied
        if not DEBUG_LOG.exists(): self.pane.setPlainText("no debug.log yet"); return
        self._tailbusy = True
        n = self.lines.currentText()
        flt = dict(self.FILTERS).get(self.filter.currentText())
        def _tail():
            out = subprocess.run(["tail", "-n", n, str(DEBUG_LOG)],
                                 capture_output=True, text=True, timeout=8).stdout
            lines = out.splitlines(); total = len(lines)
            if flt: lines = [l for l in lines if flt.search(l)]
            return total, lines
        def _err(e):
            self._tailbusy = False; self.pane.setPlainText(f"log error: {e}")
        spawn_fn(_tail, self._tail_fill, _err)
    def _tail_fill(self, r):
        self._tailbusy = False
        if self.mode != "live" or self.pane.textCursor().hasSelection(): return
        total, lines = r
        sbar = self.pane.verticalScrollBar(); atBottom = sbar.value() >= sbar.maximum() - 4
        self.pane.setPlainText("\n".join(lines))
        if atBottom: sbar.setValue(sbar.maximum())
        shown = len(lines)
        of = f" of {total:,}" if shown != total else ""
        self.stat.setText(f"● live tail · {shown:,}{of} lines · updated {datetime.now().strftime('%H:%M:%S')}")
    # ---- whole-file search (local grep, list-args — no shell) ----
    def do_search(self):
        q = self.q.text().strip()
        if not q: return self.go_live()
        if self._searching: return
        self._searching = True; self.mode = "search"
        mode = "-E" if self.regex.isChecked() else "-F"
        self.stat.setText("searching the whole debug.log…")
        def _run():
            c = subprocess.run(["grep", "-a", "-i", mode, "-c", "--", q, str(DEBUG_LOG)],
                               capture_output=True, text=True, timeout=30).stdout.strip()
            p1 = subprocess.Popen(["grep", "-a", "-i", mode, "--", q, str(DEBUG_LOG)], stdout=subprocess.PIPE)
            out = subprocess.run(["tail", "-n", "800"], stdin=p1.stdout,
                                 capture_output=True, text=True, timeout=30).stdout
            p1.stdout.close(); p1.wait(timeout=5)
            return int(c or 0), out
        def _done(r):
            self._searching = False
            total, out = r
            self.pane.setPlainText(out or "(no matches)")
            self.pane.verticalScrollBar().setValue(self.pane.verticalScrollBar().maximum())
            self.stat.setText(f"🔍 {total:,} matching lines in debug.log · showing last {len(out.splitlines()):,}"
                              " · tail paused — ● Live to resume")
        def _fail(e):
            self._searching = False; self.stat.setText(f"✗ search failed: {e}")
        spawn_fn(_run, _done, _fail)
    def go_live(self):
        self.mode = "live"; self.q.clear(); self._tick(force=True)
    # ---- correlation strip: live set vs log events, both labeled ----
    def _corr(self):
        if self._corrbusy: return
        self._corrbusy = True
        def _get():
            out = {}
            try: out["pl"] = fetch_json("/api/peers/live")
            except Exception: out["pl"] = None
            try: out["na"] = fetch_json("/api/netactivity?n=60", timeout=10)
            except Exception: out["na"] = None
            return out
        def _fail(_e): self._corrbusy = False
        spawn_fn(_get, self._corr_fill, _fail)
    def _corr_fill(self, d):
        self._corrbusy = False
        pl, na = d.get("pl"), d.get("na")
        if pl and pl.get("ok") and pl.get("total") is not None:
            age = "" if pl.get("live") else \
                f" · last known {max(0, int(time.time() - (pl.get('asOf') or 0) / 1000))}s ago (RPC busy)"
            live = (f"<b style='color:#16C784'>● {pl['total']} peers live now</b> — "
                    f"<b>{pl.get('out','?')} out · {pl.get('in','?')} in</b> "
                    f"<span style='color:#8aa0b4'>(getpeerinfo{age} — the ACTUAL current set, same truth as Overview)</span>")
        else:
            live = "<span style='color:#e3b341'>● live peer set unavailable (console/RPC busy)</span>"
        ty = (na or {}).get("tally") or {}
        self.corr.setText(live + "<br><span style='color:#8aa0b4'>log window (EVENTS over time, not current peers): "
                          f"✓ {ty.get('connected',0)} connects · ⇣ {ty.get('inbound',0)} inbound · "
                          f"⟲ {ty.get('disconnect',0)} drops · ✗ {ty.get('failed',0)} failed dials — "
                          "enable the <b>net</b> category for full per-peer detail</span>")
    # ---- verbosity (Core `logging` RPC via console, direct-RPC fallback) ----
    def _build_verb(self, layout, placeholder):
        placeholder.setText("loading categories…")
        note = QtWidgets.QLabel("Tick a category and Apply — ₿itcoin Core writes MORE detail to debug.log instantly "
                                "(runtime <code>logging</code> RPC; reverts when Core restarts). <b>net</b> = every "
                                "peer connect / disconnect / message. More categories = faster-growing log.")
        note.setStyleSheet("color:#8aa0b4"); note.setWordWrap(True); layout.addWidget(note)
        gw = QtWidgets.QWidget(); self.catgrid = QtWidgets.QGridLayout(gw)
        self.catgrid.setContentsMargins(0, 2, 0, 2); layout.addWidget(gw)
        rowb = QtWidgets.QHBoxLayout()
        ap = QtWidgets.QPushButton("Apply verbosity"); ap.clicked.connect(lambda: self._verb_apply(False)); rowb.addWidget(ap)
        off = QtWidgets.QPushButton("all off"); off.clicked.connect(lambda: self._verb_apply(True)); rowb.addWidget(off)
        self.vmsg = QtWidgets.QLabel(""); self.vmsg.setStyleSheet("color:#8aa0b4"); rowb.addWidget(self.vmsg, 1)
        layout.addLayout(rowb)
        def _get():
            try: return fetch_json("/api/log/verbosity")
            except Exception: return {"ok": True, "categories": rpc_direct("logging")}
        def _got(r):
            placeholder.hide()
            if not (r and r.get("ok")):
                self.vmsg.setText("✗ categories unavailable (node busy?)"); return
            for i, (k, on) in enumerate(sorted((r.get("categories") or {}).items())):
                cb = QtWidgets.QCheckBox(k); cb.setChecked(bool(on))
                if k == "net": cb.setStyleSheet("color:#F7931A;font-weight:700")
                self._checks[k] = cb; self.catgrid.addWidget(cb, i // 5, i % 5)
        spawn_fn(_get, _got, lambda e: placeholder.setText(f"✗ {e}"))
    def _verb_apply(self, all_off=False):
        if not self._checks: return
        inc = [] if all_off else [k for k, cb in self._checks.items() if cb.isChecked()]
        exc = list(self._checks) if all_off else [k for k, cb in self._checks.items() if not cb.isChecked()]
        self.vmsg.setText("applying…")
        def _set():
            try: return post_json("/api/log/verbosity", {"include": inc, "exclude": exc})
            except Exception: return {"ok": True, "categories": rpc_direct("logging", [inc, exc])}
        def _done(r):
            if not (r and r.get("ok")):
                self.vmsg.setText(f"✗ {(r or {}).get('error', 'failed')}"); return
            cats = r.get("categories") or {}
            for k, cb in self._checks.items():
                cb.blockSignals(True); cb.setChecked(bool(cats.get(k))); cb.blockSignals(False)
            on = [k for k, x in cats.items() if x]
            self.vmsg.setText("✓ on: " + (", ".join(on) if on else "none (default verbosity)"))
        spawn_fn(_set, _done, lambda e: self.vmsg.setText(f"✗ {e}"))
    # ---- copy / export ----
    def _copy(self):
        txt = self.pane.toPlainText()
        QtWidgets.QApplication.clipboard().setText(txt)
        self.stat.setText(f"✓ copied {len(txt.splitlines()):,} lines to the clipboard")
    def _save(self):
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Save log view",
            str(Path.home() / f"bankon-debug-view-{datetime.now().strftime('%Y-%m-%d-%H%M%S')}.log"),
            "Log files (*.log *.txt)")
        if not path: return
        try: Path(path).write_text(self.pane.toPlainText()); self.stat.setText(f"✓ saved {path}")
        except Exception as e: self.stat.setText(f"✗ save failed: {e}")
    def _export(self):
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Export last 20,000 log lines",
            str(Path.home() / f"bankon-debug-{datetime.now().strftime('%Y-%m-%d')}-last20000.log"),
            "Log files (*.log *.txt)")
        if not path: return
        def _dump():
            out = subprocess.run(["tail", "-n", "20000", str(DEBUG_LOG)],
                                 capture_output=True, text=True, timeout=30).stdout
            Path(path).write_text(out); return len(out.splitlines())
        spawn_fn(_dump, lambda n: self.stat.setText(f"✓ exported {n:,} lines → {path}"),
                 lambda e: self.stat.setText(f"✗ export failed: {e}"))


class TableTab(QtWidgets.QWidget):
    def __init__(self, headers, method, rowfn):
        super().__init__(); self.method, self.rowfn = method, rowfn; self._lastrows = []
        v = QtWidgets.QVBoxLayout(self)
        top = QtWidgets.QHBoxLayout(); top.addStretch()
        top.addWidget(QtWidgets.QLabel("max rows"))
        self.maxrows = QtWidgets.QSpinBox(); self.maxrows.setRange(1, 100000); self.maxrows.setSingleStep(50)
        self.maxrows.setValue(1000); self.maxrows.setToolTip("Max rows to display (+/- or type)")
        self.maxrows.valueChanged.connect(lambda _: self._render()); top.addWidget(self.maxrows)
        v.addLayout(top)
        self.t = QtWidgets.QTableWidget(); self.t.setColumnCount(len(headers))
        self.t.setHorizontalHeaderLabels(headers)
        hh = self.t.horizontalHeader()
        hh.setSectionResizeMode(QtWidgets.QHeaderView.Interactive)  # drag column edges to resize
        hh.setStretchLastSection(True)                              # last column fills remaining width
        hh.setSectionsMovable(True)                                 # drag-and-drop to reorder columns
        hh.setMinimumSectionSize(70); hh.setHighlightSections(False)
        self.t.verticalHeader().setVisible(False)                  # no row-number column
        self.t.verticalHeader().setDefaultSectionSize(28)          # taller rows = wider spacing
        self.t.setShowGrid(False); self.t.setAlternatingRowColors(True)
        self.t.setSortingEnabled(True)                             # click headers to sort
        self.t.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.t.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        v.addWidget(self.t)
    def refresh(self):
        spawn(self.method, self._fill)
    def _fill(self, data, stale):
        self._lastrows = self.rowfn(data); self._render()
    def _render(self):
        rows = self._lastrows[:self.maxrows.value()]    # cap to the max-rows spinbox
        self.t.setSortingEnabled(False)                 # don't reorder mid-fill
        self.t.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, val in enumerate(row):
                self.t.setItem(r, c, QtWidgets.QTableWidgetItem(str(val)))
        self.t.resizeColumnsToContents()                # sensible initial widths (still drag-resizable)
        self.t.setSortingEnabled(True)


class CardsTab(QtWidgets.QWidget):
    def __init__(self, fields, fillfn, methods):
        super().__init__(); self.fillfn, self.methods = fillfn, methods
        v = QtWidgets.QVBoxLayout(self); box, self.f = cardgrid(fields); v.addWidget(box); v.addStretch()
    def refresh(self):
        for m in self.methods:
            spawn(m, lambda d, s, mm=m: self.fillfn(self.f, mm, d, s))


class BlocksTab(QtWidgets.QWidget):
    """Live chain tip + recent blocks (/api/recentblocks). Full best hash shown; choose how
    many blocks to show; double-click a block for full detail (getblock)."""
    def __init__(self):
        super().__init__(); v = QtWidgets.QVBoxLayout(self)
        box, self.f = cardgrid(["chain tip", "tip time", "difficulty", "size on disk", "avg block time"]); v.addWidget(box)
        self.besthash = QtWidgets.QLabel("best hash:  —")           # full hash, never truncated
        self.besthash.setStyleSheet("color:#F7931A;font-family:monospace;font-size:11px")
        self.besthash.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse); self.besthash.setWordWrap(True)
        v.addWidget(self.besthash)
        top = QtWidgets.QHBoxLayout()
        self.lbl = QtWidgets.QLabel("Latest blocks — double-click a block for full detail")
        self.lbl.setStyleSheet("color:#8aa0b4"); top.addWidget(self.lbl, 1)
        top.addWidget(QtWidgets.QLabel("show"))
        self.count = QtWidgets.QSpinBox(); self.count.setRange(1, 200); self.count.setValue(100)
        self.count.setToolTip("How many recent blocks to show (+/- or type)")
        self.count.valueChanged.connect(lambda _: self.refresh()); top.addWidget(self.count)
        v.addLayout(top)
        self.t = QtWidgets.QTableWidget(); self.t.setColumnCount(4); self.t.setHorizontalHeaderLabels(["height", "hash", "time UTC", "txs"])
        hh = self.t.horizontalHeader(); hh.setStretchLastSection(False)
        hh.setSectionsMovable(True); hh.setMinimumSectionSize(60)
        # height / time / txs hug their content; hash absorbs the leftover width (so txs isn't a wide empty column)
        hh.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
        hh.setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeToContents)
        self.t.setTextElideMode(QtCore.Qt.ElideRight)
        self.t.verticalHeader().setVisible(False); self.t.verticalHeader().setDefaultSectionSize(28)
        self.t.setShowGrid(False); self.t.setAlternatingRowColors(True); self.t.setSortingEnabled(True)
        self.t.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.t.cellDoubleClicked.connect(self._open_detail)
        v.addWidget(self.t, 1)
        # stay current to ₿itcoin Core: poll the accumulating feed every 5s while visible
        self._lt = QtCore.QTimer(self); self._lt.timeout.connect(self._tick); self._lt.start(5000)
    def _tick(self):
        if self.isVisible(): self.refresh()
    def refresh(self):
        spawn("getblockchaininfo", self._tip, timeout=8)
        n = self.count.value()
        spawn_fn(lambda: fetch_json(f"/api/recentblocks?n={n}").get("blocks", []), self._blocks)
        spawn_fn(lambda: fetch_json("/api/oracle").get("oracle", {}), self._oracle)
    def _tip(self, c, stale):
        self.f["chain tip"].setText(f"{c.get('blocks',0):,}")
        self.besthash.setText("best hash:  " + (c.get('bestblockhash', '') or '—'))
        mt = c.get("mediantime"); self.f["tip time"].setText(datetime.fromtimestamp(mt, timezone.utc).strftime("%Y-%m-%d %H:%M") if mt else "—")
        d = c.get("difficulty"); self.f["difficulty"].setText(f"{d:.3e}" if d else "—")
        self.f["size on disk"].setText(f"{c.get('size_on_disk',0)/1073741824:.1f} GB")
    def _blocks(self, rb):
        self.t.setSortingEnabled(False); self.t.setRowCount(len(rb))
        for r, b in enumerate(rb):
            t = b.get("time"); tm = datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d %H:%M:%S") if t else "—"
            h = b.get('hash', ''); nt = b.get('nTx')
            cells = [f"{b.get('height',0):,}", (h or "—"), tm, (f"{nt:,}" if isinstance(nt, int) else "—")]
            for c, val in enumerate(cells):
                it = QtWidgets.QTableWidgetItem(val)
                if c == 0: it.setData(QtCore.Qt.UserRole, h)        # stash full hash for the detail view
                if c == 3: it.setTextAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)   # numbers right-aligned
                self.t.setItem(r, c, it)
        self.t.setSortingEnabled(True)   # column widths handled by header resize modes
        self.lbl.setText(f"Latest blocks ({len(rb)}) — double-click a block for full detail" if rb
                         else "Latest blocks — accumulating (node validating; tip above is live)")
    def _oracle(self, oc):
        a = oc.get("avgBlockTimeAllTime"); self.f["avg block time"].setText(f"{a/60:.2f} min" if a else "—")
    def _open_detail(self, row, col):
        it = self.t.item(row, 0); h = it.data(QtCore.Qt.UserRole) if it else None
        if not h: return
        self.lbl.setText(f"Loading block {h[:12]}…")
        spawn("getblock", self._show_detail, params=[h, 1], timeout=20)
    def _show_detail(self, b, stale):
        if not isinstance(b, dict): return
        order = ["height", "hash", "confirmations", "time", "mediantime", "nTx", "size", "strippedsize",
                 "weight", "version", "versionHex", "merkleroot", "bits", "difficulty", "nonce",
                 "chainwork", "previousblockhash", "nextblockhash"]
        lines = []
        for k in order:
            if k in b and not isinstance(b[k], list):
                val = b[k]
                if k in ("time", "mediantime"):
                    val = f"{val}  ({datetime.fromtimestamp(val, timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC)"
                lines.append(f"{k:18}: {val}")
        d = QtWidgets.QDialog(self); d.setWindowTitle(f"₿lock {b.get('height','')}"); d.resize(640, 470)
        dl = QtWidgets.QVBoxLayout(d)
        te = QtWidgets.QPlainTextEdit(); te.setReadOnly(True); te.setPlainText("\n".join(lines))
        te.setStyleSheet("font-family:monospace;font-size:12px;background:#070d14;color:#d6e3ef")
        dl.addWidget(te)
        cl = QtWidgets.QPushButton("Close"); cl.clicked.connect(d.accept); dl.addWidget(cl)
        self.lbl.setText("Latest blocks — double-click a block for full detail")
        d.exec()


class IndexesTab(QtWidgets.QWidget):
    """Live index view — every index (txindex, coinstatsindex, blockfilter…) advancing toward the
    chain tip, updated in near-realtime like the ₿locks feed. Tip comes from the cheap log-based
    synctip (no cs_main), so it stays live during IBD."""
    def __init__(self):
        super().__init__(); v = QtWidgets.QVBoxLayout(self)
        self.lbl = QtWidgets.QLabel("<b>Index quality</b> — live"); v.addWidget(self.lbl)
        self.bar = QtWidgets.QProgressBar(); self.bar.setMaximum(100000); v.addWidget(self.bar)
        self.crunch = QtWidgets.QLabel("⚙ idle"); v.addWidget(self.crunch)
        self.crunch.setStyleSheet("color:#00BFFF;font-weight:800;font-family:monospace;font-size:13px")
        self.t = QtWidgets.QTableWidget(); self.t.setColumnCount(8)
        self.t.setHorizontalHeaderLabels(["index", "height", "behind", "% indexed", "rate (blk/min)", "ETA", "size on disk", "status"])
        hh = self.t.horizontalHeader(); hh.setSectionResizeMode(QtWidgets.QHeaderView.Interactive); hh.setStretchLastSection(True)
        hh.setMinimumSectionSize(70); self.t.verticalHeader().setVisible(False)
        self.t.verticalHeader().setDefaultSectionSize(28); self.t.setShowGrid(False)
        self.t.setAlternatingRowColors(True); self.t.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        v.addWidget(self.t)
        self.activity = QtWidgets.QLabel("activity: —"); self.activity.setStyleSheet("color:#16C784;font-weight:600"); v.addWidget(self.activity)
        self.detail = QtWidgets.QLabel("sync: —"); self.detail.setStyleSheet("color:#c9d4e0;font-family:monospace;font-size:12px")
        self.detail.setWordWrap(True); v.addWidget(self.detail)
        self.note = QtWidgets.QLabel("Indexes build in the background during IBD and catch up to the chain tip.")
        self.note.setStyleSheet("color:#8B949E"); self.note.setWordWrap(True); v.addWidget(self.note)
        # --- txindex deep-dive: look up ANY transaction by txid (what txindex is FOR) ---
        dd = QtWidgets.QFrame(); dd.setStyleSheet("QFrame{border:1px solid #F7931A;border-radius:6px}")
        ddl = QtWidgets.QVBoxLayout(dd)
        ddh = QtWidgets.QHBoxLayout()
        ddt = QtWidgets.QLabel("🔍 txindex lookup — deep-dive any transaction by txid")
        ddt.setStyleSheet("color:#F7931A;font-weight:700;border:0"); ddh.addWidget(ddt, 1)
        self.txin = QtWidgets.QLineEdit(); self.txin.setPlaceholderText("paste a txid (64 hex chars)…")
        self.txin.setStyleSheet("border:1px solid #2e4a63;border-radius:4px;padding:3px;font-family:monospace")
        self.txin.returnPressed.connect(self._deep_dive); ddh.addWidget(self.txin, 2)
        self.txbtn = QtWidgets.QPushButton("Deep dive"); self.txbtn.clicked.connect(self._deep_dive); ddh.addWidget(self.txbtn)
        ddl.addLayout(ddh)
        # up/down count + recent-transaction history (last N, up to 100), newest first, click to dive
        hrow = QtWidgets.QHBoxLayout()
        self.txhint = QtWidgets.QLabel("Latest transactions (newest first) — click one to deep-dive:")
        self.txhint.setStyleSheet("color:#8B949E;border:0"); self.txhint.setWordWrap(True); hrow.addWidget(self.txhint, 1)
        hrow.addWidget(QtWidgets.QLabel("show"))
        self.txcount = QtWidgets.QSpinBox(); self.txcount.setRange(1, 100); self.txcount.setValue(20)
        self.txcount.setToolTip("How many recent transactions to list (1–100)")
        self.txcount.valueChanged.connect(lambda _: self._load_recent(force=True)); hrow.addWidget(self.txcount)
        ddl.addLayout(hrow)
        self.txhist = QtWidgets.QListWidget(); self.txhist.setMaximumHeight(150)
        self.txhist.setStyleSheet("QListWidget{border:1px solid #2e4a63;border-radius:4px;background:#05080d;"
                                  "color:#c9d4e0;font-family:monospace;font-size:11px}"
                                  "QListWidget::item:hover{background:#14202e;color:#FFD37A}")
        self.txhist.itemClicked.connect(self._hist_click); ddl.addWidget(self.txhist)
        self._recent_tip = -1                                   # -1 = never loaded → first refresh populates it
        v.addWidget(dd)
        # --- Export: share index-derived data with other nodes ---
        exp = QtWidgets.QFrame(); exp.setStyleSheet("QFrame{border:1px solid #2e4a63;border-radius:6px}")
        el = QtWidgets.QVBoxLayout(exp)
        exhdr = QtWidgets.QHBoxLayout()
        eh = QtWidgets.QLabel("⬇ Export for other nodes"); eh.setStyleSheet("color:#F7931A;font-weight:700;border:0")
        exhdr.addWidget(eh, 1)
        self.exbtn = QtWidgets.QPushButton("Export UTXO snapshot")
        self.exbtn.setToolTip("dumptxoutset (latest) → a portable UTXO snapshot another node loads with\n"
                              "loadtxoutset for fast assumeUTXO sync. Writes ~5–11 GB; takes several minutes.")
        self.exbtn.clicked.connect(self._export_utxo); exhdr.addWidget(self.exbtn)
        el.addLayout(exhdr)
        self.exnote = QtWidgets.QLabel(
            "The raw indexes (txindex, etc.) are <b>node-local</b> — txindex maps each txid to a byte offset "
            "in <i>this</i> node's block files, so it can't be loaded on another node. The portable, shareable "
            "artifact is a <b>UTXO snapshot</b>: another node bootstraps from it with <tt>loadtxoutset</tt> "
            "(assumeUTXO) and validates down to genesis in the background.")
        self.exnote.setStyleSheet("color:#8B949E;border:0"); self.exnote.setWordWrap(True); el.addWidget(self.exnote)
        self.exstatus = QtWidgets.QLabel(""); self.exstatus.setStyleSheet("color:#c9d4e0;font-family:monospace;font-size:11px;border:0")
        self.exstatus.setWordWrap(True); self.exstatus.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        el.addWidget(self.exstatus)
        v.addWidget(exp)
        # --- RAGEbtc: export the whole chain → pgvectorscale (fire-and-poll) ---
        cx = QtWidgets.QFrame(); cx.setStyleSheet("QFrame{border:1px solid #9945FF;border-radius:6px}")
        cxl = QtWidgets.QVBoxLayout(cx)
        cxh = QtWidgets.QHBoxLayout()
        ch = QtWidgets.QLabel("⛓ Export chain → pgvectorscale (RAGEbtc)")
        ch.setStyleSheet("color:#9945FF;font-weight:700;border:0"); cxh.addWidget(ch, 1)
        self.chexbtn = QtWidgets.QPushButton("Start export"); self.chexbtn.clicked.connect(self._chain_start); cxh.addWidget(self.chexbtn)
        self.chstopbtn = QtWidgets.QPushButton("Stop"); self.chstopbtn.setObjectName("secondary")
        self.chstopbtn.clicked.connect(self._chain_stop); cxh.addWidget(self.chstopbtn)
        cxl.addLayout(cxh)
        self.chbar = QtWidgets.QProgressBar(); self.chbar.setMaximum(100000); self.chbar.setFormat("idle"); cxl.addWidget(self.chbar)
        self.chstatus = QtWidgets.QLabel("Walks every block+tx via rageRPC into a searchable DB. Set DATABASE_URL "
                                         "to write (unset = dry-run); resumable by height. See the RAGE skill.")
        self.chstatus.setStyleSheet("color:#8B949E;border:0;font-family:monospace;font-size:11px")
        self.chstatus.setWordWrap(True); self.chstatus.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        cxl.addWidget(self.chstatus)
        v.addWidget(cx)
        self._chtimer = QtCore.QTimer(self); self._chtimer.timeout.connect(self._chain_poll); self._chtimer.start(2500)
        v.addStretch()
        self._tip = 0; self._idx = {}; self._sizes = {}
        self._first_tip = 0; self._first_t = 0.0; self._last_tip = 0; self._last_tt = 0.0; self._rate = 0.0
        self._spin = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"; self._si = 0; self._last_tx = 0; self._crunch_text = "—"; self._was_idx = None
        # live like the blocks: tick every 3s while this tab is visible
        self._lt = QtCore.QTimer(self); self._lt.timeout.connect(self._tick); self._lt.start(3000)
        # spinner: animates ONLY while the index is actually advancing (a block indexed recently)
        self._spin_t = QtCore.QTimer(self); self._spin_t.timeout.connect(self._spin_tick); self._spin_t.start(110)
        # THROB: pulsing electric-blue glow = depth (forward/back) — runs only while indexing
        self._throb = QtWidgets.QGraphicsDropShadowEffect(self); self._throb.setColor(QtGui.QColor("#00BFFF"))
        self._throb.setOffset(0, 0); self._throb.setBlurRadius(4); self.crunch.setGraphicsEffect(self._throb)
        self._throbA = QtCore.QPropertyAnimation(self._throb, b"blurRadius"); self._throbA.setDuration(650); self._throbA.setLoopCount(-1)
        self._throbA.setKeyValueAt(0.0, 6); self._throbA.setKeyValueAt(0.5, 30); self._throbA.setKeyValueAt(1.0, 6)
    def _tick(self):
        if self.isVisible(): self.refresh()
    def _spin_tick(self):
        if not self.isVisible(): return
        indexing = bool(self._last_tt and (_now() - self._last_tt) < 6)   # advanced within 6s → actively indexing
        if indexing != self._was_idx:                                     # transition → toggle throb + style
            self._was_idx = indexing
            if indexing:
                self._throbA.start(); self.crunch.setStyleSheet("color:#00BFFF;font-weight:800;font-family:monospace;font-size:13px")
            else:
                self._throbA.stop(); self._throb.setBlurRadius(3); self.crunch.setStyleSheet("color:#5a6b7b;font-weight:600;font-family:monospace;font-size:13px")
        h = getattr(self, "_idx_h", 0); dtx = getattr(self, "_idx_dtx", 0); cache = getattr(self, "_idx_cache", "—")
        if not h:
            self.crunch.setText("waiting for the node…"); return
        if indexing:                                                  # a block was validated/indexed in the last 6s
            self._si = (self._si + 1) % len(self._spin)
            self.crunch.setText(f"{self._spin[self._si]}  indexing block #{h:,}   ·   +{dtx:,} tx this block"
                                f"   ·   {self._rate:.1f} blk/min   ·   UTXO cache {cache}")
        else:                                                         # at the tip, between blocks — this is healthy
            self.crunch.setText(f"✓  indexed to tip #{h:,}   ·   UTXO cache {cache}   ·   awaiting next block (~10 min)")
    INDEX_META = {
        "txindex": "Look up ANY transaction by txid (getrawtransaction) — required for ₿ANKON wallet/tx lookups.",
        "coinstatsindex": "Instant UTXO-set stats (gettxoutsetinfo): supply, UTXO count, muhash — no full scan.",
        "basic block filter index": "BIP157/158 compact block filters — lets light clients sync privately.",
    }
    @staticmethod
    def _fmt_size(n):
        if not n: return "—"
        for u in ("B", "KB", "MB", "GB", "TB"):
            if n < 1024 or u == "TB": return f"{n:.1f} {u}"
            n /= 1024.0
    def refresh(self):
        spawn_fn(lambda: fetch_json("/api/indexinfo"), self._setidx)   # FRESH, realtime (indexes + disk sizes)
        spawn_fn(synctip, self._settip)                    # cheap live chain tip (debug.log)
        self._load_recent()                                # refresh the recent-tx list when a new block lands
    def _settip(self, st):
        h = st.get("height")
        if h:
            now = _now()
            if not self._first_tip: self._first_tip = h; self._first_t = now
            if self._last_tip and h > self._last_tip and (now - self._last_tt) >= 2:
                inst = (h - self._last_tip) / ((now - self._last_tt) / 60.0)            # blocks/min
                self._rate = inst if not self._rate else 0.6 * self._rate + 0.4 * inst
            if h != self._last_tip: self._last_tip = h; self._last_tt = now
            self._tip = h
            tx = st.get("tx"); dtx = (tx - self._last_tx) if (tx and self._last_tx and tx >= self._last_tx) else 0
            if tx: self._last_tx = tx
            self._idx_h = h; self._idx_dtx = dtx; self._idx_cache = st.get('cache', '—')
            sess = h - self._first_tip; ago = int(now - self._last_tt) if self._last_tt else 0
            prog = st.get("progress")
            # honest states: measured rate → show it; at tip with nothing to do → the index IS
            # complete, say so; otherwise we simply haven't observed an advance yet — not "0.0"
            if self._rate:
                self.activity.setText(f"activity:  ▲ {self._rate:.1f} blk/min  ·  +{sess:,} indexed since open  ·  last advance {ago}s ago")
            elif prog is not None and prog >= 0.9999:
                self.activity.setText(f"activity:  ✓ index complete — at tip #{h:,}  ·  awaiting next block (~10 min)")
            else:
                self.activity.setText(f"activity:  measuring…  ·  +{sess:,} indexed since open")
            bd = (st.get("blockDate") or "—").replace("T", " ").replace("Z", "")
            lt = (st.get("logTime") or ""); lt = lt[11:19] if len(lt) >= 19 else "—"
            head = f"tip {h:,}  ·  {prog*100:.4f}%  " if prog is not None else f"tip {h:,}  "
            self.detail.setText(head + f"·  block date {bd}  ·  tx {st.get('tx') or 0:,}  ·  UTXO cache {st.get('cache','—')}  ·  last UpdateTip {lt}")
        self._render()
    def _setidx(self, resp):
        resp = resp or {}
        self._idx = resp.get("indexes", {}) or {}
        self._sizes = resp.get("sizes", {}) or {}
        self._render()
    # ---- recent-transaction history ----
    def _load_recent(self, force=False):
        if not self.isVisible():
            return
        # only refetch when a new block arrived (tip changed) or the user forced it (count change)
        if not force and self._recent_tip == self._tip:
            return
        self._recent_tip = self._tip
        n = self.txcount.value()
        spawn_fn(lambda: self._fetch_recent(n), self._fill_recent)
    @staticmethod
    def _fetch_recent(n):
        try:
            txids, h = [], rpc("getbestblockhash", [], timeout=12)
            while h and len(txids) < n:                        # walk back blocks until we have n txids
                blk = rpc("getblock", [h, 1], timeout=15)
                for t in reversed(blk.get("tx", [])):          # newest-in-block first
                    txids.append(t)
                    if len(txids) >= n:
                        break
                h = blk.get("previousblockhash")
            return {"txids": txids[:n], "height": None}
        except Exception as e:
            return {"error": str(e)}
    def _fill_recent(self, d):
        if not d or d.get("error"):
            self.txhint.setText(f"recent transactions unavailable ({(d or {}).get('error','?')})"); return
        txids = d.get("txids", [])
        self.txhist.clear()
        for tx in txids:
            self.txhist.addItem(tx)                             # bare txid — no numeric prefix
        self.txhint.setText(f"Latest {len(txids)} transactions (newest first) — click one to deep-dive:")
        # default the query field to the latest transaction
        if txids and not (self.txin.text() or "").strip():
            self.txin.setText(txids[0])
    def _hist_click(self, item):
        self.txin.setText(item.text().strip()); self._deep_dive()
    # ---- txindex deep-dive ----
    def _deep_dive(self):
        txid = (self.txin.text() or "").strip().lower()
        import re as _re
        if not _re.fullmatch(r"[0-9a-f]{64}", txid):
            self.txhint.setText("<span style='color:#f85149'>that doesn't look like a txid — need 64 hex characters</span>")
            return
        self.txbtn.setEnabled(False); self.txhint.setText("resolving via txindex…")
        spawn_fn(lambda: self._fetch_tx(txid), self._show_tx)
    @staticmethod
    def _fetch_tx(txid):
        try:
            tx = rpc("getrawtransaction", [txid, True], timeout=20)
        except Exception as e:
            return {"error": str(e)}
        vin = tx.get("vin", []); vout = tx.get("vout", [])
        is_cb = any("coinbase" in i for i in vin)
        out_sum = sum(o.get("value", 0) for o in vout)
        in_values = [None] * len(vin)
        in_sum, resolved = 0.0, (not is_cb and len(vin) <= 40)  # bounded: resolve prevouts for a real fee
        if resolved:
            for idx, i in enumerate(vin):
                try:
                    p = rpc("getrawtransaction", [i["txid"], True], timeout=15)
                    val = p["vout"][i["vout"]].get("value", 0)
                    in_values[idx] = val; in_sum += val
                except Exception:
                    resolved = False; break
        fee = (in_sum - out_sum) if (resolved and not is_cb) else None
        vsize = tx.get("vsize") or tx.get("size") or 0
        feerate = (fee * 1e8 / vsize) if (fee is not None and vsize) else None
        # block context (height + median time) — one cheap header call
        height = mediantime = None
        if tx.get("blockhash"):
            try:
                bh = rpc("getblockheader", [tx["blockhash"]], timeout=12)
                height = bh.get("height"); mediantime = bh.get("mediantime")
            except Exception:
                pass
        # derived scientific measures
        rbf = any(i.get("sequence", 0xffffffff) < 0xfffffffe for i in vin if "coinbase" not in i)
        segwit = any(i.get("txinwitness") for i in vin)
        wcount = sum(len(i.get("txinwitness", [])) for i in vin)
        return {"tx": tx, "coinbase": is_cb, "in_sum": in_sum if resolved else None,
                "out_sum": out_sum, "fee": fee, "feerate": feerate, "in_values": in_values,
                "height": height, "mediantime": mediantime, "rbf": rbf, "segwit": segwit, "wcount": wcount}
    def _show_tx(self, d):
        self.txbtn.setEnabled(True)
        if not d or d.get("error"):
            self.txhint.setText(f"<span style='color:#f85149'>lookup failed: {(d or {}).get('error','?')} "
                                f"(is txindex synced?)</span>"); return
        self.txhint.setText("Only a node with <b>txindex</b> can resolve an arbitrary txid — this is that payoff.")
        tx = d["tx"]; vout = tx.get("vout", []); vin = tx.get("vin", []); inv = d.get("in_values", [])
        dlg = QtWidgets.QDialog(self); dlg.setWindowTitle(f"tx {tx.get('txid','')[:16]}…"); dlg.resize(820, 660)
        L = QtWidgets.QVBoxLayout(dlg)
        # HIGHLIGHTS row
        conf = tx.get("confirmations"); status = ("✓ %d conf" % conf) if conf else "⏳ mempool"
        hi = [("outputs", f"{d['out_sum']:.8f} ₿"),
              ("fee", "coinbase" if d["coinbase"] else (f"{d['fee']:.8f} ₿" if d["fee"] is not None else "—")),
              ("fee rate", f"{d['feerate']:.1f} sat/vB" if d["feerate"] is not None else "—"),
              ("vsize", f"{tx.get('vsize','?')} vB"), ("in/out", f"{len(vin)} → {len(vout)}"), ("status", status)]
        hb = QtWidgets.QGridLayout()
        for i, (k, val) in enumerate(hi):
            kk = QtWidgets.QLabel(k); kk.setStyleSheet("color:#8aa0b4;font-size:10px")
            vv = QtWidgets.QLabel(str(val)); vv.setStyleSheet("color:#F7931A;font-weight:800;font-family:monospace;font-size:13px")
            hb.addWidget(kk, 0, i); hb.addWidget(vv, 1, i)
        L.addLayout(hb)
        # view toggle: Normal ↔ Scientific
        trow = QtWidgets.QHBoxLayout()
        trow.addWidget(QtWidgets.QLabel("view"))
        mode = QtWidgets.QComboBox(); mode.addItems(["Normal", "Scientific"])
        mode.setToolTip("Scientific reveals raw fields, per-input values, script types, witness/RBF/SegWit, and the raw hex")
        trow.addWidget(mode); trow.addStretch(1); L.addLayout(trow)
        # identity + block
        ident = QtWidgets.QLabel(f"<b>txid</b> {tx.get('txid','')}<br><b>block</b> {tx.get('blockhash','—')}"
                                 + (f" (height {d['height']:,})" if d.get("height") is not None else "")
                                 + (f"  ·  <b>time</b> {datetime.fromtimestamp(tx['time'], timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC" if tx.get('time') else ""))
        ident.setStyleSheet("font-family:monospace;font-size:11px;color:#d6e3ef"); ident.setWordWrap(True)
        ident.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse); L.addWidget(ident)
        # --- Scientific-only panel: raw tx fields + derived measures + raw hex ---
        sci = QtWidgets.QWidget(); sl = QtWidgets.QVBoxLayout(sci); sl.setContentsMargins(0, 0, 0, 0)
        def yn(b): return "yes" if b else "no"
        mt = d.get("mediantime")
        sci_fields = [("version", tx.get("version")), ("locktime", tx.get("locktime")),
                      ("size", f"{tx.get('size','?')} B"), ("vsize", f"{tx.get('vsize','?')} vB"),
                      ("weight", f"{tx.get('weight','?')} WU"),
                      ("SegWit", yn(d.get("segwit"))), ("witness items", d.get("wcount")),
                      ("RBF (BIP125)", yn(d.get("rbf"))),
                      ("inputs value", f"{d['in_sum']:.8f} ₿" if d.get("in_sum") is not None else "— (unresolved)"),
                      ("block median time", datetime.fromtimestamp(mt, timezone.utc).strftime('%Y-%m-%d %H:%M:%S') + " UTC" if mt else "—")]
        sg = QtWidgets.QGridLayout()
        for i, (k, val) in enumerate(sci_fields):
            r, c = divmod(i, 5)
            kk = QtWidgets.QLabel(k); kk.setStyleSheet("color:#00BFFF;font-size:10px")
            vv = QtWidgets.QLabel(str(val)); vv.setStyleSheet("color:#d6e3ef;font-family:monospace;font-size:11px")
            box = QtWidgets.QVBoxLayout(); box.setSpacing(0); box.addWidget(kk); box.addWidget(vv)
            sg.addLayout(box, r, c)
        sl.addLayout(sg)
        if d["coinbase"] and vin:                                # decode the coinbase scriptSig to ASCII
            cbhex = vin[0].get("coinbase", "")
            try:
                txt = bytes.fromhex(cbhex).decode("ascii", "replace")
                txt = "".join(ch if 32 <= ord(ch) < 127 else "·" for ch in txt)
            except Exception:
                txt = cbhex
            cb = QtWidgets.QLabel(f"⛏ coinbase message: <span style='color:#FFD37A'>{txt}</span>")
            cb.setStyleSheet("font-family:monospace;font-size:11px"); cb.setWordWrap(True); sl.addWidget(cb)
        rawlbl = QtWidgets.QLabel("raw transaction (hex):"); rawlbl.setStyleSheet("color:#8aa0b4;font-size:10px"); sl.addWidget(rawlbl)
        raw = QtWidgets.QPlainTextEdit(); raw.setReadOnly(True); raw.setMaximumHeight(70); raw.setPlainText(tx.get("hex", ""))
        raw.setStyleSheet("font-family:monospace;font-size:10px;background:#05080d;color:#c9d4e0;border:1px solid #14405c"); sl.addWidget(raw)
        L.addWidget(sci)
        # inputs + outputs tables (extra columns hidden in Normal)
        split = QtWidgets.QHBoxLayout()
        it = QtWidgets.QTableWidget(); it.setColumnCount(4)
        it.setHorizontalHeaderLabels(["input (prev out)", "n", "value ₿", "seq"])
        it.setRowCount(len(vin)); it.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers); it.verticalHeader().setVisible(False)
        for r, i in enumerate(vin):
            src = "⛏ coinbase (new coins)" if "coinbase" in i else f"{i.get('txid','')[:20]}…"
            it.setItem(r, 0, QtWidgets.QTableWidgetItem(src)); it.setItem(r, 1, QtWidgets.QTableWidgetItem(str(i.get("vout", ""))))
            iv = inv[r] if r < len(inv) else None
            it.setItem(r, 2, QtWidgets.QTableWidgetItem(f"{iv:.8f}" if iv is not None else "—"))
            seq = i.get("sequence"); it.setItem(r, 3, QtWidgets.QTableWidgetItem(hex(seq) if isinstance(seq, int) else "—"))
        it.resizeColumnsToContents()
        ot = QtWidgets.QTableWidget(); ot.setColumnCount(4); ot.setHorizontalHeaderLabels(["#", "address", "value ₿", "type"])
        ot.setRowCount(len(vout)); ot.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers); ot.verticalHeader().setVisible(False)
        for r, o in enumerate(vout):
            spk = o.get("scriptPubKey", {}); addr = spk.get("address") or (spk.get("type", "") + " (no addr)")
            ot.setItem(r, 0, QtWidgets.QTableWidgetItem(str(o.get("n", r))))
            ot.setItem(r, 1, QtWidgets.QTableWidgetItem(addr))
            vitem = QtWidgets.QTableWidgetItem(f"{o.get('value', 0):.8f}"); vitem.setForeground(QtGui.QColor("#16C784"))
            ot.setItem(r, 2, vitem)
            ot.setItem(r, 3, QtWidgets.QTableWidgetItem(spk.get("type", "")))
        ot.resizeColumnsToContents()
        for w, lab in [(it, f"Inputs ({len(vin)})"), (ot, f"Outputs ({len(vout)})")]:
            col = QtWidgets.QVBoxLayout(); h = QtWidgets.QLabel(lab); h.setStyleSheet("color:#00BFFF;font-weight:700")
            col.addWidget(h); col.addWidget(w); split.addLayout(col)
        L.addLayout(split, 1)
        cl = QtWidgets.QPushButton("Close"); cl.clicked.connect(dlg.accept); L.addWidget(cl)
        # toggle behaviour: Scientific reveals the extra panel + input value/seq + output type columns
        def apply_mode(m):
            scientific = (m == "Scientific")
            sci.setVisible(scientific)
            it.setColumnHidden(2, not scientific); it.setColumnHidden(3, not scientific)
            ot.setColumnHidden(3, not scientific)
            it.resizeColumnsToContents(); ot.resizeColumnsToContents()
        mode.currentTextChanged.connect(apply_mode); apply_mode("Normal")
        dlg.exec()
    # ---- RAGEbtc chain export ----
    def _chain_start(self):
        self.chexbtn.setEnabled(False); self.chstatus.setText("starting chain export…")
        spawn_fn(lambda: post_json("/api/chain/export", {"resume": True}, timeout=15),
                 lambda d: self.chstatus.setText("✗ " + str((d or {}).get("error")) if d and not d.get("ok")
                                                 else "export running — resumable; watch progress below"),
                 lambda e: self.chstatus.setText(f"✗ start failed: {e}"))
        QtCore.QTimer.singleShot(1500, lambda: self.chexbtn.setEnabled(True))
    def _chain_stop(self):
        spawn_fn(lambda: post_json("/api/chain/export/stop", {}, timeout=10), lambda d: None)
    def _chain_poll(self):
        if not self.isVisible():
            return
        spawn_fn(lambda: fetch_json("/api/chain/export/status"), self._chain_fill)
    def _chain_fill(self, d):
        if not d or not d.get("ok"):
            return
        st = d.get("status", "idle"); pct = d.get("pct", 0)
        self.chbar.setValue(int(pct * 1000))
        mode = "dry-run" if d.get("dryRun") else ("→ " + (d.get("schema") or "db"))
        self.chbar.setFormat(f"{st} · {pct:.2f}% ({mode})")
        h, tip = d.get("height", -1), d.get("tip")
        if st in ("running", "done", "paused", "error") and tip:
            eta = d.get("etaSec")
            eta_s = (f" · ETA {eta // 3600}h{(eta % 3600) // 60}m" if eta and eta > 60 else (f" · ETA {eta}s" if eta else ""))
            err = f"  ✗ {d.get('lastError')}" if d.get("lastError") else ""
            self.chstatus.setText(f"{st}: block {h:,}/{tip:,} · {d.get('blocksDone',0):,} blocks · "
                                  f"{d.get('txDone',0):,} tx · {d.get('rateBlkS',0)} blk/s{eta_s}"
                                  f"{'  (dry-run — set DATABASE_URL to persist)' if d.get('dryRun') else ''}{err}")
    def _export_utxo(self):
        d = QtWidgets.QMessageBox(self)
        d.setIcon(QtWidgets.QMessageBox.Question); d.setWindowTitle("Export UTXO snapshot")
        d.setText("Create a portable UTXO snapshot (assumeUTXO)?")
        d.setInformativeText("Runs dumptxoutset \"latest\" → writes ~5–11 GB to ~/bankon-tools/exports/ and "
                             "takes several minutes. Network stays active (no rollback). Another node can then "
                             "loadtxoutset it to bootstrap fast.\n\nThe txindex itself is not portable and is not exported.")
        d.setStandardButtons(QtWidgets.QMessageBox.Ok | QtWidgets.QMessageBox.Cancel)
        if d.exec() != QtWidgets.QMessageBox.Ok: return
        self.exbtn.setEnabled(False)
        self.exstatus.setText("⏳ exporting UTXO snapshot… (several minutes; the node stays online)")
        spawn_fn(lambda: post_json("/api/index/export-utxo", {}, timeout=2700), self._export_done)
    def _export_done(self, d):
        self.exbtn.setEnabled(True)
        if not d or not d.get("ok"):
            self.exstatus.setText(f"✗ export failed: {(d or {}).get('error', 'unknown')}"); return
        r = d.get("result", {}) or {}; path = d.get("path", "")
        h = r.get("base_height"); bh = r.get("base_hash", ""); uh = r.get("txoutset_hash", "")
        coins = r.get("coins_written")
        self.exstatus.setText(
            f"✓ snapshot written in {d.get('elapsedSec','?')}s → {path}\n"
            f"   base height {h:,}  ·  {coins:,} coins  ·  utxo hash {uh[:16]}…\n"
            f"   use on another node:  bitcoin-cli loadtxoutset \"{path}\""
            if h is not None else f"✓ exported → {path}")
    @staticmethod
    def _eta(behind, rate):
        if behind <= 0: return "—"
        if not rate or rate <= 0: return "?"
        m = behind / rate
        return f"{m:.0f} min" if m < 90 else (f"{m/60:.1f} h" if m < 2160 else f"{m/1440:.1f} d")
    def _render(self):
        idx = self._idx; tip = self._tip
        KNOWN = ["txindex", "coinstatsindex", "basic block filter index"]
        if not idx:
            # getindexinfo unavailable (node RPC choked during IBD). txindex tracks validation,
            # so show the live validated tip as its frontier — advances in realtime via debug.log.
            if tip:
                self.t.setRowCount(1)
                sz = self._fmt_size(self._sizes.get("txindex"))
                cells = ["txindex", f"~{tip:,}", "0", "tracking", f"{self._rate:.1f}", "≈ tip", sz, "indexing… (≈ tip)"]
                for c, val in enumerate(cells):
                    it = QtWidgets.QTableWidgetItem(val)
                    if c == 0: it.setToolTip(self.INDEX_META.get("txindex", ""))
                    self.t.setItem(0, c, it)
                self.t.resizeColumnsToContents()
                self.bar.setValue(99000); self.bar.setFormat(f"txindex ≈ tip {tip:,} (live; exact height when RPC frees)")
                self.lbl.setText(f"<b>Index quality</b> — live · txindex tracking validation (tip {tip:,}) · getindexinfo pending (RPC busy)")
            else:
                self.lbl.setText("Indexes — waiting for node"); self.t.setRowCount(0); self.bar.setFormat("…")
            return
        names = sorted(idx.keys()); primary = 0.0
        self.t.setRowCount(len(names))
        for r, name in enumerate(names):
            e = idx[name] or {}; bh = e.get("best_block_height", 0); synced = bool(e.get("synced"))
            ref = tip or bh; behind = max(0, ref - bh)
            pct = (bh / ref * 100) if ref else (100.0 if synced else 0.0)
            if name == "txindex" or not primary: primary = pct
            cells = [name, f"{bh:,}", f"{behind:,}", f"{pct:.3f}%",
                     f"{self._rate:.1f}" if behind > 0 else "—",
                     "—" if synced else self._eta(behind, self._rate),
                     self._fmt_size(self._sizes.get(name)),
                     "synced ✓" if synced else "indexing…"]
            for c, val in enumerate(cells):
                it = QtWidgets.QTableWidgetItem(val)
                if c == 0: it.setToolTip(self.INDEX_META.get(name, ""))
                if c == 7: it.setForeground(QtGui.QColor("#16C784" if synced else "#F7931A"))
                self.t.setItem(r, c, it)
        self.t.resizeColumnsToContents()
        self.bar.setValue(int(primary * 1000)); self.bar.setFormat(f"{primary:.3f}% — tip {tip:,}")
        allsync = all((idx[n] or {}).get("synced") for n in names)
        missing = [k for k in KNOWN if k not in names]
        miss = f"  ·  not enabled: {', '.join(missing)}" if missing else ""
        total_sz = sum(v for v in self._sizes.values() if v)
        szs = f"  ·  total index disk: {self._fmt_size(total_sz)}" if total_sz else ""
        self.lbl.setText(f"<b>Index quality</b> — live · {len(names)} index(es) · "
                         f"{'all synced' if allsync else 'building'} (tip {tip:,}){szs}{miss}")
        # what each present index enables (hover the index name for the same)
        desc = " · ".join(f"<b>{n}</b>: {self.INDEX_META[n].split(' — ')[0].split(':')[0]}"
                          for n in names if n in self.INDEX_META)
        if desc: self.note.setText(desc + "  ·  txindex is mutually exclusive with pruning (prune=N).")


class ConsoleTab(QtWidgets.QWidget):
    ALLOW = {"getblockchaininfo","getblock","getblockstats","getblockhash","getchaintxstats",
             "getmempoolinfo","getrawmempool","getpeerinfo","getnetworkinfo","getindexinfo",
             "getmininginfo","estimatesmartfee","getblockcount","uptime","getnettotals","listwallets"}
    HELP = {
        "getblockchaininfo": "Chain state — height, verificationprogress, size, pruned, IBD flag.",
        "getblock": "₿lock by hash.  params: [hash, verbosity 0|1|2]",
        "getblockstats": "Per-block stats (fees, size, txs).  params: [height | hash]",
        "getblockhash": "₿lock hash at a height.  params: [height]",
        "getchaintxstats": "Tx count & rate over a window.  params: [nblocks]",
        "getmempoolinfo": "Mempool size, bytes, usage, min relay fee.",
        "getrawmempool": "Mempool txids.  params: [verbose true|false]",
        "getpeerinfo": "Connected peers — addr, subver, ping, height, direction.",
        "getnetworkinfo": "Version, connection count, relay fee, reachable networks.",
        "getindexinfo": "Index status (txindex, coinstatsindex, blockfilter…).",
        "getmininginfo": "Difficulty, network hashrate, mempool size.",
        "estimatesmartfee": "Fee estimate (₿TC/kvB).  params: [conf_target]",
        "getblockcount": "Current validated block height.",
        "uptime": "Node uptime in seconds.",
        "getnettotals": "Total bytes sent / received.",
        "listwallets": "Currently loaded wallets.",
    }
    def __init__(self):
        super().__init__(); v = QtWidgets.QVBoxLayout(self)
        # ---- rageRPC controller (toggle + throttle + breaker) ----
        box = QtWidgets.QFrame(); box.setObjectName("ragebox"); rl = QtWidgets.QHBoxLayout(box)
        self.rage = QtWidgets.QCheckBox("⚡ rageRPC"); self.rage.setChecked(True)
        self.rage.setToolTip("Accelerated RPC — max throughput (safe: rpcworkqueue absorbs bursts, breaker backstops)")
        self.rage.toggled.connect(self._rage_toggled); rl.addWidget(self.rage)
        rl.addSpacing(8); rl.addWidget(QtWidgets.QLabel("throttle"))
        self.inflight = QtWidgets.QSpinBox(); self.inflight.setRange(1, 32); self.inflight.setValue(32); self.inflight.setSuffix(" in-flight")
        rl.addWidget(self.inflight)
        rl.addWidget(QtWidgets.QLabel("breaker"))
        self.distress = QtWidgets.QSpinBox(); self.distress.setRange(0, 120); self.distress.setValue(3); self.distress.setSuffix(" s")
        rl.addWidget(self.distress)
        ap = QtWidgets.QPushButton("Apply"); ap.clicked.connect(self._apply); rl.addWidget(ap)
        hb = QtWidgets.QPushButton("Handoff → rage"); hb.setToolTip("Gather nodes → pgvectorscale (rage.pythai.net)")
        hb.clicked.connect(self._handoff); rl.addWidget(hb)
        rl.addStretch(); v.addWidget(box)
        self.settings_lbl = QtWidgets.QLabel("settings: —"); self.settings_lbl.setStyleSheet("color:#8aa0b4;font-family:monospace")
        v.addWidget(self.settings_lbl)
        # ---- RPC runner: method dropdown (standard catalog) + params + help ----
        h = QtWidgets.QHBoxLayout()
        self.m = QtWidgets.QComboBox(); self.m.setEditable(True); self.m.addItems(sorted(self.ALLOW))
        self.m.currentTextChanged.connect(self._help); self.m.currentIndexChanged.connect(self._pick)
        self.p = QtWidgets.QLineEdit(); self.p.setPlaceholderText('params JSON e.g. [800000]')
        run = QtWidgets.QPushButton("Run"); run.clicked.connect(self.run)
        h.addWidget(self.m, 2); h.addWidget(self.p, 2); h.addWidget(run); v.addLayout(h)
        self.help_lbl = QtWidgets.QLabel(""); self.help_lbl.setStyleSheet("color:#16C784"); self.help_lbl.setWordWrap(True)
        v.addWidget(self.help_lbl)
        self.out = QtWidgets.QPlainTextEdit(); self.out.setReadOnly(True)
        v.addWidget(QtWidgets.QLabel("read-only whitelist")); v.addWidget(self.out, 1)
        self._help(self.m.currentText())
        self._st = QtCore.QTimer(self); self._st.timeout.connect(self._tick); self._st.start(3000)
        self._load_catalog()                 # pull the FULL server catalog → grouped dropdown + help + examples
    def _tick(self):
        if self.isVisible(): self.refresh()
    def _load_catalog(self):
        spawn_fn(lambda: fetch_json("/api/catalog").get("catalog", {}), self._setcat)
    def _setcat(self, cat):
        if not cat: return
        self.ALLOW = set(); self.HELP = dict(self.HELP)
        self.m.blockSignals(True); self.m.clear()
        for category, lst in cat.items():
            self.m.addItem(f"——  {category}  ——")
            it = self.m.model().item(self.m.count() - 1)
            it.setEnabled(False); it.setForeground(QtGui.QColor("#F7931A"))
            for e in lst:
                m = e.get("m"); self.ALLOW.add(m); self.HELP[m] = e.get("d", self.HELP.get(m, ""))
                self.m.addItem(m, e)         # store the entry (with example params) as item data
        self.m.blockSignals(False)
        self.m.setCurrentIndex(1)            # first real method
        self._help(self.m.currentText())
    def _pick(self):
        m = self.m.currentText().strip(); self._help(m)
        data = self.m.currentData()          # entry dict for catalog items
        if isinstance(data, dict) and data.get("ex") is not None and not self.p.text().strip():
            self.p.setText(json.dumps(data["ex"]))   # prefill example params (only if empty)
    def refresh(self):
        spawn_fn(lambda: fetch_json("/api/settings").get("settings", {}), self._show_settings)
    def _show_settings(self, s):
        r = (s or {}).get("rpc", {})
        if not r: return
        cb = "OPEN·backoff" if r.get("circuitOpen") else "closed"
        eff = r.get("effInflight", r.get("maxInflight"))
        self.settings_lbl.setText(
            f"rage={r.get('rage')}  throttle={eff}/{r.get('maxInflight')} in-flight (adaptive)  breaker={r.get('distressMs',0)/1000:.0f}s [{cb}]"
            f"   ·   inflight={r.get('inflight')} waiting={r.get('waiting')}")
    def _rage_toggled(self, on):
        self.inflight.setValue(32 if on else 4); self.distress.setValue(3 if on else 12)
    def _apply(self):
        body = {"rage": self.rage.isChecked(), "maxInflight": self.inflight.value(), "distressMs": self.distress.value() * 1000}
        self.settings_lbl.setText("applying…")
        spawn_fn(lambda: post_json("/api/settings", body), lambda d: self._show_settings((d or {}).get("settings", {})))
    def _handoff(self):
        self.settings_lbl.setText("handing off to rage.pythai.net …")
        spawn_fn(lambda: post_json("/api/rage/handoff", {"count": 500}), self._handoff_done)
    def _handoff_done(self, d):
        d = d or {}
        if d.get("ok"):
            self.settings_lbl.setText(f"✓ handoff: {d.get('gathered')} nodes ({d.get('source')}) → {d.get('target')} (HTTP {d.get('push',{}).get('status')})")
        else:
            self.settings_lbl.setText(f"✗ handoff: {d.get('error') or d.get('push',{}).get('response','failed')}")
    def _help(self, m):
        self.help_lbl.setText("ℹ  " + self.HELP.get((m or "").strip(), "—"))
    def run(self):
        m = self.m.currentText().strip()
        if m not in self.ALLOW: self.out.setPlainText(f"'{m}' not in read-only whitelist"); return
        try: params = json.loads(self.p.text()) if self.p.text().strip() else []
        except Exception: self.out.setPlainText("params must be valid JSON array"); return
        if not isinstance(params, list): self.out.setPlainText("params must be a JSON array"); return
        # run OFF the UI thread — a slow node reply must never freeze the app (financial software:
        # a frozen UI invites double-clicks and misreads; the whitelist above is the write-guard)
        self.out.setPlainText("…")
        spawn_fn(lambda: rpc(m, params),
                 lambda v: self.out.setPlainText(json.dumps(v, indent=2)),
                 lambda e: self.out.setPlainText("ERROR: " + str(e)))


def _dur(s):
    s = int(s or 0); h = s // 3600; m = (s % 3600) // 60
    return f"{h}h {m}m" if h else (f"{m}m {s % 60}s" if m else f"{s}s")

def peer_diag_lines(p):
    """Rich per-peer diagnostics from one getpeerinfo entry → ordered (label, value) pairs.
    This is the 'more information when an individual node is chosen' surface."""
    now = _now(); age = now - (p.get("conntime") or now)
    recv = p.get("bytesrecv", 0) or 0; sent = p.get("bytessent", 0) or 0
    svc = p.get("servicesnames"); svc = ", ".join(svc) if isinstance(svc, list) and svc else (p.get("services", "—"))
    return [
        ("address",         p.get("addr", "—")),
        ("local / bind",    p.get("addrlocal") or p.get("addrbind") or "—"),
        ("network",         p.get("network", "—")),
        ("direction",       "↓ inbound" if p.get("inbound") else "↑ outbound"),
        ("conn type",       p.get("connection_type", "—")),
        ("client",          (p.get("subver", "") or "").replace("/", "") or "—"),
        ("protocol ver",    p.get("version", "—")),
        ("services",        svc),
        ("relays txs",      "yes" if p.get("relaytxes") else "no"),
        ("ping now / min",  f"{(p.get('pingtime',0) or 0)*1000:.0f} / {(p.get('minping',0) or 0)*1000:.0f} ms"),
        ("↓ recv rate",     f"{recv / max(1.0, age) / 1024.0:.1f} KB/s"),
        ("↓ received",      f"{recv/1048576:.2f} MiB"),
        ("↑ sent",          f"{sent/1048576:.2f} MiB"),
        ("connected for",   _dur(age)),
        ("their blocks",    p.get("synced_blocks", "—")),
        ("their headers",   p.get("synced_headers", "—")),
        # startingheight was removed from getpeerinfo in Core v31 — presynced_headers is the
        # closest live measure of where this peer's header sync stands before validation
        ("presynced hdrs",  p.get("presynced_headers", p.get("startingheight", "—"))),
        ("blocks in-flight", len(p.get("inflight") or [])),
        ("manual (addnode)", "yes" if p.get("addnode") else "no"),
    ]

def peer_action(kind, addr, on_done=None, subver=""):
    """promote (favourite + persistent addnode), unpromote, or boot (disconnect) a peer."""
    if kind == "promote":   path, body = "/api/node/promote", {"addr": addr, "subver": subver, "on": True}
    elif kind == "unpromote": path, body = "/api/node/promote", {"addr": addr, "on": False}
    elif kind == "boot":    path, body = "/api/node/boot", {"addr": addr}
    else: return
    spawn_fn(lambda: post_json(path, body), on_done)


def peer_rows(peers):
    now = _now(); rows = []
    for p in peers:
        rate = (p.get("bytesrecv", 0) or 0) / max(1.0, now - (p.get("conntime") or now)) / 1024.0  # ↓ KB/s
        rows.append([p.get("addr"), (p.get("subver", "").replace("/", "")), "in" if p.get("inbound") else "out",
                     f"{(p.get('pingtime',0) or 0)*1000:.0f}", f"{rate:.1f}", p.get("synced_blocks", "—")])
    return rows

def mp_fill(f, m, d, stale):
    d = d or {}
    f["txs"].setText(f"{d.get('size',0):,}")
    f["virtual size"].setText(f"{d.get('bytes',0):,} vB")
    mx = d.get('maxmempool', 0) or 0
    f["memory / max"].setText(f"{d.get('usage',0)/1048576:.1f} / {mx/1048576:.0f} MiB")
    f["min relay fee"].setText(f"{d.get('minrelaytxfee','—')} ₿TC/kvB")
    f["mempool min fee"].setText(f"{d.get('mempoolminfee','—')} ₿TC/kvB")
    tf = d.get('total_fee'); f["total fee"].setText(f"{tf} ₿TC" if tf is not None else "—")
    f["unbroadcast"].setText(f"{d.get('unbroadcastcount',0):,}")
    # fullrbf key is removed in Core v31.1+ (RBF is universal since v28) — absent ≠ unknown
    rbf = d.get("fullrbf")
    f["RBF / loaded"].setText(f"fullrbf={'universal (v28+)' if rbf is None else rbf} · loaded={d.get('loaded','?')}")

def idx_fill(f, m, d, stale):
    if "txindex" in (d or {}):
        e = d["txindex"]; f["txindex synced"].setText(str(e.get("synced")))
        f["txindex height"].setText(f"{e.get('best_block_height',0):,}")
    else:
        f["txindex synced"].setText("— (pruned/none)")


class NetworkMapTab(QtWidgets.QWidget):
    """EtherApe-style live topology from ONE ₿itcoin Core node: our node at centre,
    connected peers radial (link width + node size + colour = traffic, inbound vs
    outbound tinted), and a faint outer cloud of ALL nodes our addrman knows.

    DYNAMIC: a pulse timer animates traffic 'packets' flowing along each link and a
    selection halo, so the map is visibly live. CLICK any peer node → a diagnostics
    panel shows that peer's full getpeerinfo + Promote (favourite) / ₿oot actions."""
    def __init__(self):
        super().__init__(); v = QtWidgets.QVBoxLayout(self)
        top = QtWidgets.QHBoxLayout()
        self.info = QtWidgets.QLabel("Network map — your node (centre), connected peers, and the known-node cloud")
        self.info.setStyleSheet("color:#8aa0b4"); top.addWidget(self.info, 1)
        self.speed = QtWidgets.QLabel(""); self.speed.setStyleSheet("font-family:monospace;font-weight:700")
        self.speed.setToolTip("Live node throughput (getnettotals) — orange = data in, green = data out")
        top.addWidget(self.speed)
        self.layoutbtn = QtWidgets.QPushButton("▲ Pyramid")
        self.layoutbtn.setToolTip("Switch topology: radial sphere ↔ pyramid (₿ANKON node at the apex, "
                                  "busiest peers in the top tiers, addrman cloud as the base)")
        self.layoutbtn.clicked.connect(self._toggle_layout); top.addWidget(self.layoutbtn)
        top.addWidget(QtWidgets.QLabel("max nodes"))
        self.maxnodes = QtWidgets.QSpinBox(); self.maxnodes.setRange(0, 50000); self.maxnodes.setSingleStep(500)
        self.maxnodes.setValue(5000); self.maxnodes.setToolTip("Max known nodes to fetch/draw (+/- or type)")
        self.maxnodes.valueChanged.connect(lambda _: self.refresh()); top.addWidget(self.maxnodes)
        v.addLayout(top)
        split = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        self.scene = QtWidgets.QGraphicsScene()
        self.view = QtWidgets.QGraphicsView(self.scene)
        self.view.setRenderHints(QtGui.QPainter.Antialiasing | QtGui.QPainter.TextAntialiasing |
                                 QtGui.QPainter.SmoothPixmapTransform)
        self.view.setStyleSheet("background:#05080d;border:2px solid #00BFFF;border-radius:8px")
        self.view.setMinimumHeight(300)   # modest floor so the map stays usable on small screens
        self.view.setTransformationAnchor(QtWidgets.QGraphicsView.AnchorUnderMouse)   # wheel zooms at cursor
        self.view.setDragMode(QtWidgets.QGraphicsView.ScrollHandDrag)                 # drag pans when zoomed in
        self.view.viewport().installEventFilter(self)        # click → select a peer node · wheel → zoom
        self.view.installEventFilter(self)                   # view resize → re-pin the overlay zoom buttons
        # zoom controls live INSIDE the map, pinned to its top-right corner
        self._zbtns = []
        for txt, tip, fn in [("+", "Zoom in", lambda: self._zoom(1.15)),
                             ("−", "Zoom out", lambda: self._zoom(1 / 1.15)),
                             ("⤢", "Fit whole map", self._fit)]:
            b = QtWidgets.QPushButton(txt, self.view)
            b.setFixedSize(34, 30); b.setToolTip(tip); b.clicked.connect(fn)
            b.setStyleSheet("QPushButton{background:rgba(8,16,26,0.85);color:#00BFFF;border:1px solid #14405c;"
                            "border-radius:6px;font-weight:800;font-size:15px;padding:0}"
                            "QPushButton:hover{background:#14405c;color:#fff}")
            b.raise_(); self._zbtns.append(b)
        # EXPLORE mode: back-to-default overlay button (top-left, only while exploring)
        self._explore = None; self._cloud = []
        self.backbtn = QtWidgets.QPushButton("⬅ local view", self.view)
        self.backbtn.setToolTip("Return to the default presentation — local node → connected peers with live traffic")
        self.backbtn.move(10, 10)
        self.backbtn.setStyleSheet("QPushButton{background:rgba(8,16,26,0.9);color:#F7931A;border:1px solid #F7931A;"
                                   "border-radius:6px;font-weight:800;padding:4px 10px}"
                                   "QPushButton:hover{background:#3a2500}")
        self.backbtn.clicked.connect(self._exit_explore); self.backbtn.hide()
        # DOWN-STATE onboarding overlay — when Core is off there are no nodes to map, so turn the empty
        # map into a call-to-action: start Core + read the ₿ANKON FAQ/docs. Shown over the view; hidden
        # the moment peers/activity appear.
        self.downpanel = QtWidgets.QFrame(self.view)
        self.downpanel.setStyleSheet("QFrame{background:rgba(8,16,26,0.95);border:2px solid #F7931A;border-radius:12px}")
        dp = QtWidgets.QVBoxLayout(self.downpanel); dp.setSpacing(8); dp.setContentsMargins(24, 18, 24, 18)
        _h = QtWidgets.QLabel("₿  the wallet you can ₿ANKON"); _h.setAlignment(QtCore.Qt.AlignCenter)
        _h.setStyleSheet("color:#F7931A;font-weight:800;font-size:16px;border:0"); dp.addWidget(_h)
        self.downmsg = QtWidgets.QLabel("₿itcoin Core is not running — ₿ANKON attaches to your node.\n"
                                        "Start Core to map the network.")
        self.downmsg.setAlignment(QtCore.Qt.AlignCenter); self.downmsg.setWordWrap(True)
        self.downmsg.setStyleSheet("color:#d6e3ef;border:0"); dp.addWidget(self.downmsg)
        _sb = QtWidgets.QPushButton("▶  Start ₿itcoin Core")
        _sb.setStyleSheet("QPushButton{background:#17a24b;color:#eafff0;font-weight:800;border:2px solid #0b5d34;"
                          "border-radius:8px;padding:8px}QPushButton:hover{background:#1fc75e}")
        _sb.clicked.connect(self._start_core); dp.addWidget(_sb)
        _lk = QtWidgets.QHBoxLayout()
        for _txt, _key in [("❓ FAQ", "faq"), ("📖 Docs", "docs")]:
            _b = QtWidgets.QPushButton(_txt); _b.setObjectName("secondary")
            _b.clicked.connect(lambda _c, k=_key: self._open_doc(k)); _lk.addWidget(_b)
        dp.addLayout(_lk)
        _lk2 = QtWidgets.QHBoxLayout()
        for _txt, _key in [("₿ ₿TC Standard ↗", "cypherpunk"), ("🎛 gnuGUI ↗", "gnugui")]:
            _b = QtWidgets.QPushButton(_txt); _b.setObjectName("secondary")
            _b.clicked.connect(lambda _c, k=_key: self._open_doc(k)); _lk2.addWidget(_b)
        dp.addLayout(_lk2)
        _rd = QtWidgets.QLabel("New here? Read the ₿ANKON FAQ & docs while Core starts.")
        _rd.setAlignment(QtCore.Qt.AlignCenter); _rd.setStyleSheet("color:#8aa0b4;font-size:10px;border:0"); dp.addWidget(_rd)
        self.downpanel.hide(); self._core_off = False
        split.addWidget(self.view)
        split.addWidget(self._build_diag())
        # open at a 50/50 split between the map and the diagnostics panel
        split.setStretchFactor(0, 1); split.setStretchFactor(1, 1); split.setSizes([500, 500])
        v.addWidget(split, 1)
        self._ni, self._known, self._peers, self._act, self._pstale = {}, [], [], [], False
        self._user_zoom = False  # once the user zooms, redraws stop auto-fitting
        self._prev = {}          # addr -> (bytesrecv, bytessent, t) last sample, for live B/s
        self._rates = {}         # addr -> (in B/s, out B/s)
        self._tot_prev = None; self._tot_rate = (0.0, 0.0)
        self._hits = []          # [(x, y, r, peer)] for click hit-testing (scene coords)
        self._links = []         # [(x, y, frac)] connected-peer endpoints for flow animation
        self._anim = []          # overlay scene-items (re-drawn each pulse tick)
        self._sel = None         # currently-selected peer dict
        self._phase = 0.0
        self._atimer = QtCore.QTimer(self); self._atimer.timeout.connect(self._pulse); self._atimer.start(60)
    def _toggle_layout(self):
        self._layout = "pyramid" if getattr(self, "_layout", "radial") == "radial" else "radial"
        self.layoutbtn.setText("◉ Radial" if self._layout == "pyramid" else "▲ Pyramid")
        self._user_zoom = False                      # re-fit to the new shape
        self._redraw()

    def _build_diag(self):
        w = QtWidgets.QWidget(); w.setMinimumWidth(260)
        d = QtWidgets.QVBoxLayout(w); d.setContentsMargins(10, 4, 4, 4)
        self.diag_title = QtWidgets.QLabel("◎ click a peer node for diagnostics")
        self.diag_title.setStyleSheet("color:#F7931A;font-weight:700;font-family:monospace")
        self.diag_title.setWordWrap(True); d.addWidget(self.diag_title)
        self.diag_body = QtWidgets.QWidget(); self.diag_form = QtWidgets.QFormLayout(self.diag_body)
        self.diag_form.setLabelAlignment(QtCore.Qt.AlignRight); self.diag_form.setVerticalSpacing(3)
        sc = QtWidgets.QScrollArea(); sc.setWidgetResizable(True); sc.setWidget(self.diag_body)
        sc.setStyleSheet("border:1px solid #14405c;border-radius:6px"); d.addWidget(sc, 1)
        br = QtWidgets.QHBoxLayout()
        self.btn_promote = QtWidgets.QPushButton("★ Promote"); self.btn_promote.setObjectName("good")
        self.btn_promote.setToolTip("Mark favourite + keep a persistent connection (addnode)")
        self.btn_promote.clicked.connect(lambda: self._act_peer("promote"))
        self.btn_boot = QtWidgets.QPushButton("⏏ ₿oot"); self.btn_boot.setObjectName("danger")
        self.btn_boot.setToolTip("Disconnect this peer now (disconnectnode)")
        self.btn_boot.clicked.connect(lambda: self._act_peer("boot"))
        # discoverable entry into the 3D cluster-explore (double-click still works)
        self.btn_explore = QtWidgets.QPushButton("◉ 3D explore"); self.btn_explore.setObjectName("secondary")
        self.btn_explore.setToolTip("Explore this peer's network neighbourhood as a rotating 3D pointcloud\n"
                                    "(gossip-inferred: same ASN / prefix — cluster lines carry no flow dots)")
        self.btn_explore.clicked.connect(lambda: self._sel and self._enter_explore(self._sel))
        self.btn_explore.setEnabled(False)
        self.btn_ban = QtWidgets.QPushButton("🚫 ₿an"); self.btn_ban.setObjectName("danger")
        self.btn_ban.setToolTip("₿lacklist as unreliable — setban 7 days + disconnect")
        self.btn_ban.clicked.connect(lambda: self._list_ban(self._sel.get("addr") if self._sel else "", True))
        br.addWidget(self.btn_promote); br.addWidget(self.btn_boot); br.addWidget(self.btn_ban); br.addWidget(self.btn_explore); d.addLayout(br)
        self.diag_status = QtWidgets.QLabel(""); self.diag_status.setStyleSheet("color:#8aa0b4"); self.diag_status.setWordWrap(True)
        d.addWidget(self.diag_status)
        self.btn_promote.setEnabled(False); self.btn_boot.setEnabled(False); self.btn_ban.setEnabled(False); self.btn_explore.setEnabled(False)
        # ---- winners + whitelist/blacklist: 🏆 fastest · ★ promoted · 🚫 banned (view + edit) ----
        self.lists = QtWidgets.QTabWidget(); self.lists.setMaximumHeight(190)
        self.lists.setStyleSheet("QTabBar::tab{padding:3px 10px} QListWidget{font-family:monospace;font-size:11px;"
                                 "background:#05080d;border:1px solid #14405c;border-radius:4px}")
        self.win_list, self.fav_list, self.ban_list = (QtWidgets.QListWidget() for _ in range(3))
        self.win_list.setToolTip("Fastest measured nodes (fastpref speed index + live ping) — the winners")
        self.lists.addTab(self.win_list, "🏆 fastest"); self.lists.addTab(self.fav_list, "★ promoted")
        self.lists.addTab(self.ban_list, "🚫 banned")
        d.addWidget(self.lists)
        er = QtWidgets.QHBoxLayout()
        self.list_edit = QtWidgets.QLineEdit(); self.list_edit.setPlaceholderText("ip[:port] — or select a row above")
        er.addWidget(self.list_edit, 1)
        for txt, fn, tip in (("★", lambda: self._list_promote(self._edit_addr(), True), "Whitelist: promote (favourite + addnode)"),
                             ("🚫", lambda: self._list_ban(self._edit_addr(), True), "₿lacklist: ban as unreliable (7 days)"),
                             ("✖", self._list_remove, "Remove the selected/typed entry from the current list")):
            b = QtWidgets.QPushButton(txt); b.setFixedWidth(34); b.setToolTip(tip); b.clicked.connect(fn); er.addWidget(b)
        d.addLayout(er)
        return w
    # ---- winners / whitelist / blacklist plumbing ----
    def _edit_addr(self):
        t = self.list_edit.text().strip()
        if t: return t
        cur = self.lists.currentWidget().currentItem()
        return cur.data(QtCore.Qt.UserRole) if cur else ""
    def _fill_lists(self):
        spawn_fn(lambda: fetch_json("/api/node/fastnodes"), self._fill_winners)
        spawn_fn(lambda: fetch_json("/api/node/favourites"), self._fill_favs)
        spawn("listbanned", self._fill_banned, timeout=10)
    def _fill_winners(self, d):
        idx = (d or {}).get("nodes") or d or {}
        rows = sorted((v for v in (idx.values() if isinstance(idx, dict) else idx) if isinstance(v, dict)),
                      key=lambda x: (-(x.get("score") or 0), x.get("pingMs") or 9e9))[:12]
        live = {p.get("addr"): p for p in self._peers or []}
        self.win_list.clear()
        for i, r in enumerate(rows):
            a = r.get("addr", "?"); ping = r.get("pingMs")
            on = "🟢" if a in live else "·"
            it = QtWidgets.QListWidgetItem(f"{'🥇🥈🥉'[i] if i < 3 else f'{i+1:2d}'} {on} {a:<22} {ping if ping is not None else '—':>5} ms  score {r.get('score', 0)}")
            it.setData(QtCore.Qt.UserRole, a)
            if i < 3: it.setForeground(QtGui.QColor("#F7931A"))
            self.win_list.addItem(it)
        if not rows:
            peers = sorted((p for p in self._peers or [] if p.get("minping")), key=lambda p: p["minping"])[:8]
            for i, p in enumerate(peers):
                it = QtWidgets.QListWidgetItem(f"{i+1:2d} 🟢 {p.get('addr',''):<22} {p['minping']*1000:5.0f} ms  (live ping)")
                it.setData(QtCore.Qt.UserRole, p.get("addr")); self.win_list.addItem(it)
            if not peers: self.win_list.addItem("no measurements yet — enable ⚡ prefer fastest (Network tab)")
    def _fill_favs(self, d):
        rows = (d or {}).get("nodes") or []
        self.fav_list.clear()
        for v in sorted(rows, key=lambda r: r.get("addr", "")):
            a = v.get("addr", "")
            it = QtWidgets.QListWidgetItem(f"★ {a:<24} {(v.get('subver') or '').strip('/')[:24]}")
            it.setData(QtCore.Qt.UserRole, a); it.setForeground(QtGui.QColor("#FFD37A")); self.fav_list.addItem(it)
        self.lists.setTabText(1, f"★ promoted ({self.fav_list.count()})")
    def _fill_banned(self, rows, stale=False):
        self.ban_list.clear()
        for b in rows or []:
            a = b.get("address", "")
            until = datetime.fromtimestamp(b.get("banned_until", 0), timezone.utc).strftime("%m-%d %H:%M")
            it = QtWidgets.QListWidgetItem(f"🚫 {a:<22} until {until}")
            it.setData(QtCore.Qt.UserRole, a); it.setForeground(QtGui.QColor("#f85149")); self.ban_list.addItem(it)
        self.lists.setTabText(2, f"🚫 banned ({self.ban_list.count()})")
    def _list_promote(self, addr, on):
        if not addr: return
        spawn_fn(lambda: post_json("/api/node/promote", {"addr": addr, "on": on}),
                 lambda d: (self.diag_status.setText(("★ promoted " if on else "un-promoted ") + addr), self._fill_lists()))
    def _list_ban(self, addr, on):
        if not addr: return
        spawn_fn(lambda: post_json("/api/node/ban", {"addr": addr, "on": on}),
                 lambda d: (self.diag_status.setText((f"🚫 banned {addr} ({(d or {}).get('hours','?')}h)" if on else f"unbanned {addr}")
                                                     if (d or {}).get("ok") else f"ban failed: {(d or {}).get('error')}"),
                            self._fill_lists()))
    def _list_remove(self):
        addr = self._edit_addr()
        if not addr: return
        tab = self.lists.currentIndex()
        if tab == 2: self._list_ban(addr, False)          # banned list → unban
        else: self._list_promote(addr, False)             # promoted (or winners) → un-promote
    def refresh(self):
        spawn("getnetworkinfo", self._setni)
        n = self.maxnodes.value()
        spawn_fn(lambda: known_nodes(max(1, n)) if n else [], self._setknown)
        spawn("getpeerinfo", self._setpeers, timeout=10)
        spawn("getnettotals", self._settot)                                   # global in/out speed HUD
        spawn_fn(lambda: fetch_json("/api/netactivity?n=60"), self._setact)   # log-based fallback (works during the RPC choke)
        spawn_fn(lambda: fetch_json("/api/coremon"),                          # is Core actually off? → onboarding overlay
                 lambda d: self._set_down(bool(d) and d.get("state") == "off"))
        self._fill_lists()                                                    # 🏆 winners · ★ promoted · 🚫 banned
    # ---- zoom ----
    def _zoom(self, f):
        self._user_zoom = True; self.view.scale(f, f)
    def _fit(self):
        self._user_zoom = False; self._fitview()
    def _fitview(self):
        if self.scene.items():
            self.view.fitInView(self.scene.itemsBoundingRect().adjusted(-60, -40, 60, 40), QtCore.Qt.KeepAspectRatio)
    @staticmethod
    def _fmt_rate(bps):
        for unit, div in (("MB/s", 1e6), ("KB/s", 1e3)):
            if bps >= div: return f"{bps / div:.1f} {unit}"
        return f"{bps:.0f} B/s"
    def _settot(self, d, stale):
        if not d: return
        import time
        now = time.monotonic()
        if self._tot_prev:
            pr, ps, pt = self._tot_prev
            dt = max(0.001, now - pt)
            self._tot_rate = (max(0, d.get("totalbytesrecv", 0) - pr) / dt,
                              max(0, d.get("totalbytessent", 0) - ps) / dt)
        self._tot_prev = (d.get("totalbytesrecv", 0), d.get("totalbytessent", 0), now)
        ri, ro = self._tot_rate
        self.speed.setText(f"<span style='color:#F7931A'>⬇ in {self._fmt_rate(ri)}</span> · "
                           f"<span style='color:#16C784'>⬆ out {self._fmt_rate(ro)}</span>")
    def _setni(self, ni, stale): self._ni = ni or {}; self._redraw()
    def _setknown(self, nodes): self._known = nodes or []; self._redraw()
    def _setpeers(self, peers, stale):
        import time
        now = time.monotonic()
        for p in peers or []:                       # per-peer live B/s from deltas between polls
            a = p.get("addr")
            prev = self._prev.get(a)
            if prev:
                dt = max(0.001, now - prev[2])
                self._rates[a] = (max(0, p.get("bytesrecv", 0) - prev[0]) / dt,
                                  max(0, p.get("bytessent", 0) - prev[1]) / dt)
            self._prev[a] = (p.get("bytesrecv", 0), p.get("bytessent", 0), now)
        live = {p.get("addr") for p in peers or []}
        self._rates = {a: r for a, r in self._rates.items() if a in live}
        self._prev = {a: s for a, s in self._prev.items() if a in live}
        self._peers = peers or []; self._pstale = bool(stale)
        if self._sel:                                          # keep the open diagnostics panel live
            fresh = next((p for p in self._peers if p.get("addr") == self._sel.get("addr")), None)
            if fresh: self._sel = fresh; self._fill_diag(fresh)
            else: self.diag_status.setText("(this peer is no longer connected)")
        self._redraw()
    def _setact(self, d): self._act = (d or {}).get("events", []); self._redraw()
    # ---- click-to-select a peer node ----
    def _place_zoom(self):
        x = self.view.width() - 42
        for i, b in enumerate(self._zbtns): b.move(x, 10 + i * 36)
    def eventFilter(self, obj, ev):
        if obj is self.view and ev.type() == QtCore.QEvent.Resize:
            self._place_zoom(); self._place_down()
        if obj is self.view.viewport() and ev.type() == QtCore.QEvent.Wheel:
            self._zoom(1.15 if ev.angleDelta().y() > 0 else 1 / 1.15)
            return True                                     # consume: wheel = zoom, not scroll
        if obj is self.view.viewport() and ev.type() == QtCore.QEvent.MouseButtonPress:
            sp = self.view.mapToScene(ev.position().toPoint()) if hasattr(ev, "position") else self.view.mapToScene(ev.pos())
            best, bestd = None, 1e9
            for (x, y, r, p) in self._hits:
                dd = (sp.x() - x) ** 2 + (sp.y() - y) ** 2
                if dd <= (r + 10) ** 2 and dd < bestd: best, bestd = p, dd
            if best: self._select(best)
            else: self._sel = None; self.diag_title.setText("◎ click a peer node for diagnostics"); \
                self._clear_form(); self.btn_promote.setEnabled(False); self.btn_boot.setEnabled(False); self.btn_ban.setEnabled(False); self.btn_explore.setEnabled(False); self.diag_status.setText("")
            return False
        if obj is self.view.viewport() and ev.type() == QtCore.QEvent.MouseButtonDblClick:
            sp = self.view.mapToScene(ev.position().toPoint()) if hasattr(ev, "position") else self.view.mapToScene(ev.pos())
            for (x, y, r, p) in self._hits:
                if (sp.x() - x) ** 2 + (sp.y() - y) ** 2 <= (r + 10) ** 2:
                    self._enter_explore(p); return True
            if self._explore: self._exit_explore(); return True
        return super().eventFilter(obj, ev)
    def _clear_form(self):
        while self.diag_form.rowCount(): self.diag_form.removeRow(0)
    def _fill_diag(self, p):
        self._clear_form()
        for label, val in peer_diag_lines(p):
            lv = QtWidgets.QLabel(str(val)); lv.setStyleSheet("color:#d6e3ef;font-family:monospace")
            lv.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse); lv.setWordWrap(True)
            self.diag_form.addRow(label + ":", lv)
    def select_addr(self, addr):
        """Cross-link entry (ICE forensics → map): select + highlight a peer by address.
        Returns True if the peer is currently connected and now selected."""
        p = next((q for q in (self._peers or []) if q.get("addr") == addr), None)
        if p:
            self._select(p)
            return True
        self.diag_status.setText(f"{addr} is not currently connected")
        return False
    def _select(self, p):
        self._sel = p
        self.diag_title.setText("◎ " + (p.get("addr", "—")))
        self._fill_diag(p)
        self.btn_promote.setEnabled(True); self.btn_boot.setEnabled(True); self.btn_ban.setEnabled(True); self.btn_explore.setEnabled(True)
        self.btn_promote.setText("★ Promoted" if p.get("addnode") else "★ Promote")
        self.diag_status.setText("Promote = favourite + persistent · ₿oot = disconnect now")
        self._redraw()
    def _act_peer(self, kind):
        if not self._sel: return
        addr = self._sel.get("addr", "")
        self.diag_status.setText(f"{kind} {addr} …")
        peer_action(kind, addr, on_done=lambda d: self._acted(kind, d), subver=self._sel.get("subver", ""))
    def _acted(self, kind, d):
        if d and d.get("ok"):
            self.diag_status.setText(f"✓ {kind} done — {d.get('addr', '')}")
            if kind == "boot": self._sel = None; self.btn_promote.setEnabled(False); self.btn_boot.setEnabled(False); self.btn_ban.setEnabled(False); self.btn_explore.setEnabled(False)
            QtCore.QTimer.singleShot(700, self.refresh)
        else:
            self.diag_status.setText(f"✗ {(d or {}).get('error', 'failed')}")
    def _pulse(self):
        # DYNAMIC overlay: traffic 'packets' flow centre→peer along each link, + a halo on the
        # selected node. Cheap — only the overlay is rebuilt each tick; the base scene is static.
        if not anim_on(self):
            return    # THERMAL: don't animate (60ms) when this tab is hidden/minimized — saves CPU on the HD 3000
        for it in self._anim:
            try: self.scene.removeItem(it)
            except Exception: pass
        self._anim = []
        if self._explore:                      # STABLE 3D rotation: items persist, only positions move
            if getattr(self, "_cloud_items", None):
                # hold the rotation still while the user is dragging/panning the view —
                # inspecting a node shouldn't be a moving target
                _dragging = bool(QtWidgets.QApplication.mouseButtons() & QtCore.Qt.LeftButton) \
                    and self.view.underMouse()
                if not _dragging:
                    self._cloud_rot = getattr(self, "_cloud_rot", 0.0) + 0.010
                self._layout_cloud()
            # PACKET FLOW in the 3D view too — along the one REAL link (us ↔ the explored peer),
            # driven by that peer's live measured B/s. orange = data INTO the ₿ANKON node
            # (peer→us), green = data OUT (us→peer). Gossip-cluster lines carry no dots: honest.
            ax, ay = getattr(self, "_explore_anchor", (None, None))
            if ax is not None:
                self._phase += 0.05
                ri, ro = self._rates.get((self._explore or {}).get("addr") or "", (0.0, 0.0))
                pin, pout = self._flow_frac(ri), self._flow_frac(ro)
                ORANGE = QtGui.QColor("#F7931A"); GREEN = QtGui.QColor("#16C784")
                if pin > 0:            # centre (peer) → ₿ANKON anchor
                    npk = 1 + int(round(2 * pin)); spd = 0.6 + 2.4 * pin; rad = 2.0 + 3.0 * pin
                    for j in range(npk):
                        t = (self._phase * spd + j / npk) % 1.0
                        self._anim.append(self.scene.addEllipse(ax * t - rad, ay * t - rad, 2 * rad, 2 * rad,
                                                                QtGui.QPen(QtCore.Qt.NoPen), QtGui.QBrush(ORANGE)))
                if pout > 0:           # ₿ANKON anchor → centre (peer)
                    npk = 1 + int(round(2 * pout)); spd = 0.6 + 2.4 * pout; rad = 2.0 + 3.0 * pout
                    for j in range(npk):
                        t = (self._phase * spd + j / npk + 0.5 / max(1, npk)) % 1.0
                        self._anim.append(self.scene.addEllipse(ax * (1 - t) - rad, ay * (1 - t) - rad,
                                                                2 * rad, 2 * rad,
                                                                QtGui.QPen(QtCore.Qt.NoPen), QtGui.QBrush(GREEN)))
                lbl = getattr(self, "_explore_ratelbl", None)
                if lbl is not None:
                    try:
                        lbl.setText(f"▼ {self._rate_s(ri)}   ▲ {self._rate_s(ro)}"
                                    if (ri or ro) else "link idle")
                    except RuntimeError:
                        pass                                     # scene rebuilt under us
            return
        if not self._links and not self._sel: return
        self._phase += 0.05                                        # unbounded so per-link speeds stay smooth
        import math
        ORANGE = QtGui.QColor("#F7931A"); GREEN = QtGui.QColor("#16C784")
        # Packets = ACTUAL traffic measured between the last two peer polls (B/s deltas).
        # A direction with zero live traffic shows no dots — the map only flows when data flows.
        c0x, c0y = getattr(self, "_c0", (0.0, 0.0))
        # COMET packets (improvement fed back from the globe): a bright head with a fading
        # tail laid exactly against the direction of travel — orange comets visibly STREAM
        # INTO the centre, green ones stream OUT. Link QUALITY (measured ping) sets the
        # comet's brightness and tail crispness: fast link = crisp and bright, slow = dim.
        for k, ln in enumerate(self._links):
            x, y, pin, pout = ln[0], ln[1], ln[2], ln[3]
            q = ln[4] if len(ln) > 4 else 0.6
            dx, dy = x - c0x, y - c0y
            L = math.hypot(dx, dy) or 1.0
            ux, uy = dx / L, dy / L                            # unit vector centre → peer
            for frac, col, inbound in ((pin, ORANGE, True), (pout, GREEN, False)):
                if frac <= 0: continue
                npk = 1 + int(round(2 * frac)); spd = 0.6 + 2.4 * frac; rad = 2.0 + 3.0 * frac
                alpha = int(110 + 145 * q)
                tlen = (8 + 18 * frac) * (0.55 + 0.45 * q)     # quality link = tighter tail
                bdir = 1.0 if inbound else -1.0                # tail points BEHIND the head
                for j in range(npk):
                    t = (self._phase * spd + j / npk + k * (0.13 if inbound else 0.17)) % 1.0
                    tt = 1.0 - t if inbound else t             # inbound heads run peer → centre
                    px, py = c0x + dx * tt, c0y + dy * tt
                    mx_, my_ = px + ux * tlen * 0.45 * bdir, py + uy * tlen * 0.45 * bdir
                    tx, ty = px + ux * tlen * bdir, py + uy * tlen * bdir
                    self._anim.append(self.scene.addLine(px, py, mx_, my_,
                        QtGui.QPen(QtGui.QColor(col.red(), col.green(), col.blue(), int(alpha * 0.50)), rad * 0.9)))
                    self._anim.append(self.scene.addLine(mx_, my_, tx, ty,
                        QtGui.QPen(QtGui.QColor(col.red(), col.green(), col.blue(), int(alpha * 0.20)), rad * 0.5)))
                    self._anim.append(self.scene.addEllipse(px - rad, py - rad, 2 * rad, 2 * rad,
                        QtGui.QPen(QtCore.Qt.NoPen),
                        QtGui.QBrush(QtGui.QColor(col.red(), col.green(), col.blue(), alpha))))
        if self._sel:                                              # pulsing selection halo
            sx = sy = None
            for (x, y, r, p) in self._hits:
                if p.get("addr") == self._sel.get("addr"): sx, sy, sr = x, y, r; break
            if sx is not None:
                hr = sr + 6 + 4 * math.sin(self._phase * 2 * math.pi)
                ring = self.scene.addEllipse(sx - hr, sy - hr, 2 * hr, 2 * hr, QtGui.QPen(QtGui.QColor("#FFD37A"), 2.5))
                self._anim.append(ring)
    # ---- 3D cluster explore: double-click a peer → its network neighbourhood as a pointcloud ----
    def _enter_explore(self, peer):
        import math
        # "3D doesn't toggle" guard: the pointcloud is built from the addrman cloud — if that
        # hasn't arrived yet (tab just opened / RPC was busy), fetch it NOW and re-enter, so
        # the toggle always visibly does something instead of drawing an empty universe
        if not self._known:
            self.info.setText("Explore — fetching the addrman cloud for this neighbourhood…")
            self._explore = peer
            self.backbtn.show(); self.backbtn.raise_()
            spawn_fn(lambda: known_nodes(max(1, self.maxnodes.value() or 2000)),
                     lambda nodes: (setattr(self, "_known", nodes or []),
                                    self._enter_explore(peer) if self._known else
                                    self.info.setText("Explore — addrman unavailable (node RPC busy); try again shortly")))
            return
        ip = (peer.get("addr") or "").rsplit(":", 1)[0].strip("[]")
        base = next((nd for nd in self._known or [] if nd.get("ip") == ip), None)
        a = base.get("asn") if base else None
        pre = ".".join(ip.split(".")[:2]) if "." in ip else None
        hood = [nd for nd in (self._known or [])
                if nd.get("ip") != ip and ((a and nd.get("asn") == a) or (pre and nd.get("ip", "").startswith(pre + ".")))]
        if len(hood) < 12:                          # sparse neighbourhood -> widen to a general gossip sample
            hood = [nd for nd in (self._known or []) if nd.get("ip") != ip][:120]
        hood = hood[:150]
        ga = math.pi * (3 - math.sqrt(5))           # golden-angle sphere: even 3D distribution
        n = max(1, len(hood))
        self._cloud = []
        for i, nd in enumerate(hood):
            zz = 1 - 2 * (i + 0.5) / n
            rr = math.sqrt(max(0.0, 1 - zz * zz))
            th = ga * i
            R = 170 + (i % 5) * 14                  # shells give the cloud volume
            self._cloud.append((rr * math.cos(th) * R, zz * R * 0.72, rr * math.sin(th) * R,
                                nd.get("ip", ""), nd.get("org") or ""))
        self._explore = peer
        self._cloud_rot = 0.0
        self.backbtn.show(); self.backbtn.raise_()
        self._redraw()
    def _layout_cloud(self):
        # rotate around the vertical axis and restyle by depth — pure moves on persistent items
        import math
        rot = getattr(self, "_cloud_rot", 0.0)
        ORANGE, BLUE = QtGui.QColor(247, 147, 26), QtGui.QColor(0, 191, 255)
        front = []
        for i, (x3, y3, z3, ip, org) in enumerate(self._cloud):
            e, tx, ln = self._cloud_items[i]
            xr = x3 * math.cos(rot) - z3 * math.sin(rot)
            zr = x3 * math.sin(rot) + z3 * math.cos(rot)
            depth = max(0.0, min(1.0, (zr / 260 + 1) / 2))
            sz = 1.5 + 4.5 * depth                    # nearer = bigger + brighter (the 3D cue)
            col = QtGui.QColor(ORANGE if depth > 0.62 else BLUE)
            col.setAlpha(int(60 + 190 * depth))
            e.setRect(xr - sz, y3 - sz, 2 * sz, 2 * sz)
            e.setBrush(col); e.setZValue(depth)
            if ln:
                ln.setLine(0, 0, xr, y3)
                ln.setPen(QtGui.QPen(QtGui.QColor(90, 150, 180, int(18 + 48 * depth)), 1))
            front.append((depth, xr, y3, tx))
        # ADDRESSES: label the front-facing nodes (nearest 14) — the rest stay hover-tooltips
        front.sort(key=lambda q: -q[0])
        for j, (depth, x, y, tx) in enumerate(front):
            if j < 14 and depth > 0.55:
                tx.setPos(x + 7, y - 6); tx.setZValue(2); tx.setOpacity(0.35 + 0.65 * depth); tx.setVisible(True)
            else:
                tx.setVisible(False)
    def _exit_explore(self):
        # return to the DEFAULT presentation: local node -> connected peers with live traffic
        self._explore = None; self._cloud = []; self._cloud_items = []
        self.backbtn.hide()
        self._redraw()
    # ---- down-state onboarding (advertising moment when Core is off) ----
    def _start_core(self):
        self.downmsg.setText("starting ₿itcoin Core…")
        spawn_fn(lambda: post_json("/api/node/start", {}, timeout=12),
                 lambda d: self.downmsg.setText("₿itcoin Core is starting — the map fills in as peers connect."
                           if d and d.get("ok") else "start failed: " + str((d or {}).get("error", "?"))),
                 lambda e: self.downmsg.setText(f"start failed: {e}"))
    def _open_doc(self, which):
        urls = {"faq": "http://127.0.0.1:8088/#faq",
                "docs": "http://127.0.0.1:8088/docs/introduction.md",
                "cypherpunk": "https://github.com/cypherpunk2048",
                "gnugui": "https://github.com/gnugui"}
        try: webbrowser.open(urls.get(which, urls["docs"]))
        except Exception: pass
    def _place_down(self):
        if not self.downpanel.isVisible(): return
        self.downpanel.adjustSize()
        w, h = self.downpanel.width(), self.downpanel.height()
        self.downpanel.move(max(0, (self.view.width() - w) // 2), max(0, (self.view.height() - h) // 2))
    def _set_down(self, off):
        self._core_off = off
        if off and not self.downpanel.isVisible():
            self.downmsg.setText("₿itcoin Core is not running — ₿ANKON attaches to your node.\n"
                                 "Start Core to map the network.")
            self.downpanel.show(); self.downpanel.raise_(); self._place_down()
        elif not off and self.downpanel.isVisible():
            self.downpanel.hide()
    def _bankon_name(self):
        """The centre node's ACCURATE display name. Priority: (1) Core's own advertised
        localaddresses; (2) the address our peers report they see us as (addrlocal majority —
        the real outside view); (3) honest 'not publicly reachable'."""
        la = (self._ni or {}).get("localaddresses") or []
        if la:
            return f"₿ANKON node\n{la[0].get('address', '')}:{la[0].get('port', '')}"
        from collections import Counter
        seen = Counter((p.get("addrlocal") or "").rsplit(":", 1)[0].strip("[]")
                       for p in (self._peers or []) if p.get("addrlocal"))
        if seen:
            ip, votes = seen.most_common(1)[0]
            return f"₿ANKON node\n{ip}  (as {votes} peer{'s' if votes != 1 else ''} see us)"
        sub = ((self._ni or {}).get("subversion", "") or "").strip("/")
        return f"₿ANKON node\n{sub or 'local'} · not publicly reachable"

    @staticmethod
    def _flow_frac(bps):
        # ABSOLUTE flow scale (log): 0 at idle → 1 at ~1 MB/s. Packet density/speed now correlate
        # with the real byte rate on that link — not with "share of the busiest peer", which made
        # one hot peer hide everyone else's live traffic (and a lone idle peer look maxed out).
        import math
        return 0.0 if bps <= 0 else min(1.0, math.log10(1.0 + bps) / 6.0)

    @staticmethod
    def _rate_s(bps):
        if bps >= 1048576: return f"{bps / 1048576:.1f} MB/s"
        if bps >= 1024: return f"{bps / 1024:.1f} KB/s"
        return f"{bps:.0f} B/s"
    @staticmethod
    def _traffic_color(frac):
        # EtherApe-style spectrum: blue (idle) → green (busy) → orange (hot)
        frac = max(0.0, min(1.0, frac))
        if frac < 0.5:
            t = frac / 0.5; return QtGui.QColor(int(0 + t * 22), int(191 + t * 8), int(255 - t * 123))   # #00BFFF→#16C784
        t = (frac - 0.5) / 0.5; return QtGui.QColor(int(22 + t * 225), int(199 - t * 52), int(132 - t * 106))  # #16C784→#F7931A
    def _redraw(self):
        import math
        self.scene.clear(); self._anim = []; self._hits = []; self._links = []   # clear() frees overlay items too
        if self._explore:                      # ── 3D CLUSTER EXPLORE: selected node at centre ──
            p = self._explore
            self.scene.addEllipse(-26, -26, 52, 52, QtGui.QPen(QtGui.QColor("#F7931A"), 3), QtGui.QBrush(QtGui.QColor("#1a1200")))
            t = self.scene.addText((p.get("addr") or "?") + "\n" + (p.get("subver") or "").replace("/", ""))
            t.setDefaultTextColor(QtGui.QColor("#F7931A")); t.setScale(0.85)
            t.setPos(-t.boundingRect().width() * 0.85 / 2, 30)
            # our node anchors the ONE link in this view that is real measured traffic (the live
            # peer connection) — the cluster lines are gossip-inferred and stay flow-free, honestly.
            ax, ay = -285, 225
            self._explore_anchor = (ax, ay)
            self.scene.addLine(ax, ay, 0, 0, QtGui.QPen(QtGui.QColor(247, 147, 26, 60), 1.4))
            self.scene.addEllipse(ax - 13, ay - 13, 26, 26,
                                  QtGui.QPen(QtGui.QColor("#F7931A"), 2.5), QtGui.QBrush(QtGui.QColor("#1a1200")))
            at = self.scene.addSimpleText("₿ANKON node")
            at.setBrush(QtGui.QColor("#F7931A")); at.setScale(0.8)
            at.setPos(ax - at.boundingRect().width() * 0.8 / 2, ay + 16)
            self._explore_ratelbl = self.scene.addSimpleText("")
            self._explore_ratelbl.setBrush(QtGui.QColor("#8aa0b4")); self._explore_ratelbl.setScale(0.72)
            self._explore_ratelbl.setPos(ax * 0.5 - 40, ay * 0.5 + 8)
            # honest label: Core doesn't expose a remote node's peer list — this is its gossip cluster
            self.info.setText(f"Explore — network neighbourhood of {p.get('addr')} · {len(self._cloud)} known nodes "
                              f"near it (same AS / prefix, from our addrman gossip; peers' own connection lists are "
                              f"not public) · rotating 3D cluster · double-click empty space or ⬅ to return")
            # persistent cloud items — created ONCE here; _pulse only moves them (no blink)
            self._cloud_items = []
            for i, (x3, y3, z3, ip, org) in enumerate(self._cloud):
                ln = self.scene.addLine(0, 0, 0, 0, QtGui.QPen(QtGui.QColor(90, 150, 180, 30), 1)) if i % 3 == 0 else None
                e = self.scene.addEllipse(-3, -3, 6, 6, QtGui.QPen(QtCore.Qt.NoPen), QtGui.QBrush(QtGui.QColor(0, 191, 255, 120)))
                e.setToolTip(f"{ip}\n{org}" if org else ip)          # every node's address on hover
                tx = self.scene.addSimpleText(ip)
                tx.setBrush(QtGui.QColor(207, 227, 242)); tx.setScale(0.72); tx.setVisible(False)
                self._cloud_items.append((e, tx, ln))
            self._layout_cloud()                                      # position immediately — no blank frame
            if not self._user_zoom:
                self.view.fitInView(QtCore.QRectF(-300, -260, 600, 520), QtCore.Qt.KeepAspectRatio)
            return
        peers = self._peers or []; stale = self._pstale; n = max(1, len(peers)); R = 250
        pyramid = getattr(self, "_layout", "radial") == "pyramid"
        # centre of the topology: origin for radial; the PYRAMID APEX (top) in pyramid mode
        c0x, c0y = (0.0, -300.0) if pyramid else (0.0, 0.0)
        self._c0 = (c0x, c0y)
        # peer positions per layout: radial ring, or pyramid tiers (widening rows below the apex,
        # busiest peers nearest the top so rank-in-the-pyramid means something)
        pos = []
        tiers = []          # pyramid: [(row_y, [peer idx])] for separators + per-tier legend
        if pyramid:
            order = sorted(range(len(peers)),
                           key=lambda i: -(peers[i].get('bytesrecv', 0) + peers[i].get('bytessent', 0)))
            row, row_len, k = 0, 2, 0
            slots, rowno = [], []
            while k < len(peers):
                take = min(row_len, len(peers) - k)
                for j in range(take):
                    w = 150 * (row + 1)
                    xx = -w / 2 + (j + 0.5) * (w / take) if take > 1 else 0.0
                    slots.append((xx, c0y + 150 + row * 130)); rowno.append(row)
                k += take; row += 1; row_len += 2
            pos = [None] * len(peers)
            rows = {}
            for rank, idx in enumerate(order):
                pos[idx] = slots[rank]
                rows.setdefault(rowno[rank], []).append(idx)
            tiers = [(c0y + 150 + rn * 130, idxs) for rn, idxs in sorted(rows.items())]
        else:
            # EtherApe-style ranking on the ring: busiest peer at 12 o'clock, then clockwise
            # by traffic rank — position now carries meaning instead of arrival order
            order = sorted(range(len(peers)),
                           key=lambda i: -(peers[i].get('bytesrecv', 0) + peers[i].get('bytessent', 0)))
            pos = [None] * len(peers)
            for rank, idx in enumerate(order):
                ang = -math.pi / 2 + 2 * math.pi * rank / n
                pos[idx] = (R * math.cos(ang), R * math.sin(ang))
        # faint outer cloud = every node our addrman knows (the "all nodes" backdrop)
        if self._known:
            step = max(1, len(self._known) // 240)
            CR = 360
            for i, _nd in enumerate(self._known[::step]):
                if pyramid:      # the wider network forms the pyramid's BASE layer
                    gx = (i * 0.61803398875 % 1.0)
                    cx = -430 + 860 * gx
                    cy = c0y + 170 + (len(pos) and max(py for _px, py in pos) - c0y or 300) + 60 + (i % 7) * 12
                else:
                    ang = 2 * math.pi * (i * 0.61803398875 % 1.0)   # golden-angle scatter
                    rr = CR + (i % 7) * 6
                    cx, cy = rr * math.cos(ang), rr * math.sin(ang)
                self.scene.addEllipse(cx - 1.3, cy - 1.3, 2.6, 2.6, QtGui.QPen(QtCore.Qt.NoPen), QtGui.QBrush(QtGui.QColor(90, 150, 180, 90)))
        # pyramid tier separators + per-tier legend: each row is a traffic rank band — label it
        for _ti, (ty, idxs) in enumerate(tiers):
            tw2 = 150 * (_ti + 1) / 2 + 90
            _sep = self.scene.addLine(-tw2, ty + 58, tw2, ty + 58, QtGui.QPen(QtGui.QColor(20, 64, 92, 110), 1, QtCore.Qt.DashLine))
            _tot = sum(peers[i].get("bytesrecv", 0) + peers[i].get("bytessent", 0) for i in idxs)
            _lt = self.scene.addSimpleText(f"tier {_ti + 1} · {len(idxs)} peers · Σ {_tot / 1048576:.1f} MiB")
            _lt.setBrush(QtGui.QColor("#5a6b7b")); _lt.setScale(0.7); _lt.setPos(tw2 + 8, ty - 6)
        # our node — accurate name + LIVE aggregate throughput (getnettotals deltas): the orange ▼
        # figure is exactly what flows INTO this node, green ▲ exactly what leaves. At the pyramid
        # APEX in pyramid mode, centre otherwise.
        self.scene.addEllipse(c0x - 30, c0y - 30, 60, 60, QtGui.QPen(QtGui.QColor("#F7931A"), 3), QtGui.QBrush(QtGui.QColor("#1a1200")))
        t = self.scene.addText(self._bankon_name()); t.setDefaultTextColor(QtGui.QColor("#F7931A"))
        t.setScale(0.95)
        t.setPos(c0x - t.boundingRect().width() * 0.95 / 2, c0y - 30 - t.boundingRect().height() * 0.95 if pyramid else c0y + 32)
        tin, tout = self._tot_rate or (0.0, 0.0)
        ry = (c0y + 34) if pyramid else (c0y + 32 + t.boundingRect().height() * 0.95)
        ti = self.scene.addSimpleText(f"▼ in  {self._rate_s(tin)}")
        ti.setBrush(QtGui.QColor("#F7931A")); ti.setScale(0.85)
        ti.setPos(c0x - ti.boundingRect().width() * 0.85 - 4, ry)
        to = self.scene.addSimpleText(f"▲ out {self._rate_s(tout)}")
        to.setBrush(QtGui.QColor("#16C784")); to.setScale(0.85); to.setPos(c0x + 4, ry)
        maxt = max([(p.get('bytessent', 0) + p.get('bytesrecv', 0)) for p in peers] or [1]) or 1
        maxr = max([p.get('bytesrecv', 0) for p in peers] or [1]) or 1
        maxs = max([p.get('bytessent', 0) for p in peers] or [1]) or 1
        for i, p in enumerate(peers):
            x, y = pos[i]
            dx, dy = x - c0x, y - c0y
            dl = math.hypot(dx, dy) or 1.0
            traf = p.get('bytessent', 0) + p.get('bytesrecv', 0); frac = traf / maxt
            col = self._traffic_color(frac)
            inbound = p.get('inbound')
            # directional lanes: IN data = bitcoin orange, OUT data = candle green (width ∝ share)
            fin = p.get('bytesrecv', 0) / maxr; fout = p.get('bytessent', 0) / maxs
            ox, oy = -dy / dl * 2.2, dx / dl * 2.2                  # perpendicular lane offset
            self.scene.addLine(c0x + ox, c0y + oy, x + ox, y + oy, QtGui.QPen(QtGui.QColor(247, 147, 26, 190), 1 + 5 * fin))
            self.scene.addLine(c0x - ox, c0y - oy, x - ox, y - oy, QtGui.QPen(QtGui.QColor(22, 199, 132, 190), 1 + 5 * fout))
            # packets carry only ACTUAL live traffic: per-direction B/s measured between polls,
            # on an ABSOLUTE log scale — dot density/speed correlate with this link's real rate.
            ri, ro = self._rates.get(p.get("addr"), (0.0, 0.0))
            pin = self._flow_frac(ri)                # incoming from this external node right now
            pout = self._flow_frac(ro)               # outgoing from ₿ANKON node right now
            # live per-link rate labels on active links (midpoint, direction-coloured)
            mx, my = c0x + dx * 0.55, c0y + dy * 0.55
            if ri >= 256:
                lr = self.scene.addSimpleText("▼ " + self._rate_s(ri))
                lr.setBrush(QtGui.QColor("#F7931A")); lr.setScale(0.68)
                lr.setPos(mx + ox * 4 - 20, my + oy * 4 - 12)
            if ro >= 256:
                ls = self.scene.addSimpleText("▲ " + self._rate_s(ro))
                ls.setBrush(QtGui.QColor("#16C784")); ls.setScale(0.68)
                ls.setPos(mx - ox * 4 - 20, my - oy * 4 + 4)
            # EtherApe idiom: node DIAMETER breathes with LIVE B/s (log scale), lifetime share
            # keeps a floor so historically-busy peers stay visible between bursts
            r = 7 + 6 * frac + 8 * max(pin, pout)
            selected = bool(self._sel) and p.get("addr") == self._sel.get("addr")
            promoted = bool(p.get("addnode"))
            if promoted: pen = QtGui.QPen(QtGui.QColor("#FFD37A"), 3)          # ★ favourite = gold ring
            elif inbound: pen = QtGui.QPen(QtGui.QColor("#F7931A"), 2)         # inbound = orange ring
            else: pen = QtGui.QPen(QtGui.QColor("#14405c"), 1)
            self.scene.addEllipse(x - r, y - r, 2 * r, 2 * r, pen, QtGui.QBrush(col))
            self._hits.append((x, y, r, p)); self._links.append((x, y, pin, pout, link_quality(p)))
            lbl = self.scene.addText(p.get('addr', '')[:24] + "\n" + p.get('subver', '').replace('/', ''))
            lbl.setDefaultTextColor(QtGui.QColor("#FFD37A") if selected else QtGui.QColor("#d6e3ef")); lbl.setScale(0.75)
            if pyramid:
                lbl.setPos(x - lbl.boundingRect().width() * 0.75 / 2, y + r + 2)
            else:
                lbl.setPos(x + (12 if dx >= 0 else -110), y - 8)
        # Log-based connection ACTIVITY ring — shows the node dialing peers even when getpeerinfo is
        # RPC-choked, so the map is never empty during IBD. connected=green · failed=red · inbound=blue.
        acts = [e for e in (self._act or []) if e.get("kind") in ("connected", "failed", "inbound", "disconnect")]
        if acts and len(peers) < 4:
            AC = {"connected": "#16C784", "failed": "#f85149", "inbound": "#00BFFF", "disconnect": "#F7931A"}
            for i, e in enumerate(acts[-40:]):
                ang = 2 * math.pi * (i * 0.61803398875 % 1.0); rr = 150 + (i % 6) * 10
                c0x, c0y = getattr(self, "_c0", (0.0, 0.0))
                x, y = c0x + rr * math.cos(ang), c0y + rr * math.sin(ang); col = QtGui.QColor(AC.get(e.get("kind"), "#8aa0b4"))
                self.scene.addLine(c0x, c0y, x, y, QtGui.QPen(QtGui.QColor(col.red(), col.green(), col.blue(), 70), 1))
                self.scene.addEllipse(x - 4, y - 4, 8, 8, QtGui.QPen(QtCore.Qt.NoPen), QtGui.QBrush(col))
                lab = e.get("addr") or (("peer=" + e["peer"]) if e.get("peer") else "")
                if lab:
                    t = self.scene.addText(lab); t.setDefaultTextColor(col); t.setScale(0.6); t.setPos(x + 6, y - 8)
        if not self._user_zoom: self._fitview()
        if peers:
            # honest subsample: the backdrop draws at most ~240 of the known nodes — say so,
            # and say the positions are topology, not geography
            _kn = len(self._known or [])
            drawn = -(-_kn // max(1, _kn // 240)) if _kn else 0   # exact count the [::step] slice draws
            cloud = (f"cloud shows {drawn} of {len(self._known):,} known" if self._known else "no addrman data yet")
            self.info.setText(f"Network map (topology view — positions are not geographic) — {len(peers)} peers · {cloud} · "
                              f"orange comets = live data IN (peer→node) · green comets = data OUT · "
                              f"brightness = link quality (ping) · "
                              f"gold ring = ★favourite · wheel = zoom · click a node{' · cached' if stale else ''}")
        elif acts:
            nc = sum(1 for e in acts if e['kind'] == 'connected'); nf = sum(1 for e in acts if e['kind'] == 'failed')
            self.info.setText(f"Network map — peer RPC busy (IBD); showing live connection activity from the log: "
                              f"{nc} connected · {nf} failed (centre = your node)")
        else:
            self.info.setText("Network map — waiting for the node (RPC busy during verification/IBD)")
    def resizeEvent(self, e):
        if not self._user_zoom: self._fitview()
        super().resizeEvent(e)


class GlobeWidget(QtWidgets.QWidget):
    """Spinning orthographic globe drawn with pure QPainter (raster on CPU — works under
    QT_OPENGL=software, no GPU). WGS84 unit-sphere positions, rotated about the polar axis
    each frame; only the front hemisphere is drawn. Shows the known-node cloud, connected
    peers, and great-circle arcs to our node."""
    def __init__(self):
        super().__init__()
        self.setMinimumSize(360, 360)
        self.setStyleSheet("background:#05080d;border:2px solid #00BFFF;border-radius:8px")
        self.setCursor(QtCore.Qt.OpenHandCursor)
        self.spin = 0.0
        self.base_tilt = math.radians(20)
        self.view_tilt = self.base_tilt        # vertical drag changes this (view latitude)
        self.auto = 0.35                       # auto-spin deg/frame (signed: + = east, − = west, 0 = stop)
        self.zoom = 1.0
        self._drag = None; self._vel = 0.0     # hand-drag state + fling inertia (deg/frame)
        self._nodes, self._peers, self._arcs, self._my = [], [], [], None
        self._fphase = 0.0                     # packet-flow phase (comets riding the arcs)
        self._hits = []                        # hover targets: (x, y, r, peer dict) per frame
        self._hover_pk = None                  # the dict under the cursor (gold-ringed)
        self.my_tip = ""                       # our node's hover card (set by GeoMapTab)
        self.setMouseTracking(True)            # hover cards without a button held
        _st = QtCore.QSettings("BANKON", "bankon-qt")
        self.show_borders = _st.value("geomap/borders", "true") == "true"
        self.show_acc = _st.value("geomap/accuracy", "true") == "true"
        from services.earth import earth_texture
        self._tex = earth_texture()            # real Blue Marble (numpy HxWx3) or None → vector fallback
        self._th, self._tw = (self._tex.shape[0], self._tex.shape[1]) if self._tex is not None else (0, 0)
        self._mkey = None                      # cache key for the spin-independent projection map
        self._t = QtCore.QTimer(self); self._t.timeout.connect(self._tick); self._t.start(40)  # ~25 fps
    def _tick(self):
        if not anim_on(self): return           # THERMAL: no 25 fps spin while hidden/minimized
        if self._drag is None:                 # auto-spin + inertia only when not hand-dragging
            self.spin = (self.spin + self.auto + self._vel) % 360
            self._vel *= 0.92
            if abs(self._vel) < 0.01: self._vel = 0.0
        self._fphase += 0.03                   # packet flow keeps moving even when spin is held
        self.update()
    # --- interaction (learned from QGlobe / Qt_Globe_Engine / Marble): grab to rotate,
    #     wheel to zoom, inertial fling on release; pure-QPainter so it works software-rendered ---
    def mousePressEvent(self, e):
        self._drag = e.position(); self._vel = 0.0; self.setCursor(QtCore.Qt.ClosedHandCursor)
    def mouseMoveEvent(self, e):
        if self._drag is None:
            # HOVER: the fixed points answer with their ACTUAL data — ip · location ·
            # accuracy · speed (ping + live ▼/▲ B/s) · traffic · direction
            p = e.position(); hit = None
            for (hx, hy, hr, pk) in self._hits:
                if (p.x() - hx) ** 2 + (p.y() - hy) ** 2 <= (hr + 5) ** 2:
                    hit = pk; break
            self._hover_pk = hit
            if hit and hit.get("tip"):
                QtWidgets.QToolTip.showText(e.globalPosition().toPoint(), hit["tip"], self)
            elif not hit:
                QtWidgets.QToolTip.hideText()
            self.setCursor(QtCore.Qt.PointingHandCursor if hit else QtCore.Qt.OpenHandCursor)
            return
        p = e.position(); dx = p.x() - self._drag.x(); dy = p.y() - self._drag.y(); self._drag = p
        self.spin = (self.spin - dx * 0.45) % 360                          # drag right → globe follows the hand
        lim = math.radians(85)
        self.view_tilt = max(-lim, min(lim, self.view_tilt + dy * 0.005))  # vertical drag → view latitude
        self._vel = -dx * 0.45                                             # remember velocity → fling
        self.update()
    def mouseReleaseEvent(self, e):
        self._drag = None; self.setCursor(QtCore.Qt.OpenHandCursor)
    def wheelEvent(self, e):
        d = e.angleDelta().y()
        self.zoom = max(0.6, min(3.5, self.zoom * (1.12 if d > 0 else 0.89))); self.update()
    def set_auto_speed(self, dpf): self.auto = dpf          # signed deg/frame from the spin slider
    def reset_view(self):
        self.view_tilt = self.base_tilt; self.zoom = 1.0; self._vel = 0.0; self.update()
    def set_data(self, nodes, peers, my):
        self._nodes_total = len(nodes)         # full count, so captions can be honest about the cut
        self._nodes = nodes[:700]              # subsample the cloud for smooth spin
        self._peers = peers                    # list of dicts — see GeoMapTab._redraw for the keys
        self._my = my
        self._arcs = [great_circle_points(my[0], my[1], p["lat"], p["lon"], 36) for p in peers] if my else []
        self.update()
    def _proj(self, lat, lon, cx, cy, R):
        p = math.radians(lat); l = math.radians(lon + self.spin)
        x = math.cos(p) * math.cos(l); y = math.sin(p); z = math.cos(p) * math.sin(l)
        ct, st = math.cos(self.view_tilt), math.sin(self.view_tilt)
        y2 = y * ct - z * st; z2 = y * st + z * ct        # tilt about X (drag-controlled)
        return cx + R * x, cy - R * y2, z2 > 0             # visible if facing viewer
    def _polyline(self, qp, pts):
        prev = None
        for cur in pts:
            if prev is not None and prev[2] and cur[2]:
                qp.drawLine(QtCore.QPointF(prev[0], prev[1]), QtCore.QPointF(cur[0], cur[1]))
            prev = cur
    def _flow_arc(self, qp, pts, pin, pout, q, k):
        """Packet COMETS riding a projected great-circle arc: the head follows the arc, the
        fading tail is drawn from the arc's own trailing samples — so both bend WITH the
        sphere and always point along the true direction of travel. orange = data IN
        (peer→node), green = OUT; link QUALITY (measured ping) sets brightness — a crisp
        bright comet is a fast link, a dim one a slow link. Arcs run node→peer."""
        n = len(pts)
        if n < 2 or (pin <= 0 and pout <= 0):
            return
        for frac, col, inbound in ((pin, QtGui.QColor("#F7931A"), True),
                                   (pout, QtGui.QColor("#16C784"), False)):
            if frac <= 0: continue
            npk = 1 + int(round(2 * frac)); spd = 0.5 + 2.0 * frac; rad = 1.5 + 2.3 * frac
            alpha = int(110 + 145 * q)
            tail = 2 + int(3 * frac)
            for j in range(npk):
                t = (self._fphase * spd + j / npk + k * (0.13 if inbound else 0.17)) % 1.0
                if inbound: t = 1.0 - t                       # IN travels peer→node (param ↓)
                fpos = t * (n - 1); i0 = int(fpos); fr = fpos - i0
                a, b = pts[i0], pts[min(i0 + 1, n - 1)]
                if not (a[2] and b[2]): continue              # front hemisphere only
                hx, hy = a[0] + (b[0] - a[0]) * fr, a[1] + (b[1] - a[1]) * fr
                step = 1 if inbound else -1                   # tail = BEHIND the direction of travel
                px, py = hx, hy
                for s in range(1, tail + 1):
                    ii = i0 + step * s
                    if not (0 <= ii < n) or not pts[ii][2]: break
                    fade = 1 - s / (tail + 1.0)
                    qp.setPen(QtGui.QPen(QtGui.QColor(col.red(), col.green(), col.blue(),
                                                      int(alpha * fade * 0.55)), max(0.8, rad * fade)))
                    qp.drawLine(QtCore.QPointF(px, py), QtCore.QPointF(pts[ii][0], pts[ii][1]))
                    px, py = pts[ii][0], pts[ii][1]
                qp.setPen(QtCore.Qt.NoPen)
                qp.setBrush(QtGui.QBrush(QtGui.QColor(col.red(), col.green(), col.blue(), alpha)))
                qp.drawEllipse(QtCore.QPointF(hx, hy), rad, rad)
    def _ensure_map(self, R):
        """Spin-independent per-pixel map (lat→row, base-lon→col, Lambert shade, disc mask).
        Rebuilt only when size/tilt/zoom change; spin is a cheap column shift each frame."""
        import numpy as np
        D = int(min(2 * R, 640)) or 2
        key = (D, round(self.view_tilt, 4))
        if self._mkey == key:
            return
        ax = (np.arange(D) + 0.5) / D * 2 - 1
        nx = np.broadcast_to(ax, (D, D))                 # x across columns
        ny = np.broadcast_to(-ax[:, None], (D, D))       # y down rows (y up)
        r2 = nx * nx + ny * ny
        mask = r2 <= 1.0
        z2 = np.sqrt(np.clip(1 - r2, 0, 1))              # front-hemisphere depth
        ct, st = math.cos(self.view_tilt), math.sin(self.view_tilt)
        y = ny * ct + z2 * st                            # un-tilt about X
        z = -ny * st + z2 * ct
        x = nx
        lat = np.degrees(np.arcsin(np.clip(y, -1, 1)))
        lon = np.degrees(np.arctan2(z, x))               # base longitude at spin=0
        self._mrow = np.clip(((90 - lat) / 180 * self._th).astype(np.int32), 0, self._th - 1)
        self._mcol0 = (lon + 180) / 360 * self._tw       # float texel column (per frame: −spin shift)
        lx, ly, lz = -0.5, 0.55, 0.78                    # fixed sun (upper-left, toward viewer)
        ln = (lx * lx + ly * ly + lz * lz) ** 0.5
        lam = np.clip((nx * lx + ny * ly + z2 * lz) / ln, 0, 1)
        self._mshade = (0.32 + 0.68 * lam)               # ambient + diffuse → 3D ball look
        self._mmask = mask
        self._mD = D; self._mkey = key
    def _globe_image(self):
        """Render the spinning Blue Marble sphere to a QImage (spin = texture column shift)."""
        import numpy as np
        D = self._mD
        shift = self.spin / 360.0 * self._tw
        col = ((self._mcol0 - shift) % self._tw).astype(np.int32)
        rgb = self._tex[self._mrow, col]                                  # (D,D,3) gathered imagery
        out = np.zeros((D, D, 4), dtype=np.uint8)
        out[..., :3] = np.clip(rgb * self._mshade[..., None], 0, 255).astype(np.uint8)
        out[..., 3] = self._mmask * 255
        self._imgbuf = out                                               # keep alive for the QImage
        return QtGui.QImage(out.data, D, D, 4 * D, QtGui.QImage.Format_RGBA8888)
    def paintEvent(self, e):
        qp = QtGui.QPainter(self); qp.setRenderHint(QtGui.QPainter.Antialiasing)
        qp.setRenderHint(QtGui.QPainter.SmoothPixmapTransform)
        w, h = self.width(), self.height(); cx, cy = w / 2, h / 2; R = (min(w, h) / 2 - 16) * self.zoom
        # atmosphere halo around the limb (drawn first; the sphere covers its interior)
        atm = QtGui.QRadialGradient(cx, cy, R * 1.16); inner = R / (R * 1.16)
        atm.setColorAt(max(0.0, inner - 0.03), QtGui.QColor(70, 175, 240, 0))
        atm.setColorAt(inner, QtGui.QColor(72, 178, 242, 95))
        atm.setColorAt(1.0, QtGui.QColor(72, 178, 242, 0))
        qp.setPen(QtCore.Qt.NoPen); qp.setBrush(QtGui.QBrush(atm))
        qp.drawEllipse(QtCore.QPointF(cx, cy), R * 1.16, R * 1.16)
        if self._tex is not None:
            # hyperreal: real NASA Blue Marble wrapped on the WGS84 sphere, Lambert-shaded
            self._ensure_map(R)
            qp.drawImage(QtCore.QRectF(cx - R, cy - R, 2 * R, 2 * R), self._globe_image(), QtCore.QRectF(0, 0, self._mD, self._mD))
            qp.setPen(QtGui.QPen(QtGui.QColor("#0e3d57"), 2)); qp.setBrush(QtCore.Qt.NoBrush)
            qp.drawEllipse(QtCore.QPointF(cx, cy), R, R)
        else:
            # vector fallback: shaded ocean + continent polygons
            g = QtGui.QRadialGradient(cx - R * 0.35, cy - R * 0.35, R * 1.5)
            g.setColorAt(0, QtGui.QColor("#0d2e44")); g.setColorAt(1, QtGui.QColor("#04101a"))
            qp.setPen(QtGui.QPen(QtGui.QColor("#0e3d57"), 2)); qp.setBrush(QtGui.QBrush(g))
            qp.drawEllipse(QtCore.QPointF(cx, cy), R, R)
            qp.setPen(QtGui.QPen(QtGui.QColor("#1c4a63"), 1)); qp.setBrush(QtGui.QBrush(QtGui.QColor(18, 60, 84, 210)))
            for poly in WORLD:
                front = [QtCore.QPointF(x, y) for (x, y, v) in (self._proj(la, lo, cx, cy, R) for lo, la in poly) if v]
                if len(front) >= 3: qp.drawPolygon(QtGui.QPolygonF(front))
        # overlays clipped to the globe disc
        qp.setClipRegion(QtGui.QRegion(QtCore.QRect(int(cx - R), int(cy - R), int(2 * R), int(2 * R)), QtGui.QRegion.Ellipse))
        if getattr(self, "show_borders", True) and WORLD_BORDERS:
            # POLITICAL overlay — front-hemisphere-clipped boundary polylines (same law as arcs)
            qp.setBrush(QtCore.Qt.NoBrush)
            qp.setPen(QtGui.QPen(QtGui.QColor(138, 160, 180, 120), 1.0))
            for line in WORLD_BORDERS:
                self._polyline(qp, [self._proj(la, lo, cx, cy, R) for lo, la in line])
        qp.setPen(QtCore.Qt.NoPen); qp.setBrush(QtGui.QBrush(QtGui.QColor(255, 255, 255, 130)))
        for (la, lo) in self._nodes:                                    # known-node cloud
            x, y, v = self._proj(la, lo, cx, cy, R)
            if v: qp.drawEllipse(QtCore.QPointF(x, y), 1.4, 1.4)
        # great-circle arcs — projected once per frame, then REUSED by the packet flow so the
        # comets ride the exact same curve the eye sees (heading adapts to the sphere's angle)
        proj_arcs = [[self._proj(la, lo, cx, cy, R) for (la, lo) in arc] for arc in self._arcs]
        qp.setPen(QtGui.QPen(QtGui.QColor(247, 147, 26, 180), 1.2))
        for pa in proj_arcs:
            self._polyline(qp, pa)
        for i, p in enumerate(self._peers):
            if i >= len(proj_arcs): break
            self._flow_arc(qp, proj_arcs[i], p.get("pin", 0.0), p.get("pout", 0.0), p.get("q", 0.6), i)
        self._hits = []                                                # rebuilt every frame (globe spins)
        self._hover_xy = None
        for pk in self._peers:                                         # connected peers
            la, lo, col, r, acc = pk["lat"], pk["lon"], pk["col"], pk["r"], pk.get("acc", 0)
            x, y, v = self._proj(la, lo, cx, cy, R)
            if not v: continue
            self._hits.append((x, y, r, pk))
            if pk is self._hover_pk:                                   # hovered = gold ring
                self._hover_xy = (x, y)
                qp.setBrush(QtCore.Qt.NoBrush)
                qp.setPen(QtGui.QPen(QtGui.QColor("#FFD37A"), 2))
                qp.drawEllipse(QtCore.QPointF(x, y), r + 4, r + 4)
            if acc and self.show_acc:
                # GeoIP accuracy ring — angular radius acc/RE on the sphere; the dot is a
                # centroid, the address is somewhere inside this circle
                ar = R * math.sin(min(1.2, acc / 6371.0))
                if ar > r + 2:
                    qp.setBrush(QtCore.Qt.NoBrush)
                    qp.setPen(QtGui.QPen(QtGui.QColor(col.red(), col.green(), col.blue(), 80), 1, QtCore.Qt.DashLine))
                    qp.drawEllipse(QtCore.QPointF(x, y), ar, ar)
            qp.setPen(QtGui.QPen(QtGui.QColor("#0b0f15"), 1)); qp.setBrush(QtGui.QBrush(col))
            qp.drawEllipse(QtCore.QPointF(x, y), r, r)
            if pk.get("fav"):                                          # ★ promoted = gold ring
                qp.setBrush(QtCore.Qt.NoBrush)
                qp.setPen(QtGui.QPen(QtGui.QColor("#FFD37A"), 2))
                qp.drawEllipse(QtCore.QPointF(x, y), r + 3, r + 3)
            if pk.get("medal"):                                        # 🥇 measured fastest
                qp.drawText(QtCore.QPointF(x - r - 14, y + 4), pk["medal"])
            # every node point states its DATA right on the sphere — address, geographic
            # city + country, and the LIVE rate when data is flowing (front hemisphere only)
            if pk.get("label"):
                f = qp.font(); f.setPointSize(8); f.setBold(False); qp.setFont(f)
                qp.setPen(QtGui.QColor(230, 237, 243, 225))
                qp.drawText(QtCore.QPointF(x + r + 3, y - 1), pk["label"][0])
                qp.setPen(QtGui.QColor(138, 190, 220, 205))
                qp.drawText(QtCore.QPointF(x + r + 3, y + 10), pk["label"][1])
                if pk.get("rate_s"):
                    qp.setPen(QtGui.QColor("#16C784"))
                    qp.drawText(QtCore.QPointF(x + r + 3, y + 21), pk["rate_s"])
        if self._my:                                                    # our node
            x, y, v = self._proj(self._my[0], self._my[1], cx, cy, R)
            if v:
                qp.setPen(QtGui.QPen(QtGui.QColor("#F7931A"), 2)); qp.setBrush(QtGui.QBrush(QtGui.QColor("#1a1200")))
                qp.drawEllipse(QtCore.QPointF(x, y), 6, 6)
                if self.my_tip:
                    mypk = getattr(self, "_my_pk", None)
                    if mypk is None or mypk.get("tip") != self.my_tip:
                        mypk = self._my_pk = {"tip": self.my_tip}
                    self._hits.append((x, y, 8, mypk))
                    if mypk is self._hover_pk:
                        self._hover_xy = (x, y)
        # in-canvas HOVER CARD — the point's ACTUAL data drawn by us, so it can never be
        # lost to a window manager that swallows tooltips
        qp.setClipping(False)
        if self._hover_pk is not None and self._hover_xy is not None and self._hover_pk.get("tip"):
            lines = self._hover_pk["tip"].split("\n")
            f = qp.font(); f.setPointSize(8); f.setBold(False); qp.setFont(f)
            fm = QtGui.QFontMetricsF(f)
            tw = max(fm.horizontalAdvance(t) for t in lines) + 16
            th = 8 + 14 * len(lines)
            hx, hy = self._hover_xy
            bx = min(max(6.0, hx + 14), max(6.0, w - tw - 6))
            by = min(max(6.0, hy - th - 10), max(6.0, h - th - 6))
            qp.setPen(QtGui.QPen(QtGui.QColor("#F7931A"), 1))
            qp.setBrush(QtGui.QBrush(QtGui.QColor(4, 7, 12, 235)))
            qp.drawRoundedRect(QtCore.QRectF(bx, by, tw, th), 6, 6)
            for i, t in enumerate(lines):
                qp.setPen(QtGui.QColor("#F7931A") if i == 0 else QtGui.QColor("#d6e3ef"))
                qp.drawText(QtCore.QPointF(bx + 8, by + 14 + 14 * i), t)
        qp.end()


class AdvancedGeoWidget(QtWidgets.QWidget):
    """🔬 Advanced — ₿ANKON network science, computed ONLY from this node's live data
    (no external service, ever). Two instruments that actually matter for enhancing a
    running Bitcoin Core:

    🩺 NETWORK HEALTH — peer-diversity / eclipse-risk indicators: country & ASN
       concentration (Herfindahl–Hirschman index), inbound/outbound mix, transport mix,
       ping distribution, addrman depth. A concentrated peer set is how eclipse attacks
       start — this page says so plainly.
    📏 LATENCY vs DISTANCE — per peer: geodesic distance (from GeoIP), the physical
       light-in-fiber lower bound for that round trip, the MEASURED ping, and the link's
       efficiency (bound ÷ measured). Finds slow links that geography cannot excuse."""
    def __init__(self, node_latlon_fn=None, peers_fn=None):
        super().__init__()
        self._node_latlon = node_latlon_fn or (lambda: None)
        self._peers_fn = peers_fn or (lambda: [])
        lay = QtWidgets.QVBoxLayout(self)
        priv = QtWidgets.QLabel("🔒 Computed locally from this node's RPC + bundled GeoLite2 — "
                                "no external service is contacted. Geo is approximate (see 🎯 accuracy).")
        priv.setStyleSheet("color:#16C784;font-weight:600"); priv.setWordWrap(True)
        lay.addWidget(priv)
        tabs = QtWidgets.QTabWidget(); lay.addWidget(tabs, 1)
        # 🩺 health
        hw = QtWidgets.QWidget(); hv = QtWidgets.QVBoxLayout(hw)
        self.health = QtWidgets.QLabel("open with connected peers to compute…")
        self.health.setWordWrap(True); self.health.setTextFormat(QtCore.Qt.RichText)
        self.health.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        self.health.setAlignment(QtCore.Qt.AlignTop)
        hs = QtWidgets.QScrollArea(); hs.setWidgetResizable(True); hs.setWidget(self.health)
        hv.addWidget(hs)
        tabs.addTab(hw, "🩺 network health")
        # 📏 latency vs distance
        lw = QtWidgets.QWidget(); lv = QtWidgets.QVBoxLayout(lw)
        self.lat_note = QtWidgets.QLabel(
            "distance = great circle from THIS node (GeoIP, approximate) · bound = physical best-case "
            "RTT at light-in-fiber (~200,000 km/s) · efficiency = bound ÷ measured ping (100% = at the "
            "physical limit; low % = routing/queueing overhead the geography cannot excuse)")
        self.lat_note.setWordWrap(True); self.lat_note.setStyleSheet("color:#8aa0b4")
        lv.addWidget(self.lat_note)
        self.lat_tbl = QtWidgets.QTableWidget(0, 6)
        self.lat_tbl.setHorizontalHeaderLabels(["peer", "location", "distance", "light bound", "ping", "efficiency"])
        self.lat_tbl.verticalHeader().setVisible(False)
        self.lat_tbl.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        # HD3000 rule: Interactive header + ONE resize per render, never ResizeToContents mode
        self.lat_tbl.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Interactive)
        lv.addWidget(self.lat_tbl, 1)
        tabs.addTab(lw, "📏 latency vs distance")

    @staticmethod
    def _hav_km(la1, lo1, la2, lo2):
        p1, p2 = math.radians(la1), math.radians(la2)
        dl, dp = math.radians(lo2 - lo1), p2 - p1
        a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
        return 2 * 6371.0 * math.asin(min(1.0, math.sqrt(a)))

    @staticmethod
    def _hhi(counter):
        n = sum(counter.values())
        return sum((v / n) ** 2 for v in counter.values()) if n else 0.0

    def refresh_data(self):
        from collections import Counter
        peers = self._peers_fn() or []
        my = self._node_latlon()
        cc, asns, nets = Counter(), Counter(), Counter()
        pings, located, rows = [], 0, []
        inn = sum(1 for p in peers if p.get("inbound"))
        for p in peers:
            host = (p.get("addr") or "").rsplit(":", 1)[0].strip("[]")
            g = geolocate(host) if is_ip_literal(host) else None
            nets[p.get("network") or ("ip" if is_ip_literal(host) else "tor/other")] += 1
            if p.get("pingtime"): pings.append(p["pingtime"] * 1000)
            if not g: continue
            located += 1; cc[g["iso"]] += 1
            an = asn_lookup(host) or {}
            if an.get("org"): asns[an["org"][:24]] += 1
            if my and p.get("pingtime"):
                d = self._hav_km(my[0], my[1], g["lat"], g["lon"])
                bound = max(0.1, d / 100.0)               # RTT ms at ~200,000 km/s in fiber
                ping = p["pingtime"] * 1000
                rows.append((p.get("addr"), f"{g.get('city') or ''} {flag(g['iso'])}".strip(),
                             d, bound, ping, min(1.0, bound / ping) if ping else 0.0))
        # ── 🩺 health verdicts ──
        pings.sort()
        med = pings[len(pings) // 2] if pings else None
        hhi_c, hhi_a = self._hhi(cc), self._hhi(asns)
        def risk(h):
            return ("<span style='color:#16C784'>diverse</span>" if h < 0.2 else
                    "<span style='color:#F7931A'>concentrated</span>" if h < 0.4 else
                    "<span style='color:#f85149'>ECLIPSE-RISK</span>")
        top_c = "  ".join(f"{flag(i)} {i} {n}" for i, n in cc.most_common(5)) or "—"
        top_a = " · ".join(f"{o} {n}" for o, n in asns.most_common(3)) or "—"
        net_mix = " · ".join(f"{k} {n}" for k, n in nets.most_common()) or "—"
        self.health.setText(
            f"<b style='color:#F7931A'>peers</b> {len(peers)} ({len(peers) - inn} out · {inn} in) · "
            f"{located} located · median ping {med:.0f} ms<br>" if med is not None else
            f"<b style='color:#F7931A'>peers</b> {len(peers)} ({len(peers) - inn} out · {inn} in) · "
            f"{located} located<br>")
        self.health.setText(self.health.text() +
            f"<b>countries</b> {len(cc)} — {top_c}<br>"
            f"<b>country concentration</b> HHI {hhi_c:.2f} → {risk(hhi_c)}<br>"
            f"<b>ASNs</b> {len(asns)} — {top_a}<br>"
            f"<b>ASN concentration</b> HHI {hhi_a:.2f} → {risk(hhi_a)}<br>"
            f"<b>transports</b> {net_mix}<br><br>"
            "<span style='color:#8aa0b4'>Eclipse attacks begin with a homogeneous peer set: "
            "if one country or one network operator carries most of your connections, that party "
            "can isolate this node's view of the chain. Diversify with addnode / onion peers when "
            "a line above turns orange or red. (Computation: Herfindahl–Hirschman index over the "
            "located peers; inbound mix and transports from getpeerinfo.)</span>")
        # ── 📏 table: one resize after fill (HD3000 rule) ──
        rows.sort(key=lambda r: r[5])                     # worst efficiency first — the news
        self.lat_tbl.setRowCount(len(rows))
        for i, (addr, loc, d, bound, ping, eff) in enumerate(rows):
            vals = [addr, loc, f"{d:,.0f} km", f"{bound:,.1f} ms", f"{ping:,.0f} ms", f"{eff * 100:.0f}%"]
            for j, s in enumerate(vals):
                it = QtWidgets.QTableWidgetItem(s)
                if j == 5:
                    it.setForeground(QtGui.QColor("#16C784" if eff > 0.5 else
                                                  "#F7931A" if eff > 0.2 else "#f85149"))
                self.lat_tbl.setItem(i, j, it)
        self.lat_tbl.resizeColumnsToContents()



class PriceOverlay(QtWidgets.QWidget):
    """🪙 ₿TC/USD price OVERLAY — floats over the geo display (globe, flat map, flatearth,
    advanced), drawn completely IN-HOUSE with QPainter. Data source is the opt-in CoinGecko
    free-tier poll (casual: once an hour); the chart shows the last ~24 hourly prices with
    each price MARKED ON THE HOUR. Rendered only while the 🪙 toggle is on — zero cost off."""
    def __init__(self, parent, tzfmt):
        super().__init__(parent)
        self._tzfmt = tzfmt                    # GeoMapTab's timezone-choice formatter
        self._series = []                      # [(epoch_s on-the-hour, usd)] ascending
        self._spot = None                      # (usd, epoch_s) most recent spot quote
        self._err = ""
        self.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents)   # never steal globe drags
        self.hide()
    def set_data(self, series, spot):
        self._series = series or []; self._err = ""
        if spot: self._spot = spot
        self.update()
    def set_error(self, msg):
        self._err = msg or ""; self.update()
    def paintEvent(self, e):
        qp = QtGui.QPainter(self); qp.setRenderHint(QtGui.QPainter.Antialiasing)
        w, h = self.width(), self.height()
        qp.setPen(QtGui.QPen(QtGui.QColor("#F7931A"), 1))
        qp.setBrush(QtGui.QBrush(QtGui.QColor(4, 7, 12, 215)))      # translucent in-house panel
        qp.drawRoundedRect(0, 0, w - 1, h - 1, 8, 8)
        f = qp.font()
        spot, sat = self._spot if self._spot else (None, None)
        f.setPointSize(14); f.setBold(True); qp.setFont(f); qp.setPen(QtGui.QColor("#F7931A"))
        qp.drawText(QtCore.QRectF(10, 4, w - 20, 24), QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter,
                    f"₿ ${spot:,.0f}" if spot else "₿ price …")
        f.setPointSize(8); f.setBold(False); qp.setFont(f); qp.setPen(QtGui.QColor("#8aa0b4"))
        sub = (self._err or "CoinGecko free tier · casual hourly poll · marked on the hour"
               + (f" · updated {self._tzfmt(sat)}" if sat else ""))
        qp.drawText(QtCore.QRectF(10, 26, w - 20, 14), QtCore.Qt.AlignLeft, sub[:96])
        s = self._series[-25:]
        if len(s) < 2:
            qp.end(); return
        L, R, T, B = 12, w - 12, 48, h - 20
        vals = [p for _, p in s]
        lo, hi = min(vals), max(vals); pad = max(1.0, (hi - lo) * 0.15); lo -= pad; hi += pad
        xof = lambda i: L + i / (len(s) - 1) * (R - L)
        yof = lambda v: B - (v - lo) / (hi - lo) * (B - T)
        # hour ticks: label every 4th wall-clock hour along the bottom (chosen timezone)
        qp.setPen(QtGui.QColor("#5a7891"))
        for i, (ts, _v) in enumerate(s):
            if (ts // 3600) % 4 == 0:
                qp.drawText(int(xof(i)) - 14, h - 6, self._tzfmt(ts, "%H:00"))
        line = QtGui.QPainterPath(); line.moveTo(xof(0), yof(s[0][1]))
        for i, (_ts, v) in enumerate(s[1:], 1): line.lineTo(xof(i), yof(v))
        qp.setPen(QtGui.QPen(QtGui.QColor("#F7931A"), 1.6)); qp.setBrush(QtCore.Qt.NoBrush)
        qp.drawPath(line)
        qp.setPen(QtGui.QPen(QtGui.QColor("#0b0f15"), 1)); qp.setBrush(QtGui.QBrush(QtGui.QColor("#F7931A")))
        for i, (_ts, v) in enumerate(s):                      # ● every on-the-hour mark
            qp.drawEllipse(QtCore.QPointF(xof(i), yof(v)), 2.4, 2.4)
        # latest hourly mark: candle-green dot + its price
        qp.setBrush(QtGui.QBrush(QtGui.QColor("#16C784")))
        qp.drawEllipse(QtCore.QPointF(xof(len(s) - 1), yof(s[-1][1])), 3.2, 3.2)
        qp.setPen(QtGui.QColor("#16C784"))
        qp.drawText(int(min(xof(len(s) - 1) - 52, w - 64)), int(yof(s[-1][1])) - 6, f"${s[-1][1]:,.0f}")
        qp.setPen(QtGui.QColor("#5a7891"))                    # range labels (inside the plot)
        qp.drawText(L + 2, T + 10, f"${hi:,.0f}")
        qp.drawText(L + 2, B - 4, f"${lo:,.0f}")
        qp.end()


class NodeInfoOverlay(QtWidgets.QWidget):
    """🏠 LOCAL NODE overlay — this machine's node, PROMINENT, from actual data: NODE
    (height · sync · agent) · NET (peers + live ▼/▲ totals + our address) · BLOCKS (tip ·
    age · headers) — each line its own toggle — with the time (chosen timezone) riding the
    header. Drawn completely in-house with QPainter; floats top-left over the geo display,
    opposite the 🪙 price overlay. Transparent to the mouse so globe drags pass through."""
    LBL = {"node": "NODE", "net": "NET", "blocks": "BLOCKS"}
    def __init__(self, parent, tzfmt):
        super().__init__(parent)
        self._tzfmt = tzfmt
        self._d = {}
        self._flags = ("node", "net", "blocks")
        # never in the way: DRAG anywhere on the card to move it (snap-docks to a corner on
        # release, or stays free), drag the ⇲ grip to RESIZE — both remembered
        self.on_layout = None                   # GeoMapTab's persist/snap hook
        self._drag = None; self._resz = None
        self.setMouseTracking(True)
        self.setCursor(QtCore.Qt.OpenHandCursor)
        self.hide()
    def _in_grip(self, pos):
        return pos.x() > self.width() - 18 and pos.y() > self.height() - 18
    def mousePressEvent(self, e):
        if e.button() != QtCore.Qt.LeftButton: return
        if self._in_grip(e.position()):
            self._resz = (e.position().x(), self.width())
        else:
            self._drag = e.position(); self.setCursor(QtCore.Qt.ClosedHandCursor)
    def mouseMoveEvent(self, e):
        par = self.parentWidget()
        if self._resz is not None and par is not None:
            w = int(self._resz[1] + e.position().x() - self._resz[0])
            self.resize(max(240, min(par.width() - self.x() - 6, w)), self.wanted_height())
            self.update()
        elif self._drag is not None and par is not None:
            np_ = self.mapToParent(e.position()) - self._drag
            self.move(int(max(0, min(np_.x(), par.width() - self.width()))),
                      int(max(0, min(np_.y(), par.height() - self.height()))))
        else:
            self.setCursor(QtCore.Qt.SizeFDiagCursor if self._in_grip(e.position())
                           else QtCore.Qt.OpenHandCursor)
    def mouseReleaseEvent(self, e):
        moved = self._drag is not None or self._resz is not None
        self._drag = self._resz = None
        self.setCursor(QtCore.Qt.OpenHandCursor)
        if moved and self.on_layout:
            self.on_layout()
    def set_flags(self, flags):
        self._flags = tuple(flags); self.update()
    def set_data(self, d):
        self._d.update(d or {}); self.update()
    def wanted_height(self):
        return 36 + 16 * len(self._flags)
    def paintEvent(self, e):
        qp = QtGui.QPainter(self); qp.setRenderHint(QtGui.QPainter.Antialiasing)
        w, h = self.width(), self.height()
        qp.setPen(QtGui.QPen(QtGui.QColor("#00BFFF"), 1))
        qp.setBrush(QtGui.QBrush(QtGui.QColor(4, 7, 12, 215)))
        qp.drawRoundedRect(0, 0, w - 1, h - 1, 8, 8)
        f = qp.font(); f.setPointSize(11); f.setBold(True); qp.setFont(f)
        qp.setPen(QtGui.QColor("#F7931A"))                        # the local node is PROMINENT
        qp.drawText(10, 20, "🏠 ₿ANKON node")
        qp.setPen(QtGui.QColor("#00BFFF"))                        # …with the time beside it
        qp.drawText(QtCore.QRectF(0, 4, w - 10, 20), QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter,
                    self._d.get("clock", ""))
        f.setPointSize(8); f.setBold(False); qp.setFont(f)
        y = 26
        for k in self._flags:
            y += 16
            qp.setPen(QtGui.QColor("#5a7891")); qp.drawText(10, y, self.LBL.get(k, k.upper()))
            qp.setPen(QtGui.QColor("#d6e3ef")); qp.drawText(62, y, (self._d.get(k) or "…")[:170])
        qp.setPen(QtGui.QColor("#3a4b5c"))      # ⇲ resize grip, bottom-right
        qp.drawText(w - 14, h - 5, "⇲")
        qp.end()


class GeoMapTab(QtWidgets.QWidget):
    """Geo map (EPSG:4326 plate carrée). The WHOLE known network from this node's addrman
    (getnodeaddresses — ₿itnodes-style, self-sourced, no external API) as a density layer,
    plus the CONNECTED peers with great-circle arcs to our node, coloured & geolocated by
    GeoLite2. Edges are inferred (Core exposes none) and IP geolocation is approximate."""
    W, H = 1440, 720
    def __init__(self):
        super().__init__(); v = QtWidgets.QVBoxLayout(self)
        self.globe = GlobeWidget()                                  # spinning orthographic globe (default view)
        top = QtWidgets.QHBoxLayout()
        self.info = QtWidgets.QLabel("Geo map — GeoLite2" if HAVE_GEOIP else "Geo map — GeoIP DB not found")
        self.info.setStyleSheet("color:#8aa0b4"); top.addWidget(self.info, 1)
        # spin speed + direction: left = west, centre = stop, right = east
        top.addWidget(QtWidgets.QLabel("◀ spin ▶"))
        self.spin_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal); self.spin_slider.setFixedWidth(130)
        self.spin_slider.setRange(-100, 100); self.spin_slider.setToolTip("Auto-spin — left=west · centre=stop · right=east")
        self.spin_slider.valueChanged.connect(lambda val: self.globe.set_auto_speed(val / 100 * 1.5))
        self.spin_slider.setValue(23)                              # ≈ 0.35°/frame east (matches default)
        top.addWidget(self.spin_slider)
        reset = QtWidgets.QPushButton("⟲"); reset.setFixedWidth(34); reset.setToolTip("Reset view (tilt + zoom)")
        reset.clicked.connect(self.globe.reset_view); top.addWidget(reset)
        self.connected = QtWidgets.QCheckBox("connected"); self.connected.setChecked(True)   # ON by default
        self.connected.setToolTip("Plot every CONNECTED peer at its actual location (arcs + live packet flow); "
                                  "peers with no geo data (Tor/unmapped) are listed honestly, never dropped")
        self.connected.toggled.connect(lambda _: self._redraw()); top.addWidget(self.connected)
        self.allnodes = QtWidgets.QCheckBox("🌐 all known"); self.allnodes.setToolTip(
            "GLOBAL view — additionally plot the whole addrman network (every node this node knows about)")
        self.allnodes.toggled.connect(lambda _: self.refresh()); top.addWidget(self.allnodes)
        # POLITICAL overlay (Natural Earth 110m admin_0 boundary lines) + optional nearest-major-city
        # labels — both persisted across sessions. Borders default ON, cities opt-in.
        _st = QtCore.QSettings("BANKON", "bankon-qt")
        self.borders_chk = QtWidgets.QCheckBox("🗺 borders")
        self.borders_chk.setChecked(_st.value("geomap/borders", "true") == "true")
        self.borders_chk.setToolTip("Political overlay — international boundary lines (Natural Earth 110m, public domain)")
        self.borders_chk.toggled.connect(self._overlay_changed); top.addWidget(self.borders_chk)
        self.cities_chk = QtWidgets.QCheckBox("🏙 cities")
        self.cities_chk.setChecked(_st.value("geomap/cities", "false") == "true")
        self.cities_chk.setToolTip("Label the nearest major city for each located peer (bundled ~800-city table)")
        self.cities_chk.toggled.connect(self._overlay_changed); top.addWidget(self.cities_chk)
        self.toggle = QtWidgets.QPushButton("🗺 Flat map"); self.toggle.setToolTip("Switch spinning globe / flat map")
        self.toggle.clicked.connect(self._toggle); top.addWidget(self.toggle)
        # Flat-map projection: plate carrée (EPSG:4326) or the accurate flat-earth
        # azimuthal-equidistant disc (exact distance/azimuth from the North Pole).
        self.projbox = QtWidgets.QComboBox()
        self.projbox.addItems(["Plate Carrée", "flatearth (azimuthal equidistant)"])
        self.projbox.setToolTip("Flat-map projection · flatearth = North-Pole azimuthal-equidistant (accurate 2D)")
        self.projbox.currentIndexChanged.connect(self._on_proj); top.addWidget(self.projbox)
        self.projmode = "plate"
        self.advbtn = QtWidgets.QPushButton("🔬 Advanced")
        self.advbtn.setToolTip("₿ANKON network science from THIS node's live data: 🩺 peer-diversity / "
                               "eclipse-risk health + 📏 latency-vs-distance efficiency. No external calls.")
        self.advbtn.clicked.connect(self._show_advanced); top.addWidget(self.advbtn)
        v.addLayout(top)
        # second control row: GeoIP truth + opt-in price overlay + timezone choice
        row2 = QtWidgets.QHBoxLayout()
        self.acc_chk = QtWidgets.QCheckBox("🎯 accuracy")
        self.acc_chk.setChecked(_st.value("geomap/accuracy", "true") == "true")
        self.acc_chk.setToolTip("GeoIP truth per node ADDRESS: draw GeoLite2's accuracy_radius as a circle — "
                                "the address is somewhere INSIDE it, the dot is only the centroid. "
                                "Tooltips state ±km and the precision tier.")
        self.acc_chk.toggled.connect(self._overlay_changed); row2.addWidget(self.acc_chk)
        self.price_chk = QtWidgets.QCheckBox("🪙 ₿ price")
        self.price_chk.setChecked(_st.value("geomap/price", "false") == "true")
        self.price_chk.setToolTip("OPT-IN external call: poll CoinGecko (free tier, no key) for ₿TC/USD at a casual "
                                  "hourly cadence and overlay the chart on the display — each price marked ON THE HOUR. "
                                  "Off (default) = zero network contact. Drawn completely in-house.")
        self.price_chk.toggled.connect(self._price_toggled); row2.addWidget(self.price_chk)
        self.feed_chk = QtWidgets.QCheckBox("📡 feed")
        self.feed_chk.setChecked(_st.value("geomap/feed", "true") == "true")
        self.feed_chk.setToolTip("Side column beside the globe: live node activity (connections, from this "
                                 "node's log) + transactions (mempool Δ and connected blocks)")
        self.feed_chk.toggled.connect(self._feed_toggled); row2.addWidget(self.feed_chk)
        # 🏆 marks — the Network / Net Map tabs' knowledge overlaid HERE: ★ promoted peers
        # (gold ring), 🏆 measured-fastest medals, 🚫 banned addresses geolocated
        self.marks_chk = QtWidgets.QCheckBox("🏆 marks")
        self.marks_chk.setChecked(_st.value("geomap/marks", "true") == "true")
        self.marks_chk.setToolTip("Overlay what Network / Net Map already know: ★ promoted (addnode) = gold ring · "
                                  "🥇🥈🥉 measured-fastest medals (score/ping) · 🚫 banned addresses as red ✖ "
                                  "at their geolocation")
        self.marks_chk.toggled.connect(self._marks_toggled); row2.addWidget(self.marks_chk)
        self.maxbtn = QtWidgets.QPushButton("⛶ max")
        self.maxbtn.setToolTip("Maximize the geo display to the whole screen — ⤢ retract (or Esc) puts it "
                               "back exactly where it was")
        self.maxbtn.clicked.connect(self._toggle_max); row2.addWidget(self.maxbtn)
        ngb = QtWidgets.QPushButton("🌐 +globe")
        ngb.setToolTip("Create a NEW globe instance in its own window to watch — fed the same live data; "
                       "drag it to any display, close it when done")
        ngb.clicked.connect(self._new_globe); row2.addWidget(ngb)
        # 🏠 local-node overlay toggles — the participant's own node, prominent, actual data
        self.ovl_node = QtWidgets.QCheckBox("🏠 node")
        self.ovl_node.setToolTip("Overlay line: this node's height · sync % · agent (actual getblockchaininfo)")
        self.ovl_net = QtWidgets.QCheckBox("🌐 net")
        self.ovl_net.setToolTip("Overlay line: peers (out/in) · live ▼/▲ B/s totals · our address")
        self.ovl_blocks = QtWidgets.QCheckBox("⛓ blocks")
        self.ovl_blocks.setToolTip("Overlay line: chain tip · tip age · headers")
        for key, c in (("ovl_node", self.ovl_node), ("ovl_net", self.ovl_net), ("ovl_blocks", self.ovl_blocks)):
            c.setChecked(_st.value("geomap/" + key, "true") == "true")
            c.toggled.connect(self._nodeinfo_changed); row2.addWidget(c)
        row2.addWidget(QtWidgets.QLabel("🕐 tz"))
        self.tz_box = QtWidgets.QComboBox()
        self.tz_box.addItems(["UTC", "local"] + [f"UTC{h:+d}" for h in range(-12, 15) if h])
        self.tz_box.setCurrentText(_st.value("geomap/tz", "UTC"))
        self.tz_box.setToolTip("Timezone for every timestamp on this map (hour marks, updated-at, addrman as-of) — "
                               "UTC by default, or local / a fixed UTC offset")
        self.tz_box.currentTextChanged.connect(self._tz_changed); row2.addWidget(self.tz_box)
        row2.addStretch(1)
        v.addLayout(row2)
        self.scene = QtWidgets.QGraphicsScene(); self.view = QtWidgets.QGraphicsView(self.scene)
        self.view.setRenderHint(QtGui.QPainter.Antialiasing)
        self.view.setStyleSheet("background:#05080d;border:2px solid #00BFFF;border-radius:8px")
        self.advanced = AdvancedGeoWidget(self._my_latlon, lambda: self._peers)
        self.stack = QtWidgets.QStackedWidget(); self.stack.addWidget(self.globe); self.stack.addWidget(self.view); self.stack.addWidget(self.advanced)
        # RIGHT data column — the empty flank beside the globe becomes OUTPUT from the local
        # node's perspective: live connection activity (top) + transactions/blocks (bottom).
        # The LEFT flank stays clear for the 🛠 admin popup's geo-map dock.
        self.feed = QtWidgets.QWidget(); fv = QtWidgets.QVBoxLayout(self.feed)
        fv.setContentsMargins(6, 0, 0, 0); fv.setSpacing(4)
        self.feed.setFixedWidth(285)
        _t1 = QtWidgets.QLabel("📡 node activity — this node's log")
        _t1.setStyleSheet("color:#00BFFF;font-weight:700"); fv.addWidget(_t1)
        self.act_list = QtWidgets.QListWidget()
        self.act_list.setStyleSheet("font-size:11px"); self.act_list.setWordWrap(False)
        self.act_list.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        fv.addWidget(self.act_list, 3)
        _t2 = QtWidgets.QLabel("🧾 transactions — mempool & blocks")
        _t2.setStyleSheet("color:#F7931A;font-weight:700"); fv.addWidget(_t2)
        self.tx_list = QtWidgets.QListWidget()
        self.tx_list.setStyleSheet("font-size:11px"); self.tx_list.setWordWrap(False)
        self.tx_list.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        fv.addWidget(self.tx_list, 2)
        self.feed.setVisible(_st.value("geomap/feed", "true") == "true")
        mid = QtWidgets.QHBoxLayout(); mid.setSpacing(0)
        mid.addWidget(self.stack, 1); mid.addWidget(self.feed)
        v.addLayout(mid, 1)
        self._mid = mid                         # ⛶ max lifts the stack out and retracts it here
        self._max_win = None
        self._watchers = []                     # 🌐 +globe: (window, GlobeWidget) mirrors
        self.stack.installEventFilter(self)     # any stack resize (tab OR fullscreen) → re-place overlays
        self._mp_prev = None                    # (txcount, t) for the mempool Δ line
        self._fast, self._favs, self._banned = {}, set(), []   # 🏆 Net Map knowledge
        # 🪙 price OVERLAY floats over whichever view is showing (globe / flat / flatearth / advanced)
        self.price_overlay = PriceOverlay(self.stack, self._tzfmt)
        self._price_series = []; self._price_last = 0.0; self._price_try = 0.0; self._price_busy = False
        self._price_timer = QtCore.QTimer(self); self._price_timer.timeout.connect(self._price_tick)
        self._price_timer.setInterval(60_000)   # 1-min heartbeat that only ACTS once an hour
        # 🏠 local-node OVERLAY (top-left counterpart) + its data + a 1 s clock in the chosen tz
        self.node_overlay = NodeInfoOverlay(self.stack, self._tzfmt)
        self.node_overlay.on_layout = self._nodeovl_dropped   # drag/resize → snap-dock + remember
        self._bci = {}
        self._clock_t = QtCore.QTimer(self); self._clock_t.timeout.connect(self._clock_tick)
        self._clock_t.start(1000)
        self.legend = QtWidgets.QLabel(""); self.legend.setStyleSheet("color:#d6e3ef"); self.legend.setWordWrap(True); v.addWidget(self.legend)
        self._peers, self._ni, self._net, self._act = [], {}, [], []
        self._bg = None; self._bg_n = -1     # cached background pixmap + the node count it was built for
        # PACKET FLOW state — same colour law as the Net Map: orange = actual bytes INTO this
        # node (peer → us along the arc), green = actual bytes OUT (us → peer). Rates are real
        # per-peer B/s deltas between polls, on the same absolute log scale.
        self._prev = {}          # addr -> (bytesrecv, bytessent, t)
        self._rates = {}         # addr -> (in B/s, out B/s)
        self._flows = []         # [(arc points [(x,y)…] node→peer, pin, pout)]
        self._fanim = []         # overlay dot items, rebuilt each pulse
        self._fphase = 0.0
        self._ftimer = QtCore.QTimer(self); self._ftimer.timeout.connect(self._flow_pulse); self._ftimer.start(80)
        cities_ensure_full()      # complete GeoNames city list loads in the background (lazy, ~1s)
        # DNS-named peers (addnode'd seed hostnames ride getpeerinfo.addr verbatim) resolve
        # here once per session so they can be plotted — labeled approximate, never implied exact
        self._dns = {}
        if self.price_chk.isChecked():          # persisted opt-in → resume the casual hourly poll
            self._price_toggled(True)
        self._update_nodeinfo()                 # 🏠 overlay up from the start (per its toggles)
    def _peer_ip(self, p):
        """Peer's plottable IP: literal addr, or the session-resolved IP of a DNS-named peer."""
        host = (p.get("addr") or "").rsplit(":", 1)[0].strip("[]")
        return (host if is_ip_literal(host) else self._dns.get(host)), host
    def _resolve_hostnames(self, peers):
        todo = []
        for p in peers or []:
            host = (p.get("addr") or "").rsplit(":", 1)[0].strip("[]")
            if host and not is_ip_literal(host) and host not in self._dns:
                todo.append(host)
        if not todo:
            return
        def work(hs=sorted(set(todo))):
            import socket as _s
            out = {}
            for h in hs:
                try:
                    out[h] = _s.getaddrinfo(h, 8333, _s.AF_INET)[0][4][0]
                except Exception:
                    out[h] = None                    # cache the miss — no repeat lookups
            return out
        spawn_fn(work, lambda d: (self._dns.update(d or {}), self._redraw()))
    def _toggle(self):
        # Globe (0) ⇄ Flat (1); from Advanced (2) go back to Globe.
        i = 1 if self.stack.currentIndex() == 0 else 0
        self.stack.setCurrentIndex(i)
        self.toggle.setText("🗺 Flat map" if i == 0 else "🌐 Globe")
    def _show_advanced(self):
        self.advanced.refresh_data()           # compute from the CURRENT peers, every open
        self.stack.setCurrentIndex(2)
    def _on_proj(self, i):
        self.projmode = "flatearth" if i == 1 else "plate"
        self._bg = None                        # projection changed → rebuild background
        self.stack.setCurrentIndex(1)          # show the flat map so the change is visible
        self.toggle.setText("🌐 Globe")
        self._redraw()
    def _overlay_changed(self, _on):
        st = QtCore.QSettings("BANKON", "bankon-qt")
        st.setValue("geomap/borders", "true" if self.borders_chk.isChecked() else "false")
        st.setValue("geomap/cities", "true" if self.cities_chk.isChecked() else "false")
        st.setValue("geomap/accuracy", "true" if self.acc_chk.isChecked() else "false")
        self.globe.show_borders = self.borders_chk.isChecked()
        self.globe.show_acc = self.acc_chk.isChecked()
        self._bg = None                        # borders live in the cached background pixmap
        self._redraw(); self.globe.update()
    # ── timezone choice (UTC default · local · fixed UTC offset) — every stamp on this map ──
    def _tzfmt(self, epoch, fmt="%H:%M"):
        try:
            choice = self.tz_box.currentText()
        except Exception:
            choice = "UTC"
        if choice == "local":
            return time.strftime(fmt, time.localtime(epoch))
        off = int(choice[3:]) * 3600 if len(choice) > 3 else 0
        return time.strftime(fmt, time.gmtime(epoch + off))
    def _tz_changed(self, txt):
        QtCore.QSettings("BANKON", "bankon-qt").setValue("geomap/tz", txt)
        self.price_overlay.update(); self._redraw()
    # ── 🪙 ₿ price (CoinGecko free tier) — OPT-IN, casual hourly, marked on the hour ──
    def _price_toggled(self, on):
        QtCore.QSettings("BANKON", "bankon-qt").setValue("geomap/price", "true" if on else "false")
        self.price_overlay.setVisible(on)
        if on:
            self._place_overlays()
            self._price_timer.start()
            self._price_fetch(backfill=len(self._price_series) < 2)
        else:
            self._price_timer.stop()           # off = zero network contact
    def _place_overlays(self):
        w = min(360, max(240, self.stack.width() - 24))
        self.price_overlay.setGeometry(max(8, self.stack.width() - w - 12), 10, w, 150)
        self.price_overlay.raise_()
        # 🏠 card: remembered dock corner (TL/TR/BL/BR) or the remembered free spot + width
        st = QtCore.QSettings("BANKON", "bankon-qt")
        dock = st.value("geomap/nodeovl", "TL")
        try:
            gx, gy, gw = [int(t) for t in (st.value("geomap/nodeovl_geom") or "").split(",")]
        except Exception:
            gx, gy, gw = 10, 10, 0
        sw, sh = self.stack.width(), self.stack.height()
        nw = max(240, min(gw or int(sw * 0.5), max(240, sw - 20)))
        nh = self.node_overlay.wanted_height()
        pos = {"TL": (10, 10), "TR": (sw - nw - 10, 10),
               "BL": (10, sh - nh - 10), "BR": (sw - nw - 10, sh - nh - 10)}.get(dock)
        if pos is None:                          # free — clamp the remembered spot into view
            pos = (max(0, min(gx, sw - nw)), max(0, min(gy, sh - nh)))
        self.node_overlay.setGeometry(pos[0], pos[1], nw, nh)
        self.node_overlay.raise_()
    def eventFilter(self, obj, ev):
        if obj is self.stack and ev.type() == QtCore.QEvent.Resize:
            self._place_overlays()
        return super().eventFilter(obj, ev)
    # ── ⛶ maximize the geo display to the screen · ⤢ retract to its original position ──
    def _toggle_max(self):
        if self._max_win is None:
            w = self._max_win = QtWidgets.QWidget(None)
            w.setWindowTitle("🌍 ₿ANKON Geo Map — fullscreen (Esc or ⤢ to retract)")
            lay = QtWidgets.QVBoxLayout(w); lay.setContentsMargins(4, 4, 4, 4)
            bar = QtWidgets.QHBoxLayout()
            back = QtWidgets.QPushButton("⤢ retract")
            back.setToolTip("Return the geo display to its place in the tab (Esc works too)")
            back.clicked.connect(self._toggle_max); bar.addWidget(back); bar.addStretch(1)
            lay.addLayout(bar)
            self.stack.setParent(None); lay.addWidget(self.stack, 1)   # overlays ride along
            w.keyPressEvent = lambda e: self._toggle_max() if e.key() == QtCore.Qt.Key_Escape else None
            def _closed(e):
                if self._max_win is not None: self._toggle_max()       # WM close = retract, never lose the stack
                e.accept()
            w.closeEvent = _closed
            w.showFullScreen()
            self.maxbtn.setText("⤢ retract")
        else:
            w, self._max_win = self._max_win, None
            self.stack.setParent(None)
            self._mid.insertWidget(0, self.stack, 1)                   # back to the original position
            w.deleteLater()
            self.maxbtn.setText("⛶ max")
        QtCore.QTimer.singleShot(150, self._place_overlays)
    # ── 🌐 +globe: an extra watcher globe in its own window, fed the same live data ──
    def _new_globe(self):
        gw = GlobeWidget()
        win = QtWidgets.QWidget(None)
        win.setWindowTitle("🌐 ₿ANKON Globe — watcher")
        lay = QtWidgets.QVBoxLayout(win); lay.setContentsMargins(2, 2, 2, 2); lay.addWidget(gw)
        win.resize(520, 540)
        gw.show_borders = self.globe.show_borders; gw.show_acc = self.globe.show_acc
        gw.my_tip = self.globe.my_tip
        gw.set_data(list(self.globe._nodes), self.globe._peers, self.globe._my)
        self._watchers.append((win, gw))
        def _closed(e, w=win):
            self._watchers = [(a, b) for (a, b) in self._watchers if a is not w]
            e.accept()
        win.closeEvent = _closed
        win.show()
    def close_aux_windows(self):
        for w, _g in list(self._watchers): w.close()
        if self._max_win is not None: self._toggle_max()
    def _nodeovl_dropped(self):
        # drop → snap-dock to the nearest stack corner (≤40 px) or keep the free spot; remember
        st = QtCore.QSettings("BANKON", "bankon-qt")
        g = self.node_overlay.geometry()
        sw, sh = self.stack.width(), self.stack.height()
        dock = "free"
        for k, (dx, dy) in {"TL": (g.left() - 10, g.top() - 10),
                            "TR": (sw - 10 - g.right(), g.top() - 10),
                            "BL": (g.left() - 10, sh - 10 - g.bottom()),
                            "BR": (sw - 10 - g.right(), sh - 10 - g.bottom())}.items():
            if abs(dx) < 40 and abs(dy) < 40:
                dock = k; break
        st.setValue("geomap/nodeovl", dock)
        st.setValue("geomap/nodeovl_geom", f"{g.x()},{g.y()},{g.width()}")
        self._place_overlays()
    # ── 🏠 local-node overlay: prominent, actual data — node/net/blocks toggles + time ──
    def _nodeinfo_changed(self, _on):
        st = QtCore.QSettings("BANKON", "bankon-qt")
        for key, c in (("ovl_node", self.ovl_node), ("ovl_net", self.ovl_net), ("ovl_blocks", self.ovl_blocks)):
            st.setValue("geomap/" + key, "true" if c.isChecked() else "false")
        self._update_nodeinfo()
    def _on_bci(self, bci, stale):
        self._bci = bci or {}
        self._update_nodeinfo()
    def _clock_tick(self):
        if self.node_overlay.isVisible() and anim_on(self):
            self.node_overlay.set_data({"clock": self._tzfmt(time.time(), "%H:%M:%S")
                                        + " " + self.tz_box.currentText()})
    def _update_nodeinfo(self):
        flags = [k for k, c in (("node", self.ovl_node), ("net", self.ovl_net),
                                ("blocks", self.ovl_blocks)) if c.isChecked()]
        self.node_overlay.set_flags(flags)
        self.node_overlay.setVisible(bool(flags))
        if not flags:
            return
        d = {"clock": self._tzfmt(time.time(), "%H:%M:%S") + " " + self.tz_box.currentText()}
        bci = self._bci or {}
        if bci:
            vp = bci.get("verificationprogress") or 0.0
            state = (" (FULL NODE)" if vp >= 0.9999 else
                     " (IBD)" if bci.get("initialblockdownload") else "")
            agent = ((self._ni or {}).get("subversion") or "").replace("/", "")
            d["node"] = (f"height {bci.get('blocks', 0):,} · sync {min(100.0, vp * 100):.2f}%{state}"
                         + (f" · {agent}" if agent else ""))
            if bci.get("time"):
                d["blocks"] = (f"tip {bci.get('blocks', 0):,} · age {human_dt(time.time() - bci['time'])}"
                               + (f" · headers {bci.get('headers', 0):,}" if bci.get("headers") else ""))
        peers = self._peers or []
        if peers or self._ni:
            inn = sum(1 for p in peers if p.get("inbound")); out = len(peers) - inn
            tin = sum(r[0] for r in self._rates.values()); tout = sum(r[1] for r in self._rates.values())
            la = (self._ni or {}).get("localaddresses") or []
            myip, approx = (la[0].get("address") if la else None), ""
            if not myip:
                from collections import Counter
                seen = Counter((p.get("addrlocal") or "").rsplit(":", 1)[0].strip("[]")
                               for p in peers if p.get("addrlocal"))
                myip = seen.most_common(1)[0][0] if seen else None
                approx = " (approx)" if myip else ""
            d["net"] = (f"{len(peers)} peers ({out} out · {inn} in) · live ▼ {NetworkMapTab._rate_s(tin)}"
                        f" ▲ {NetworkMapTab._rate_s(tout)}" + (f" · via {myip}{approx}" if myip else ""))
        self.node_overlay.set_data(d)
        self._place_overlays()
    def _price_tick(self):
        # casual cadence: act only when a NEW hour needs its mark, or the data is >65 min
        # stale; failures retry no sooner than 5 min — the free tier is never hammered
        now = time.time()
        if self._price_busy or now - self._price_try < 300:
            return
        cur_hr = int(now // 3600) * 3600
        have = {ts for ts, _ in self._price_series}
        if cur_hr not in have or now - self._price_last >= 3900:
            self._price_fetch(backfill=len(self._price_series) < 2)
    def _price_fetch(self, backfill=False):
        if self._price_busy:
            return
        self._price_busy = True; self._price_try = time.time()
        def work():
            from services.price_service import spot_usd, hourly_usd
            out = {"spot": spot_usd()}
            if backfill:
                out["hist"] = hourly_usd(days=1)      # last 24h, snapped on the hour
            return out
        spawn_fn(work, self._on_price, self._on_price_fail)
    def _on_price(self, d):
        self._price_busy = False
        d = d or {}
        merged = dict(self._price_series)
        merged.update(dict(d.get("hist") or []))
        sp = d.get("spot") or {}
        if sp.get("usd") is not None:
            self._price_last = time.time()
            # the first spot seen inside each hour stamps that hour → marked ON THE HOUR
            merged.setdefault(int(self._price_last // 3600) * 3600, float(sp["usd"]))
        self._price_series = sorted(merged.items())[-26:]
        self.price_overlay.set_data(self._price_series,
                                    (float(sp["usd"]), sp.get("at") or self._price_last) if sp.get("usd") is not None else None)
    def _on_price_fail(self, err):
        self._price_busy = False
        self.price_overlay.set_error(f"CoinGecko unreachable — retrying (casual): {str(err)[:60]}")
    # ── GeoIP accuracy: radius (km) → on-map ellipse for the ACTIVE projection ──
    def _acc_px(self, lat, km):
        if self.projmode == "flatearth":
            # AE from the pole: radial scale is exact (20015 km pole→rim); tangential scale
            # stretches by colat/sin(colat) — return (radial, tangential) for a rotated ellipse
            rr = km * self._AE_R / 20015.0
            colat = math.radians(90 - lat)
            stretch = colat / math.sin(colat) if 0.01 < colat < math.pi - 0.01 else 1.0
            return rr, rr * min(4.0, stretch)
        rx = km / (111.32 * max(0.2, math.cos(math.radians(lat)))) / 360 * self.W
        ry = km / 111.32 / 180 * self.H
        return rx, ry
    def _draw_acc(self, x, y, lat, km, col):
        """Dashed GeoIP-accuracy circle under a located dot — flatearth gets the properly
        ROTATED ellipse (radial axis toward the pole), plate carrée an axis-aligned one."""
        rx, ry = self._acc_px(lat, km)
        if max(rx, ry) < 2.5:
            return
        pen = QtGui.QPen(QtGui.QColor(col.red(), col.green(), col.blue(), 80), 1, QtCore.Qt.DashLine)
        brush = QtGui.QBrush(QtGui.QColor(col.red(), col.green(), col.blue(), 14))
        it = self.scene.addEllipse(-rx, -ry, 2 * rx, 2 * ry, pen, brush)
        it.setPos(x, y)
        if self.projmode == "flatearth":
            cx, cy = self.W / 2, self.H / 2
            it.setRotation(math.degrees(math.atan2(y - cy, x - cx)))   # radial axis outward
    @staticmethod
    def _acc_tier(km):
        return ("precise" if km <= 20 else "city-level" if km <= 100
                else "region-level" if km <= 500 else "country-level")
    # Flat-map point projection → scene (x, y). Dispatches on the selected mode.
    _AE_R = 350.0
    def proj(self, lon, lat):
        if self.projmode == "flatearth":
            return azimuthal_equidistant(lat, lon, self.W / 2, self.H / 2, self._AE_R)
        return ((lon + 180) / 360 * self.W, (90 - lat) / 180 * self.H)
    def refresh(self):
        spawn("getpeerinfo", self._on_peers, timeout=10)
        spawn("getnetworkinfo", self._on_ni, timeout=8)
        spawn("getblockchaininfo", self._on_bci, timeout=10)   # 🏠 overlay: height/sync/tip age
        if self.feed_chk.isChecked():                          # 🧾 feed: mempool Δ + blocks
            spawn("getmempoolinfo", self._on_mpi, timeout=8)
            spawn_fn(lambda: fetch_json("/api/recentblocks?n=6").get("blocks", []), self._on_feed_blocks)
        if self.marks_chk.isChecked():                         # 🏆 the Net Map's knowledge
            spawn_fn(lambda: fetch_json("/api/node/fastnodes"), self._on_fast)
            spawn_fn(lambda: fetch_json("/api/node/favourites"), self._on_favs)
            spawn("listbanned", self._on_banned, timeout=10)
        if self.allnodes.isChecked():                        # off = peers only; on = whole addrman
            spawn_fn(lambda: known_nodes(5000), self._on_net)
        else:
            self._net = []; self._redraw()
        spawn_fn(lambda: fetch_json("/api/netactivity?n=60"), self._on_act)   # log-based geo fallback (works during choke)
    def _on_peers(self, peers, stale):
        import time as _t
        now = _t.time()
        for p in (peers or []):                       # real per-peer B/s between polls
            a = p.get("addr")
            prev = self._prev.get(a)
            if prev and now > prev[2]:
                dt = now - prev[2]
                self._rates[a] = (max(0, p.get("bytesrecv", 0) - prev[0]) / dt,
                                  max(0, p.get("bytessent", 0) - prev[1]) / dt)
            self._prev[a] = (p.get("bytesrecv", 0), p.get("bytessent", 0), now)
        self._peers = peers or []
        self._resolve_hostnames(self._peers)     # DNS-named peers → plottable (async, once/session)
        self._redraw()
    def _on_ni(self, ni, stale): self._ni = ni or {}; self._redraw()
    def _on_net(self, nodes): self._net = nodes or []; self._redraw()
    def _on_act(self, d):
        self._act = (d or {}).get("events", [])
        self._fill_act_feed()
        self._redraw()
    # ── 🏆 marks: Network / Net Map knowledge reused on the geo display ──
    def _marks_toggled(self, on):
        QtCore.QSettings("BANKON", "bankon-qt").setValue("geomap/marks", "true" if on else "false")
        if on: self.refresh()
        else: self._fast, self._favs, self._banned = {}, set(), []; self._redraw()
    def _on_fast(self, d):
        idx = (d or {}).get("nodes") or {}
        rows = sorted((v for v in (idx.values() if isinstance(idx, dict) else idx) if isinstance(v, dict)),
                      key=lambda x: (-(x.get("score") or 0), x.get("pingMs") or 9e9))[:12]
        self._fast = {r.get("addr"): (i, r.get("pingMs"), r.get("score") or 0) for i, r in enumerate(rows)}
        self._redraw()
    def _on_favs(self, d):
        self._favs = {v.get("addr") for v in ((d or {}).get("nodes") or []) if v.get("addr")}
        self._redraw()
    def _on_banned(self, rows, stale):
        self._banned = [(b.get("address", "").split("/")[0], b.get("banned_until", 0))
                        for b in (rows or [])][:50]
        self._redraw()
    # ── 📡/🧾 side feeds: OUTPUT from the local node's perspective ──
    def _feed_toggled(self, on):
        QtCore.QSettings("BANKON", "bankon-qt").setValue("geomap/feed", "true" if on else "false")
        self.feed.setVisible(on)
        if on: self.refresh()
    _ACT_ICO = {"connected": ("✚", "#16C784"), "inbound": ("◂", "#00BFFF"),
                "failed": ("✖", "#f85149"), "disconnect": ("−", "#F7931A"), "local": ("🏠", "#F7931A")}
    def _fill_act_feed(self):
        self.act_list.clear()
        for e in reversed((self._act or [])[-40:]):            # newest first
            ico, colr = self._ACT_ICO.get(e.get("kind"), ("·", "#8aa0b4"))
            when = (e.get("time") or "")[-8:]
            addr = (e.get("addr") or e.get("text") or "")[:26]
            ip = addr.rsplit(":", 1)[0].strip("[]")
            g = geolocate(ip) if is_ip_literal(ip) else None
            it = QtWidgets.QListWidgetItem(f"{when} {ico} {addr}" + (f" {flag(g['iso'])}" if g else ""))
            it.setForeground(QtGui.QColor(colr))
            it.setToolTip(f"{e.get('kind')} · {e.get('addr') or ''} · {e.get('subver') or ''}"
                          + (f" · {g['country']}" if g else ""))
            self.act_list.addItem(it)
    def _on_mpi(self, mp, stale):
        mp = mp or {}
        now = time.time()
        n = mp.get("size")
        if n is None: return
        line = f"mempool {n:,} tx · {mp.get('bytes', 0) / 1e6:.1f} vMB"
        if self._mp_prev and now > self._mp_prev[1]:
            dn = n - self._mp_prev[0]
            line += f" · Δ {dn:+,} in {human_dt(now - self._mp_prev[1])}"
        self._mp_prev = (n, now)
        self._mp_line = line
        self._fill_tx_feed()
    def _on_feed_blocks(self, blocks):
        self._feed_blocks = blocks or []
        self._fill_tx_feed()
    def _fill_tx_feed(self):
        self.tx_list.clear()
        if getattr(self, "_mp_line", None):
            it = QtWidgets.QListWidgetItem("🧾 " + self._mp_line)
            it.setForeground(QtGui.QColor("#F7931A")); self.tx_list.addItem(it)
        for b in reversed(getattr(self, "_feed_blocks", [])):  # newest block first
            when = self._tzfmt(b.get("time") or 0) if b.get("time") else "—"
            ntx = b.get("nTx")
            it = QtWidgets.QListWidgetItem(f"⛏ {b.get('height', 0):,} · "
                                           + (f"{ntx:,} tx · " if ntx else "") + when)
            it.setForeground(QtGui.QColor("#16C784"))
            it.setToolTip(b.get("hash", ""))
            self.tx_list.addItem(it)
    def _flow_pulse(self):
        # animated packets along the REAL great-circle arcs, scaled by ACTUAL per-peer B/s.
        # Same colour law as everywhere in ₿ANKON: orange = data IN (peer→node),
        # green = data OUT (node→peer). Gated by anim_on — zero cost when hidden.
        if not anim_on(self):
            return
        for it in self._fanim:
            try: self.scene.removeItem(it)
            except Exception: pass
        self._fanim = []
        if not self._flows:
            return
        self._fphase += 0.04
        # COMETS, not plain dots: the head rides the arc, a fading tail is laid along the
        # arc's own trailing samples — so the trail bends with the great circle and always
        # points against the direction of travel. Link QUALITY (ping) sets brightness.
        for k, (pts, pin, pout, q) in enumerate(self._flows):
            if len(pts) < 2: continue
            self._comet(pts, pin, QtGui.QColor("#F7931A"), True, k, q)    # IN: peer → node
            self._comet(pts, pout, QtGui.QColor("#16C784"), False, k, q)  # OUT: node → peer
    def _comet(self, pts, frac, col, inbound, k, q):
        """One direction's comets along a (possibly seam-broken) node→peer polyline."""
        if frac <= 0:
            return
        n = len(pts)
        npk = 1 + int(round(2 * frac)); spd = 0.5 + 2.0 * frac; rad = 1.6 + 2.4 * frac
        alpha = int(110 + 145 * q)
        tail = 2 + int(3 * frac)
        for j in range(npk):
            t = (self._fphase * spd + j / npk + k * (0.11 if inbound else 0.17)) % 1.0
            if inbound: t = 1.0 - t                       # IN runs the arc backwards
            f = t * (n - 1); i0 = int(f); fr = f - i0
            a, b = pts[i0], pts[min(i0 + 1, n - 1)]
            if a is None or b is None: continue           # antimeridian seam break
            hx, hy = a[0] + (b[0] - a[0]) * fr, a[1] + (b[1] - a[1]) * fr
            step = 1 if inbound else -1                   # tail trails BEHIND the travel direction
            px, py = hx, hy
            for s in range(1, tail + 1):
                ii = i0 + step * s
                if not (0 <= ii < n) or pts[ii] is None: break
                fade = 1 - s / (tail + 1.0)
                pen = QtGui.QPen(QtGui.QColor(col.red(), col.green(), col.blue(),
                                              int(alpha * fade * 0.55)), max(0.8, rad * fade))
                self._fanim.append(self.scene.addLine(px, py, pts[ii][0], pts[ii][1], pen))
                px, py = pts[ii][0], pts[ii][1]
            self._fanim.append(self.scene.addEllipse(hx - rad, hy - rad, 2 * rad, 2 * rad,
                                                     QtGui.QPen(QtCore.Qt.NoPen),
                                                     QtGui.QBrush(QtGui.QColor(col.red(), col.green(), col.blue(), alpha))))
    def _my_latlon(self):
        la = (self._ni or {}).get("localaddresses") or []
        for a in la:
            g = geolocate(a.get("address", ""))
            if g:
                self._my_src = "localaddress"; self._my_acc = g.get("acc")
                return g["lat"], g["lon"]
        # not publicly reachable → locate by the address our peers report they see us as
        from collections import Counter
        seen = Counter((p.get("addrlocal") or "").rsplit(":", 1)[0].strip("[]")
                       for p in (self._peers or []) if p.get("addrlocal"))
        for ip, _n in seen.most_common(3):
            g = geolocate(ip)
            if g:
                self._my_src = "addrlocal"    # NAT'd — this is the ISP egress, mark it approx
                self._my_acc = g.get("acc")
                return g["lat"], g["lon"]
        self._my_src = ""; self._my_acc = None
        return None
    def _build_bg(self):
        """World + graticule + the whole known network as a dim density layer (one pixmap)."""
        pm = QtGui.QPixmap(self.W, self.H); pm.fill(QtGui.QColor("#071019"))
        qp = QtGui.QPainter(pm); qp.setRenderHint(QtGui.QPainter.Antialiasing)
        if self.projmode == "flatearth":
            # ocean disc first, so the flat-earth reads as a distinct body on the scene
            qp.setPen(QtGui.QPen(QtGui.QColor("#16324a"), 1.4)); qp.setBrush(QtGui.QBrush(QtGui.QColor("#0a1723")))
            qp.drawEllipse(QtCore.QPointF(self.W / 2, self.H / 2), self._AE_R, self._AE_R)
        qp.setPen(QtGui.QPen(QtGui.QColor("#16324a"))); qp.setBrush(QtGui.QBrush(QtGui.QColor("#0c2236")))
        for poly in WORLD:
            # Flat-earth (AE): great-circle-densify edges so continents curve correctly.
            ring = densify_latlon(poly) if self.projmode == "flatearth" else poly
            if self.projmode == "flatearth" and any(la <= -89 for _lo, la in poly):
                # north-pole AE: the Antarctica ring closes ACROSS the south pole — filled,
                # it would paint the whole disc as land and hide every continent. Draw its
                # coastline (points short of the pole seam) as a rim polyline instead.
                pts = [QtCore.QPointF(*self.proj(lo, la)) for lo, la in ring if la > -85]
                if len(pts) >= 2:
                    qp.drawPolyline(QtGui.QPolygonF(pts))
                continue
            qp.drawPolygon(QtGui.QPolygonF([QtCore.QPointF(*self.proj(lo, la)) for lo, la in ring]))
        # POLITICAL overlay — international boundary polylines on top of the land fill,
        # antimeridian-safe (segment break on wrap), densified in AE mode like the coasts.
        if getattr(self, "borders_chk", None) and self.borders_chk.isChecked() and WORLD_BORDERS:
            qp.setBrush(QtCore.Qt.NoBrush)
            qp.setPen(QtGui.QPen(QtGui.QColor(90, 107, 123, 150), 1.0))     # #5a6b7b corporate grey
            for line in WORLD_BORDERS:
                seg = densify_latlon(line) if self.projmode == "flatearth" else line
                px = None; path = QtGui.QPainterPath(); started = False
                for lo, la in seg:
                    x, y = self.proj(lo, la)
                    if px is not None and abs(x - px) > self.W / 2:
                        started = False                                     # break at the seam
                    if not started: path.moveTo(x, y); started = True
                    else: path.lineTo(x, y)
                    px = x
                qp.drawPath(path)
        qp.setPen(QtGui.QPen(QtGui.QColor("#0e2a3d")))
        qp.setBrush(QtCore.Qt.NoBrush)     # graticule is LINES — a filled boundary circle would repaint the disc over the land
        if self.projmode == "flatearth":
            cx, cy = self.W / 2, self.H / 2
            for la in range(-60, 91, 30):                     # concentric parallels
                rho = self._AE_R * (90 - la) / 180.0
                qp.drawEllipse(QtCore.QPointF(cx, cy), rho, rho)
            for lo in range(-180, 180, 30):                    # radial meridians
                x, y = self.proj(lo, -90)
                qp.drawLine(int(cx), int(cy), int(x), int(y))
            qp.setPen(QtGui.QPen(QtGui.QColor("#16324a")))     # equator + boundary
            qp.drawEllipse(QtCore.QPointF(cx, cy), self._AE_R / 2, self._AE_R / 2)
            qp.drawEllipse(QtCore.QPointF(cx, cy), self._AE_R, self._AE_R)
            # labeled graticule — the AE disc reads as a chart, not just rings: parallels
            # tagged along the NE diagonal, cardinal meridians tagged inside the rim
            f = qp.font(); f.setPointSize(8); qp.setFont(f)
            qp.setPen(QtGui.QColor("#3d5a70"))
            for la in (60, 30, 0, -30, -60):
                rho = self._AE_R * (90 - la) / 180.0
                qp.drawText(int(cx + rho * 0.7071) + 3, int(cy - rho * 0.7071) - 2, f"{la}°")
            for lo, tag in ((0, "0°"), (90, "90°E"), (180, "180°"), (-90, "90°W")):
                x, y = self.proj(lo, -78)
                qp.drawText(int(x) - 12, int(y) + 4, tag)
        else:
            for lo in range(-150, 181, 30):
                x, _ = self.proj(lo, 0); qp.drawLine(int(x), 0, int(x), self.H)
            for la in range(-60, 91, 30):
                _, y = self.proj(0, la); qp.drawLine(0, int(y), self.W, int(y))
        qp.setPen(QtCore.Qt.NoPen); qp.setBrush(QtGui.QBrush(QtGui.QColor(90, 160, 190, 70)))  # dim density
        for nd in self._net:
            x, y = self.proj(nd["lon"], nd["lat"]); qp.drawEllipse(QtCore.QPointF(x, y), 1.7, 1.7)
        qp.end()
        self._bg = pm; self._bg_n = len(self._net)
    def _redraw(self):
        from collections import Counter
        self.scene.clear(); self._fanim = []; self._flows = []
        if self._bg is None or self._bg_n != len(self._net):
            self._build_bg()
        self.scene.addPixmap(self._bg)
        my = self._my_latlon()
        show_conn = self.connected.isChecked() if hasattr(self, "connected") else True
        # great-circle arcs from our node to each connected peer (inferred edges)
        if my and show_conn:
            mx, my_y = self.proj(my[1], my[0])
            arc_pen = QtGui.QPen(QtGui.QColor(247, 147, 26, 120), 1.0)
            self._flows = []
            for p in self._peers:
                ip, _host = self._peer_ip(p)
                g = geolocate(ip) if ip else None
                if not g: continue
                path = QtGui.QPainterPath(); started = False; px = None
                pts = []                                            # node→peer arc for packet flow
                for la, lo in great_circle_points(my[0], my[1], g["lat"], g["lon"], 40):
                    x, y = self.proj(lo, la)
                    if px is not None and abs(x - px) > self.W / 2:   # antimeridian wrap
                        started = False
                        pts.append(None)                              # break the flow at the seam too
                    if not started: path.moveTo(x, y); started = True
                    else: path.lineTo(x, y)
                    px = x; pts.append((x, y))
                self.scene.addPath(path, arc_pen)
                ri, ro = self._rates.get(p.get("addr"), (0.0, 0.0))
                pin, pout = NetworkMapTab._flow_frac(ri), NetworkMapTab._flow_frac(ro)
                if pin > 0 or pout > 0:
                    self._flows.append((pts, pin, pout, link_quality(p)))
            _macc = getattr(self, "_my_acc", None)
            if _macc and self.acc_chk.isChecked():
                self._draw_acc(mx, my_y, my[0], _macc, QtGui.QColor("#F7931A"))
            mk = self.scene.addEllipse(mx - 6, my_y - 6, 12, 12, QtGui.QPen(QtGui.QColor("#F7931A"), 2), QtGui.QBrush(QtGui.QColor("#1a1200")))
            _nc = nearest_city(my[0], my[1])
            # honesty: an addrlocal-derived position is the ISP egress, not this machine
            _src = " · (approx — ISP egress, from addrlocal)" if getattr(self, "_my_src", "") == "addrlocal" else ""
            _accs = f" · ±{_macc:,} km ({self._acc_tier(_macc)})" if _macc else ""
            mk.setToolTip(f"bankon: this node · nearest city: {_nc[0]}, {_nc[1]} (~{_nc[2]:.0f} km){_accs}{_src}")
            self.globe.my_tip = mk.toolTip()             # same truth on the globe's hover
        # connected peers on top, coloured by traffic/direction, ASN in tooltip.
        # EVERY connected peer is accounted for: located ones at their true position, the rest
        # (Tor / I2P / unmapped IPs) in an honest strip — never silently dropped.
        cc, asncc, located, gpeers = Counter(), Counter(), 0, []
        unlocated, accs = [], []
        _citymarks = {}          # (name, iso) -> city entry — deduped nearest-major-city labels
        for p in (self._peers if show_conn else []):
            ip, _host = self._peer_ip(p)
            g = geolocate(ip) if ip else None
            if not g:
                unlocated.append(p)
                continue
            located += 1; cc[g["iso"]] += 1
            an = asn_lookup(ip) or {}
            _dnsnote = "  ·  (DNS-named — location via re-resolve, approximate)" if _host != ip else ""
            if an.get("org"): asncc[an["org"][:22]] += 1
            x, y = self.proj(g["lon"], g["lat"])
            traf = p.get("bytessent", 0) + p.get("bytesrecv", 0); inbound = p.get("inbound")
            col = QtGui.QColor("#16C784") if traf > (1 << 20) else (QtGui.QColor("#F7931A") if inbound else QtGui.QColor("#00BFFF"))
            r = 5 + min(6, traf / (1 << 21))
            acc = g.get("acc") or 0
            if acc: accs.append(acc)
            if acc and self.acc_chk.isChecked():
                self._draw_acc(x, y, g["lat"], acc, col)     # the ADDRESS is inside this circle
            self.scene.addEllipse(x - r - 3, y - r - 3, 2 * (r + 3), 2 * (r + 3), QtGui.QPen(QtCore.Qt.NoPen), QtGui.QBrush(QtGui.QColor(col.red(), col.green(), col.blue(), 60)))
            d = self.scene.addEllipse(x - r, y - r, 2 * r, 2 * r, QtGui.QPen(QtGui.QColor("#eef3f8"), 1), QtGui.QBrush(col))
            # city truth ladder: the mmdb's own per-IP city name first, then the nearest city
            # from the complete GeoNames list (population shown when the full dataset is live)
            _ce = nearest_city_entry(g["lat"], g["lon"])
            _pop = f", pop {_ce[5]:,}" if len(_ce) > 5 and _ce[5] else ""
            _city = f"{g['city']} (GeoIP)" if g.get("city") else f"near {_ce[0]} (~{_ce[4]:.0f} km{_pop})"
            _accs = f"±{acc:,} km ({self._acc_tier(acc)})" if acc else "accuracy unknown"
            _ping = f"{p.get('pingtime', 0) * 1000:.0f} ms" if p.get("pingtime") else "ping ?"
            _up = human_dt(time.time() - p["conntime"]) if p.get("conntime") else "?"
            # 🏆 marks — Net Map knowledge on THIS display: ★ promoted ring + fastest medal
            _addr = p.get("addr")
            _fav = self.marks_chk.isChecked() and (_addr in self._favs or p.get("addnode"))
            _fr = self._fast.get(_addr) if self.marks_chk.isChecked() else None
            _medal = "🥇🥈🥉"[_fr[0]] if _fr and _fr[0] < 3 else ""
            if _fav:
                self.scene.addEllipse(x - r - 4, y - r - 4, 2 * (r + 4), 2 * (r + 4),
                                      QtGui.QPen(QtGui.QColor("#FFD37A"), 2), QtGui.QBrush(QtCore.Qt.NoBrush))
            if _medal:
                _mt = self.scene.addSimpleText(_medal); _mt.setPos(x - r - 16, y - 8)
            d.setToolTip(f"{p.get('addr')}  ·  {flag(g['iso'])} {g['country']}  ·  {_city}  ·  {_accs}  ·  "
                         f"AS{an.get('asn','?')} {an.get('org','')}  ·  {(traf/1048576):.1f} MiB  ·  "
                         f"{_ping}  ·  up {_up}  ·  {'in' if inbound else 'out'}{_dnsnote}")
            # globe payload: position/size/colour + accuracy + LIVE flow fractions + link
            # quality + the political-mode label + the FULL hover card (ip · location ·
            # accuracy · speed: ping + live ▼/▲ B/s · traffic · direction)
            _ri, _ro = self._rates.get(p.get("addr"), (0.0, 0.0))
            gpeers.append({"lat": g["lat"], "lon": g["lon"], "col": col, "r": max(4.0, r), "acc": acc,
                           "pin": NetworkMapTab._flow_frac(_ri), "pout": NetworkMapTab._flow_frac(_ro),
                           "q": link_quality(p),
                           "label": ((p.get("addr") or "")[:28],
                                     f"{g.get('city') or _ce[0]}, {g['country']} · {_ping}"),
                           "rate_s": (f"▼ {NetworkMapTab._rate_s(_ri)} ▲ {NetworkMapTab._rate_s(_ro)}"
                                      if (_ri >= 256 or _ro >= 256) else ""),
                           "fav": bool(_fav), "medal": _medal,
                           "tip": (f"{('★ ' if _fav else '')}{_medal}{p.get('addr')}\n"
                                   f"{flag(g['iso'])} {g['country']} · {g.get('city') or _ce[0]}  ·  {_accs}\n"
                                   f"AS{an.get('asn','?')} {an.get('org','')}"
                                   + (f" · {(p.get('subver') or '').strip('/')}" if p.get("subver") else "") + "\n"
                                   f"speed: {_ping} · live ▼ {NetworkMapTab._rate_s(_ri)} ▲ {NetworkMapTab._rate_s(_ro)}\n"
                                   + (f"🏆 fastest #{_fr[0] + 1} · score {_fr[2]}"
                                      + (f" · measured {_fr[1]:.0f} ms" if _fr[1] is not None else "") + "\n" if _fr else "")
                                   + f"{(traf/1048576):.1f} MiB total · up {_up} · "
                                   f"{'inbound' if inbound else 'outbound'}{_dnsnote.strip()}")})
            if self.cities_chk.isChecked() and _ce:
                _citymarks[(_ce[0], _ce[1])] = _ce
        # 🏙 optional overlay: nearest MAJOR CITY per located peer — drawn at the city's own
        # coordinates (not the peer's), deduped, capped for legibility
        for _ce in list(_citymarks.values())[:40]:
            cx, cy = self.proj(_ce[3], _ce[2])
            self.scene.addEllipse(cx - 1.6, cy - 1.6, 3.2, 3.2,
                                  QtGui.QPen(QtCore.Qt.NoPen), QtGui.QBrush(QtGui.QColor("#9F6BFF")))
            ct = self.scene.addSimpleText(_ce[0])
            ct.setBrush(QtGui.QColor(196, 162, 255, 200)); ct.setScale(0.66)   # sparse polygon purple
            ct.setPos(cx + 3, cy - 5)
        # 🚫 banned addresses (listbanned — the Net Map's list) at their geolocation
        if self.marks_chk.isChecked():
            for _bip, _until in self._banned:
                gb = geolocate(_bip)
                if not gb: continue
                bx, by = self.proj(gb["lon"], gb["lat"])
                bt = self.scene.addSimpleText("✖")
                bt.setBrush(QtGui.QColor("#f85149")); bt.setPos(bx - 4, by - 7)
                bt.setToolTip(f"🚫 banned {_bip} · {flag(gb['iso'])} {gb['country']} · until "
                              + datetime.fromtimestamp(_until, timezone.utc).strftime("%m-%d %H:%M"))
        if unlocated:
            uy = self.H - 16
            _ndns = sum(1 for p in unlocated if not is_ip_literal((p.get("addr") or "").rsplit(":", 1)[0].strip("[]")))
            _kinds = ("DNS-named (resolving…) / " if _ndns else "") + "tor / unmapped"
            lab = self.scene.addSimpleText(f"⚫ no geo data ({len(unlocated)}): {_kinds} —")
            lab.setBrush(QtGui.QColor("#8aa0b4")); lab.setPos(8, uy - 4)
            ux = 8 + lab.boundingRect().width() + 10
            for p in unlocated[:12]:
                d = self.scene.addEllipse(ux, uy, 8, 8, QtGui.QPen(QtGui.QColor("#5a6b7b"), 1),
                                          QtGui.QBrush(QtGui.QColor("#22303c")))
                d.setToolTip(f"{p.get('addr')} · no geolocation (Tor/I2P/unmapped) · "
                             f"{((p.get('bytessent',0)+p.get('bytesrecv',0))/1048576):.1f} MiB · "
                             f"{'in' if p.get('inbound') else 'out'}")
                ux += 14
            if len(unlocated) > 12:
                more = self.scene.addSimpleText(f"+{len(unlocated) - 12}")
                more.setBrush(QtGui.QColor("#8aa0b4")); more.setPos(ux + 2, uy - 4)
        # Log-based connection ACTIVITY — geolocate recent connect/fail events when the peer RPC is
        # choked, so the geo map / globe isn't empty during IBD. connected=green · failed=red · inbound=blue.
        act_plotted = 0
        if located == 0 and self._act:
            AC = {"connected": QtGui.QColor("#16C784"), "failed": QtGui.QColor("#f85149"), "inbound": QtGui.QColor("#00BFFF")}
            for e in self._act:
                ip = (e.get("addr") or "").rsplit(":", 1)[0].strip("[]")
                if not ip: continue
                g = geolocate(ip)
                if not g: continue
                col = AC.get(e.get("kind"), QtGui.QColor("#8aa0b4"))
                x, y = self.proj(g["lon"], g["lat"])
                self.scene.addEllipse(x - 6, y - 6, 12, 12, QtGui.QPen(QtCore.Qt.NoPen), QtGui.QBrush(QtGui.QColor(col.red(), col.green(), col.blue(), 60)))
                dd = self.scene.addEllipse(x - 4, y - 4, 8, 8, QtGui.QPen(QtGui.QColor("#eef3f8"), 1), QtGui.QBrush(col))
                dd.setToolTip(f"{e.get('kind')} {e.get('addr')} · {flag(g['iso'])} {g['country']}"
                              + (f" · ±{g['acc']:,} km" if g.get("acc") else ""))
                gpeers.append({"lat": g["lat"], "lon": g["lon"], "col": col, "r": 5.0,
                               "acc": g.get("acc") or 0,
                               "label": ((e.get("addr") or "")[:28],
                                         f"{g.get('city') or '?'}, {g['country']}"),
                               "tip": (f"{e.get('kind')} {e.get('addr')}\n"
                                       f"{flag(g['iso'])} {g['country']} · {g.get('city') or '?'}\n"
                                       "log-based connection event (peer RPC busy)")})
                act_plotted += 1
        # AE disc: fit the view to the DISC (square scene rect), not the 2:1 pixmap —
        # otherwise the flat-earth occupies only the centre of a wide letterboxed scene
        if self.projmode == "flatearth":
            cx, cy, m = self.W / 2, self.H / 2, self._AE_R + 18
            self.scene.setSceneRect(cx - m, cy - m, 2 * m, 2 * m)
        else:
            self.scene.setSceneRect(0, 0, self.W, self.H)
        self.view.fitInView(self.scene.sceneRect(), QtCore.Qt.KeepAspectRatio)
        net_age = network_asof()
        net_when = (self._tzfmt(net_age) + f" {self.tz_box.currentText()}") if net_age else "—"
        # honest accuracy statement for the whole picture: median GeoLite2 radius of what's drawn
        _amed = f" · GeoIP median ±{sorted(accs)[len(accs) // 2]:,} km" if accs else ""
        if located == 0 and act_plotted:
            self.info.setText(f"Geo map — peer RPC busy (IBD); plotting {act_plotted} geolocated connection events from the log · "
                              "geo approximate (EPSG:4326)" + ("" if HAVE_GEOIP else "  (GeoIP DB missing)"))
        else:
            _unl = len(unlocated)
            _kn = len(self._net or [])
            self.info.setText(
                (f"Network {_kn:,} known nodes (addrman @ {net_when}"
                 + (f" · globe draws {min(700, _kn)}" if _kn > 700 else "") + ") · " if self._net else "") +
                f"connected: {located} located" + (f" + {_unl} no-geo (tor/unmapped)" if _unl else "") +
                f" of {len(self._peers)} · {len(cc)} countries{_amed} · orange dots = data IN · green = data OUT · "
                "arcs inferred · geo approximate (EPSG:4326)" + ("" if HAVE_GEOIP else "  (GeoIP DB missing)"))
        top_c = "  ".join(f"{flag(iso)} {iso} {n}" for iso, n in cc.most_common(12))
        top_a = "  ·  ".join(f"{o} {n}" for o, n in asncc.most_common(4))
        # peers by SPEED (measured ping) and by UPTIME (connection age) — ALL connected peers,
        # Tor/unmapped included, so these tallies are complete even when geolocation isn't
        _pings = sorted(p["pingtime"] * 1000 for p in self._peers if p.get("pingtime"))
        _now = time.time()
        _ups = sorted(_now - p["conntime"] for p in self._peers if p.get("conntime"))
        spd = ""
        if _pings:
            b = [sum(1 for v in _pings if lo <= v < hi) for lo, hi in ((0, 100), (100, 300), (300, 1e9))]
            spd = (f"     by speed (ping): ⚡<100ms {b[0]} · 100–300ms {b[1]} · 🐢≥300ms {b[2]}"
                   f" · median {_pings[len(_pings) // 2]:.0f} ms")
        upt = ""
        if _ups:
            u = [sum(1 for v in _ups if lo <= v < hi) for lo, hi in ((0, 3600), (3600, 86400), (86400, 1e12))]
            upt = (f"     by uptime: <1h {u[0]} · 1–24h {u[1]} · >24h {u[2]}"
                   f" · median {human_dt(_ups[len(_ups) // 2])}")
        self.legend.setText(f"peers by country (located only): {top_c or '—'}"
                            + (f"     top ASNs: {top_a}" if top_a else "") + spd + upt)
        # feed the spinning globe (same data, projected onto the sphere) — and every watcher
        self.globe.set_data([(n["lat"], n["lon"]) for n in self._net], gpeers, my)
        for _w, _gw in self._watchers:
            _gw.my_tip = self.globe.my_tip
            _gw.set_data(list(self.globe._nodes), gpeers, my)
        self._update_nodeinfo()                 # 🏠 overlay rides the same truth
    def resizeEvent(self, e):
        if self.scene.sceneRect().width(): self.view.fitInView(self.scene.sceneRect(), QtCore.Qt.KeepAspectRatio)
        self._place_overlays()
        super().resizeEvent(e)


def human_dt(s):
    s = max(0, int(s))
    if s < 60: return f"{s}s"
    if s < 3600: return f"{s//60}m {s%60}s"
    if s < 86400: return f"{s//3600}h {(s%3600)//60}m"
    d = s // 86400
    return f"{d}d {(s%86400)//3600}h" if d < 365 else f"{d/365:.2f} yr"

def _now():
    return datetime.now(timezone.utc).timestamp()

def _parse_iso(s):
    try: return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except Exception: return 0


class MeshPanel(QtWidgets.QWidget):
    """₿TC.oracle graphical area — a fine electric-blue mesh with an animated shimmer sweep, the
    recent block-interval sparkline, and the headline average block time (drawn with QPainter)."""
    def __init__(self):
        super().__init__()
        self.setMinimumHeight(180)                                         # shrunk from 300
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        self._phase = 0.0; self._series = []; self._headline = "—"; self._sub = ""
        self._t = QtCore.QTimer(self); self._t.timeout.connect(self._tick); self._t.start(50)  # shimmer ~20 fps
    def _tick(self):
        if not anim_on(self): return           # THERMAL: no 20 fps shimmer while hidden/minimized
        self._phase = (self._phase + 0.010) % 1.0; self.update()
    def set_series(self, vals): self._series = vals or []; self.update()
    def set_headline(self, h, sub=""): self._headline = h; self._sub = sub; self.update()
    def paintEvent(self, e):
        qp = QtGui.QPainter(self); qp.setRenderHint(QtGui.QPainter.Antialiasing)
        w, h = self.width(), self.height()
        qp.fillRect(0, 0, w, h, QtGui.QColor("#04070c"))
        step = 18
        qp.setPen(QtGui.QPen(QtGui.QColor(0, 191, 255, 30), 1))            # fine electric-blue mesh
        x = 0
        while x <= w: qp.drawLine(x, 0, x, h); x += step
        y = 0
        while y <= h: qp.drawLine(0, y, w, y); y += step
        # headline avg block time lives in the TOP band; the timeline gets the bottom band
        head_h = int(h * 0.46)
        cx = self._phase * (w + 240) - 120                                 # shimmer band sweeping the headline
        gx = 0
        while gx <= w:
            d = abs(gx - cx)
            if d < 100:
                qp.setPen(QtGui.QPen(QtGui.QColor(150, 228, 255, int(120 * (1 - d / 100))), 1.3)); qp.drawLine(gx, 0, gx, head_h)
            gx += step
        throb = 0.5 + 0.5 * math.sin(self._phase * 2 * math.pi * 2)
        gr = 58 + 30 * throb
        gg = QtGui.QRadialGradient(w / 2, head_h / 2, gr)
        gg.setColorAt(0, QtGui.QColor(0, 191, 255, int(45 + 55 * throb))); gg.setColorAt(1, QtGui.QColor(0, 191, 255, 0))
        qp.setPen(QtCore.Qt.NoPen); qp.setBrush(QtGui.QBrush(gg))
        qp.drawEllipse(QtCore.QPointF(w / 2, head_h / 2), gr, gr * 0.5)
        qp.setPen(QtGui.QColor("#eef3f8"))                                  # headline: avg block time
        f = qp.font(); f.setPointSize(20 + int(4 * throb)); f.setBold(True); qp.setFont(f)
        qp.drawText(QtCore.QRectF(0, 4, w, head_h - 22), QtCore.Qt.AlignCenter, self._headline)
        f.setPointSize(9); f.setBold(False); qp.setFont(f); qp.setPen(QtGui.QColor("#8aa0b4"))
        qp.drawText(QtCore.QRectF(0, head_h - 20, w, 18), QtCore.Qt.AlignCenter, self._sub)
        self._paint_timeline(qp, w, h, head_h)
        qp.end()
    @staticmethod
    def _nice_axis(want):
        # (axis-max, major-step, minor-step) — all WHOLE minutes. minor = single-minute grid,
        # major = labeled 5-minute lines; steps coarsen only when the range zooms out.
        for top, major, minor in [(15, 5, 1), (20, 5, 1), (30, 10, 2), (45, 15, 5),
                                  (60, 15, 5), (90, 30, 10), (120, 30, 10)]:
            if want <= top: return top, major, minor
        import math as _m
        top = int(_m.ceil(want / 30) * 30); return top, 30, 10
    def _paint_timeline(self, qp, w, h, top):
        # block-interval timeline — minutes between recent blocks, with the 10-minute protocol target
        L, R, B, T = 40, w - 12, h - 16, top + 10                          # plot rect (leave room for labels)
        if R - L < 20 or B - T < 12: return
        f = qp.font(); f.setPointSize(8); f.setBold(False); qp.setFont(f)
        s = self._series
        avg = (sum(s) / len(s)) if s else 0.0
        # scale around the ACTUAL average (baseline 15m so a ~10m average sits ~2/3 up); outliers clip
        vmax, major, minor = self._nice_axis(max(15.0, avg * 1.5))
        yof = lambda v: B - (min(v, vmax) / vmax) * (B - T)
        # minor gridlines — single-minute detail (faint, unlabeled)
        val = 0.0
        while val <= vmax + 0.1:
            if val % major > 0.01:                                          # skip where a major line lands
                gy = yof(val); qp.setPen(QtGui.QPen(QtGui.QColor(90, 120, 145, 26), 1))
                qp.drawLine(int(L), int(gy), int(R), int(gy))
            val += minor
        # major gridlines every 5 min — labeled; the 10-min protocol target is highlighted
        val = 0.0
        while val <= vmax + 0.1:
            gy = yof(val); is_target = (abs(val - 10) < 0.01)
            qp.setPen(QtGui.QPen(QtGui.QColor(0, 191, 255, 150) if is_target else QtGui.QColor(90, 120, 145, 70),
                                 1, QtCore.Qt.DashLine if is_target else QtCore.Qt.SolidLine))
            qp.drawLine(int(L), int(gy), int(R), int(gy))
            qp.setPen(QtGui.QColor("#00BFFF") if is_target else QtGui.QColor("#5a7891"))
            qp.drawText(2, int(gy) + 4, f"{val:g}m")
            val += major
        if len(s) > 1:
            n = len(s); xof = lambda i: L + i / (n - 1) * (R - L)
            # area fill under the curve (orange gradient)
            area = QtGui.QPainterPath(); area.moveTo(L, B)
            for i, v in enumerate(s): area.lineTo(xof(i), yof(v))
            area.lineTo(R, B); area.closeSubpath()
            ag = QtGui.QLinearGradient(0, T, 0, B)
            ag.setColorAt(0, QtGui.QColor(247, 147, 26, 90)); ag.setColorAt(1, QtGui.QColor(247, 147, 26, 8))
            qp.setPen(QtCore.Qt.NoPen); qp.setBrush(QtGui.QBrush(ag)); qp.drawPath(area)
            # the interval curve
            line = QtGui.QPainterPath(); line.moveTo(xof(0), yof(s[0]))
            for i in range(1, n): line.lineTo(xof(i), yof(s[i]))
            qp.setPen(QtGui.QPen(QtGui.QColor("#F7931A"), 2)); qp.setBrush(QtCore.Qt.NoBrush); qp.drawPath(line)
            # series average — measured from the raw block-time seconds, so show m + s precision
            ay = yof(avg)
            am = int(avg); asec = int(round((avg - am) * 60))
            if asec == 60: am += 1; asec = 0
            qp.setPen(QtGui.QPen(QtGui.QColor(255, 211, 122, 170), 1, QtCore.Qt.DotLine))
            qp.drawLine(int(L), int(ay), int(R), int(ay))
            qp.setPen(QtGui.QColor("#FFD37A"))
            qp.drawText(QtCore.QRectF(L + 4, ay - 14, 104, 12), QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter,
                        f"avg {am}m {asec:02d}s")
            # latest-point marker + its value
            lx, ly, lv = xof(n - 1), yof(s[-1]), s[-1]
            qp.setBrush(QtGui.QBrush(QtGui.QColor("#FFD37A"))); qp.setPen(QtGui.QPen(QtGui.QColor("#F7931A"), 1.5))
            qp.drawEllipse(QtCore.QPointF(lx, ly), 3.2, 3.2)
            qp.setPen(QtGui.QColor("#FFD37A"))
            qp.drawText(QtCore.QRectF(lx - 60, ly - 18, 56, 14), QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter, f"{lv:.1f}m")
            qp.setPen(QtGui.QColor("#5a7891"))
            qp.drawText(int(L), int(B) + 13, f"◀ {n} block intervals")
        else:
            qp.setPen(QtGui.QColor("#5a7891"))
            qp.drawText(QtCore.QRectF(L, T, R - L, B - T), QtCore.Qt.AlignCenter, "collecting block intervals…")


class Collapsible(QtWidgets.QWidget):
    """Accordion row — a clickable header that expands a detail area, lazy-loading on first expand."""
    def __init__(self, title, on_expand=None):
        super().__init__()
        lay = QtWidgets.QVBoxLayout(self); lay.setContentsMargins(0, 0, 0, 1); lay.setSpacing(0)
        self.btn = QtWidgets.QToolButton(); self.btn.setText(title); self.btn.setCheckable(True)
        self.btn.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon); self.btn.setArrowType(QtCore.Qt.RightArrow)
        self.btn.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        self.btn.setStyleSheet("QToolButton{border:1px solid #2e4a63;border-radius:5px;padding:5px;"
                               "text-align:left;background:#0e1620;color:#d6e3ef;font-family:monospace;font-size:12px}"
                               "QToolButton:hover{background:#14202e}")
        self.btn.toggled.connect(self._toggle); lay.addWidget(self.btn)
        self.area = QtWidgets.QWidget(); self.area.setVisible(False)
        self.al = QtWidgets.QVBoxLayout(self.area); self.al.setContentsMargins(12, 4, 4, 8); lay.addWidget(self.area)
        self._on_expand = on_expand; self._loaded = False
    def _toggle(self, on):
        self.btn.setArrowType(QtCore.Qt.DownArrow if on else QtCore.Qt.RightArrow); self.area.setVisible(on)
        if on and not self._loaded and self._on_expand:
            self._loaded = True
            lbl = QtWidgets.QLabel("loading block analysis…"); lbl.setStyleSheet("color:#8aa0b4"); self.al.addWidget(lbl)
            self._on_expand(self.al, lbl)


class BlockSciencePanel(QtWidgets.QFrame):
    """🔬 Scientific analysis of a SINGLE block — defaults to the CURRENT RUNNING TIP and follows it
    as new blocks arrive; any height on demand. A visual workflow rendered from actual node
    measurements only (getblockhash → getblockheader + getblockstats, no third party):
        ① identity → ② proof-of-work → ③ structure → ④ cryptnomics
    DeFi meets sci-fi, and ₿ANKON.oracle is accuracy: every figure below is measured, none estimated."""
    def __init__(self):
        super().__init__(); self.setObjectName("scienceframe")
        v = QtWidgets.QVBoxLayout(self)
        top = QtWidgets.QHBoxLayout()
        tt = QtWidgets.QLabel("🔬 ₿lock science — visual workflow from the actual block")
        tt.setStyleSheet("color:#00BFFF;font-weight:800"); top.addWidget(tt, 1)
        self.follow = QtWidgets.QCheckBox("follow tip"); self.follow.setChecked(True)
        self.follow.setToolTip("Re-analyze automatically as each new block arrives (the current running block)")
        top.addWidget(self.follow)
        self.hgt = QtWidgets.QLineEdit(); self.hgt.setPlaceholderText("height (blank = tip)")
        self.hgt.setMaximumWidth(130); self.hgt.returnPressed.connect(self.analyze); top.addWidget(self.hgt)
        go = QtWidgets.QPushButton("Analyze"); go.clicked.connect(self.analyze); top.addWidget(go)
        v.addLayout(top)
        self.head = QtWidgets.QLabel("① identity:  waiting for a block…")
        self.head.setStyleSheet("color:#F7931A;font-family:monospace;font-size:11px")
        self.head.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse); self.head.setWordWrap(True)
        v.addWidget(self.head)
        grid = QtWidgets.QGridLayout(); grid.setHorizontalSpacing(18); self.q = {}
        for col, (key, title) in enumerate([("pow", "② proof-of-work"), ("struct", "③ structure"), ("econ", "④ cryptnomics")]):
            lab = QtWidgets.QLabel(f"<b>{title}</b>"); lab.setStyleSheet("color:#00BFFF"); grid.addWidget(lab, 0, col)
            body = QtWidgets.QLabel("…"); body.setStyleSheet("font-family:monospace;font-size:11px;color:#c9d4e0")
            body.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse); body.setWordWrap(True)
            body.setAlignment(QtCore.Qt.AlignTop); grid.addWidget(body, 1, col); self.q[key] = body
        grid.setRowStretch(1, 1)
        v.addLayout(grid, 1)
        self.fullbar = QtWidgets.QProgressBar(); self.fullbar.setMaximum(4_000_000)
        self.fullbar.setFormat("block fullness — %v / 4,000,000 WU (%p%)"); v.addWidget(self.fullbar)
        self.feebar = QtWidgets.QLabel("fee percentiles: —")
        self.feebar.setStyleSheet("font-family:monospace;font-size:11px;color:#8aa0b4"); v.addWidget(self.feebar)
        self._analyzing = None; self._hdr = None; self._st = None; self._last_tip = None
    def maybe_tip(self, h):
        # follow-tip: re-analyze only when a NEW tip arrives and no manual height is pinned
        if self.follow.isChecked() and h and h != self._last_tip and not self.hgt.text().strip():
            self._go(h)
    def refresh(self): self.analyze()
    def analyze(self):
        txt = self.hgt.text().strip()
        if txt.isdigit(): self._go(int(txt))
        else: spawn("getblockchaininfo", lambda c, s: self._go((c or {}).get("blocks")), timeout=8)
    def _go(self, h):
        if h is None: return
        if h == self._analyzing and (self._hdr is None or self._st is None): return   # fetch in flight
        self._analyzing = h; self._last_tip = h; self._hdr = None; self._st = None
        spawn("getblockstats", self._on_stats, params=[h], timeout=25)
        spawn("getblockhash", self._on_hash, params=[h], timeout=10)
    def _on_hash(self, hsh, stale): spawn("getblockheader", self._on_hdr, params=[hsh], timeout=10)
    def _on_hdr(self, hdr, stale): self._hdr = hdr or {}; self._render()
    def _on_stats(self, st, stale): self._st = st or {}; self._render()
    def _render(self):
        if self._hdr is None or self._st is None: return          # render once BOTH measurements landed
        H, S = self._hdr, self._st
        t = H.get("time"); age = human_dt(_now() - t) if t else "—"
        tm = datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC") if t else "—"
        self.head.setText(f"① identity:  #{S.get('height', self._analyzing):,}  ·  {tm}  ·  age {age}\n"
                          f"   {H.get('hash', '—')}")
        d = H.get("difficulty") or 0
        # exact expected work from the compact target — integer math, no float difficulty×2³²
        try:
            hashes = work_from_bits(int(H.get("bits", "0"), 16))
        except (TypeError, ValueError):
            hashes = 0
        self.q["pow"].setText(
            f"difficulty {d:.3e}\n"
            f"exp. hashes {Decimal(hashes):.6e} (exact from bits)\n"
            f"bits {H.get('bits','—')}\n"
            f"nonce {H.get('nonce','—')}\n"
            f"version 0x{H.get('version',0):08x}\n"
            f"merkle {str(H.get('merkleroot',''))[:16]}…\n"
            f"confirmations {H.get('confirmations','—')}")
        w = S.get("total_weight", 0); sw = S.get("swtotal_weight", 0)
        self.q["struct"].setText(
            f"txs {S.get('txs',0):,}\n"
            f"size {S.get('total_size',0)/1000:,.1f} kB\n"
            f"weight {w:,} WU\n"
            f"vsize {w/4:,.0f} vB\n"
            f"segwit share {100*sw/w if w else 0:.1f}%\n"
            f"inputs {S.get('ins',0):,} · outputs {S.get('outs',0):,}\n"
            f"UTXO Δ {S.get('utxo_increase',0):+,}")
        # cryptnomics stay INTEGER SATOSHIS end-to-end; display is exact Decimal at 18 dp
        # (first 8 decimals = satoshi resolution, tail zeros exact) — no float /1e8 anywhere.
        # Fees are MEASURED, not just totalled: a "0.03 ₿TC fees" block is ~3M sat spread
        # over thousands of txs — the per-tx and per-vB lines below say what was really paid.
        sub_s = S.get("subsidy", 0) or 0; fee_s = S.get("totalfee", 0) or 0
        ntx = max(1, S.get("txs", 1) - 1)                 # fee-paying txs (coinbase pays none)
        pctl = S.get("feerate_percentiles") or []
        p50 = pctl[2] if len(pctl) == 5 else None
        share = 100.0 * fee_s / (sub_s + fee_s) if (sub_s + fee_s) else 0.0
        self.q["econ"].setText(
            f"subsidy {btc18(sub_s)} ₿TC\n"
            f"fees {btc18(fee_s)} ₿TC = {fee_s:,} sat\n"
            f"  = {share:.2f}% of the {btc18(sub_s + fee_s)} ₿TC reward\n"
            f"  ÷ {ntx:,} paying txs → avg {S.get('avgfee', 0):,} sat · median {S.get('medianfee', 0):,} sat\n"
            f"feerate avg {S.get('avgfeerate', 0)} sat/vB"
            + (f" · median {p50} sat/vB" if p50 is not None else "") + "\n"
            f"fee span {S.get('minfee', 0):,} – {S.get('maxfee', 0):,} sat\n"
            f"total out {btc18(S.get('total_out', 0))} ₿TC")
        self.fullbar.setValue(min(4_000_000, int(w)))
        pct = S.get("feerate_percentiles") or []
        if len(pct) == 5:
            self.feebar.setText("fee percentiles (sat/vB):  p10 %s · p25 %s · p50 %s · p75 %s · p90 %s" % tuple(pct))


class _DiagRow(QtWidgets.QFrame):
    """One draggable diagnostic card — grip ⠿, muted title, roomy monospace value."""
    def __init__(self, key, value_label):
        super().__init__()
        self._key = key; self._press = None
        self.setObjectName("diagrow")
        self.setStyleSheet("#diagrow{border:1px solid #0e3d57;border-radius:5px;background:#070d14}"
                           "#diagrow:hover{border-color:#2e6a8f}")
        h = QtWidgets.QHBoxLayout(self); h.setContentsMargins(7, 5, 7, 5); h.setSpacing(8)
        grip = QtWidgets.QLabel("⠿"); grip.setStyleSheet("color:#3a4b5c;border:0;font-size:13px")
        grip.setCursor(QtCore.Qt.OpenHandCursor); grip.setToolTip("drag to reorder"); h.addWidget(grip)
        col = QtWidgets.QVBoxLayout(); col.setSpacing(1)
        t = QtWidgets.QLabel(key); t.setStyleSheet("color:#8aa0b4;font-size:10px;border:0")
        col.addWidget(t); col.addWidget(value_label); h.addLayout(col, 1)
    def mousePressEvent(self, ev):
        if ev.button() == QtCore.Qt.LeftButton: self._press = ev.pos()
        super().mousePressEvent(ev)
    def mouseMoveEvent(self, ev):
        if (self._press is not None and
                (ev.pos() - self._press).manhattanLength() > QtWidgets.QApplication.startDragDistance()):
            self._press = None
            mime = QtCore.QMimeData(); mime.setData(OracleDiagList.MIME, self._key.encode())
            drag = QtGui.QDrag(self); drag.setMimeData(mime)
            drag.setPixmap(self.grab()); drag.setHotSpot(ev.pos())
            drag.exec(QtCore.Qt.MoveAction)
        super().mouseMoveEvent(ev)


class OracleDiagList(QtWidgets.QScrollArea):
    """The ₿TC.oracle statistical readout, un-scrunched: one card per diagnostic with real
    breathing room, scrolling vertically when the tab is short, living in a splitter pane
    (user-resizable against the mesh), and drag-and-drop reorderable — the order persists
    (QSettings) so the oracle greets you the way you arranged it."""
    MIME = "application/x-bankon-oracle-diag"
    def __init__(self, keys):
        super().__init__()
        self.setWidgetResizable(True); self.setAcceptDrops(True)
        self.setMinimumWidth(230)
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.setStyleSheet("QScrollArea{border:0}")
        holder = QtWidgets.QWidget(); self.lay = QtWidgets.QVBoxLayout(holder)
        self.lay.setAlignment(QtCore.Qt.AlignTop); self.lay.setSpacing(3)
        self.lay.setContentsMargins(2, 2, 6, 2)
        self.setWidget(holder)
        self.f = {}
        try:
            saved = json.loads(QtCore.QSettings("BANKON", "bankon-qt").value("oracle/diagorder", "[]"))
        except Exception:
            saved = []
        order = [k for k in saved if k in keys] + [k for k in keys if k not in saved]
        for k in order:
            val = QtWidgets.QLabel("…")
            val.setStyleSheet("color:#d6e3ef;font-family:monospace;border:0")
            val.setWordWrap(True); val.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
            self.f[k] = val
            self.lay.addWidget(_DiagRow(k, val))
    def dragEnterEvent(self, ev):
        if ev.mimeData().hasFormat(self.MIME): ev.acceptProposedAction()
    def dragMoveEvent(self, ev):
        if ev.mimeData().hasFormat(self.MIME): ev.acceptProposedAction()
    def dropEvent(self, ev):
        key = bytes(ev.mimeData().data(self.MIME)).decode()
        rows = [self.lay.itemAt(i).widget() for i in range(self.lay.count())]
        moving = next((r for r in rows if r._key == key), None)
        if moving is None: return
        pos = ev.position().toPoint() if hasattr(ev, "position") else ev.pos()
        y = pos.y() + self.verticalScrollBar().value()          # viewport → holder coords
        target = sum(1 for r in rows if r is not moving and r.geometry().center().y() < y)
        self.lay.removeWidget(moving); self.lay.insertWidget(target, moving)
        ev.acceptProposedAction()
        order = [self.lay.itemAt(i).widget()._key for i in range(self.lay.count())]
        QtCore.QSettings("BANKON", "bankon-qt").setValue("oracle/diagorder", json.dumps(order))


class OracleTab(QtWidgets.QWidget):
    """₿TC.oracle — the clock kept on a ₿itcoin block. ₿itcoin-orange framed, with an electric-blue
    mesh graphical area (block-interval sparkline + headline) beside the statistical readout, plus a
    block-measurement history accordion for per-block scientific analysis (getblockstats)."""
    def __init__(self):
        super().__init__(); outer = QtWidgets.QVBoxLayout(self); outer.setContentsMargins(5, 5, 5, 5); outer.setSpacing(5)
        frame = QtWidgets.QFrame(); frame.setObjectName("oracleframe")
        v = QtWidgets.QVBoxLayout(frame); v.setContentsMargins(7, 5, 7, 7); v.setSpacing(5)
        t = QtWidgets.QLabel("₿  ₿TC.oracle — ₿LOCKCLOCK · the clock kept on a ₿itcoin block"); t.setObjectName("oracletitle")
        t.setAlignment(QtCore.Qt.AlignCenter); v.addWidget(t)
        # the brand equation: blockclock reads time FROM blocks; clockblock measures blocks BY time —
        # blocktime is the shared unit, rendered exact to 18 decimals by the precision core
        sub = QtWidgets.QLabel("blockclock: time read from blocks   ·   clockblock: blocks measured by time   ·   "
                               "blocktime exact to 0.000000000000000001")
        sub.setAlignment(QtCore.Qt.AlignCenter)
        sub.setStyleSheet("color:#8aa0b4;font-size:10px;letter-spacing:1px;border:0")
        sub.setToolTip("₿LOCKCLOCK / CLOCK₿LOCK — the oracle's two faces:\n"
                       "• blockclock — the chain as a timepiece: tip date, time since last block, halving epochs\n"
                       "• clockblock — wall-time as the ruler: block intervals, all-time and 2016-block averages\n"
                       "Every blocktime figure is exact Decimal arithmetic at 18 dp — no float drift.")
        v.addWidget(sub)
        # ANTI-CLOCKBLOCK: never trust one clock. Tip height/time are cross-checked across
        # independent sources every refresh — Console cache · debug.log tail · the node
        # queried DIRECTLY (cache bypass) · a second node when one runs (pruned :8342) —
        # and the displayed averages are checked against the local measurement HISTORY (σ).
        self.xcheck = QtWidgets.QLabel("anti-clockblock: probing sources…")
        self.xcheck.setAlignment(QtCore.Qt.AlignCenter)
        self.xcheck.setStyleSheet("color:#5a6b7b;font-family:'DejaVu Sans Mono',monospace;font-size:10px;border:0")
        self.xcheck.setToolTip("Methods against clockblocking (a stale/lying time source steering the oracle):\n"
                               "• multi-SOURCE: Console cache vs debug.log vs direct node RPC (cache bypass)\n"
                               "• multi-NODE: the pruned node (:8342) is queried too when it runs\n"
                               "• HISTORY: current block-time averages sanity-checked against the local\n"
                               "  measurement log (σ of recent intervals) — divergence is flagged, never hidden")
        v.addWidget(self.xcheck)
        self.mesh = MeshPanel()                                            # graphical area
        self.diag = OracleDiagList(["chain height", "tip block date", "time since last block",
            "avg block time — all-time (from genesis)", "avg block time — recent (~2016 blk)",
            "protocol target", "basis used", "recommended poll",
            "avg peer ping", "network ↓ / ↑ rate", "network total ↓ / ↑",
            "genesis", "time since last update"])
        self.f = self.diag.f                                               # statistical area (same fill API)
        mid = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        mid.addWidget(self.mesh); mid.addWidget(self.diag)
        mid.setStretchFactor(0, 3); mid.setStretchFactor(1, 2)
        st = QtCore.QSettings("BANKON", "bankon-qt")
        try: mid.setSizes([int(x) for x in json.loads(st.value("oracle/midsplit", "[600, 420]"))])
        except Exception: mid.setSizes([600, 420])
        mid.splitterMoved.connect(lambda *_: QtCore.QSettings("BANKON", "bankon-qt")
                                  .setValue("oracle/midsplit", json.dumps(mid.sizes())))
        v.addWidget(mid, 1)
        outer.addWidget(frame, 1)
        # QUADRANTS: with the frame's mesh|stats split above, the two panels below complete a
        # 2×2 oracle — Q1 mesh · Q2 statistics · Q3 🔬 block science (current running block,
        # visual workflow) · Q4 measurement history + log. Splitter = user-adjustable quadrant line.
        self.science = BlockSciencePanel()
        qsplit = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        qsplit.addWidget(self.science)
        histcol = QtWidgets.QWidget(); outer_hist = QtWidgets.QVBoxLayout(histcol)
        outer_hist.setContentsMargins(0, 0, 0, 0)
        page = outer                # the tab's real page layout (quadrant frame goes here at the end)
        outer = outer_hist          # re-point: the history/log widgets below land in quadrant Q4
        # ₿lock-measurement history — accordion + a logging-verbosity control for scientific monitoring.
        hrow = QtWidgets.QHBoxLayout()
        hh = QtWidgets.QLabel("📜 ₿lock measurement history — expand a block for scientific analysis")
        hh.setStyleSheet("color:#F7931A;font-weight:700"); hrow.addWidget(hh, 1)
        self.automeasure = QtWidgets.QCheckBox("⚡ auto-measure"); self.automeasure.setChecked(True)
        self.automeasure.setToolTip("Measure every new block as it arrives → live activity stream + JSONL.\n"
                                    "The running log is your visual confirmation the node is connected and receiving blocks.")
        hrow.addWidget(self.automeasure)
        hrow.addWidget(QtWidgets.QLabel("logging"))
        self.verb = QtWidgets.QComboBox(); self.verb.addItems(["Quiet", "Normal", "Verbose", "Scientific"])
        self.verb.setCurrentText("Normal")
        self.verb.setToolTip("Detail level for block monitoring / ₿TC.oracle diagnostics:\n"
                             "Quiet = one-line · Normal = full metric grid · Verbose = + raw getblockstats · "
                             "Scientific = + header + derived measures")
        self.verb.currentTextChanged.connect(self._verb_changed)
        hrow.addWidget(self.verb)
        hgrip = QtWidgets.QWidget(); hgrip.setLayout(hrow); outer.addWidget(hgrip)   # drag grip (hold to dock)
        sc = QtWidgets.QScrollArea(); sc.setWidgetResizable(True); sc.setStyleSheet("border:1px solid #2e4a63;border-radius:6px")
        hold = QtWidgets.QWidget(); self.hist_lay = QtWidgets.QVBoxLayout(hold); self.hist_lay.setAlignment(QtCore.Qt.AlignTop)
        self.hist_lay.setSpacing(2); sc.setWidget(hold); outer.addWidget(sc, 2)
        ml = QtWidgets.QHBoxLayout()
        ml.addWidget(QtWidgets.QLabel("🔬 measurement log")); ml.addStretch()
        ml.addWidget(QtWidgets.QLabel("export"))
        ej = QtWidgets.QPushButton("JSON"); ej.clicked.connect(lambda: self._export("json")); ml.addWidget(ej)
        el = QtWidgets.QPushButton("JSONL"); el.clicked.connect(lambda: self._export("jsonl")); ml.addWidget(el)
        ec = QtWidgets.QPushButton("CSV"); ec.setObjectName("secondary"); ec.clicked.connect(lambda: self._export("csv")); ml.addWidget(ec)
        clr = QtWidgets.QPushButton("clear"); clr.setObjectName("danger"); ml.addWidget(clr)
        mgrip = QtWidgets.QWidget(); mgrip.setLayout(ml); outer.addWidget(mgrip)     # drag grip (hold to dock)
        self.mlog = QtWidgets.QPlainTextEdit(); self.mlog.setReadOnly(True); self.mlog.setMaximumHeight(96)
        self.mlog.setStyleSheet("font-family:monospace;font-size:11px;background:#05080d;color:#c9d4e0"); outer.addWidget(self.mlog)
        clr.clicked.connect(self.mlog.clear)
        qsplit.addWidget(histcol)
        self.qsplit = qsplit
        self._hh = hh; self._hh_base = hh.text()
        if QtCore.QSettings("BANKON", "bankon-qt").value("oracle/quaddock", "right") == "bottom":
            qsplit.setOrientation(QtCore.Qt.Vertical)
        self._apply_quadsizes()
        page.addWidget(qsplit, 2)   # complete the 2×2: Q3 science | Q4 history now under Q1|Q2
        # PRESS-AND-HOLD drag-to-dock: hold either log header, drag, release LOW → the
        # history+measurement-log quadrant docks BELOW block science; release RIGHT → beside it.
        for grip in (hgrip, mgrip):
            grip.setToolTip("press & HOLD, then drag — release LOW to dock the history + measurement log\n"
                            "below the block-science panel, release RIGHT to dock it beside")
            HoldDrag(grip, self, self._dock_quad, self._dock_msg)
        self._hist_heights = set(); self._measurements = []
        self._last_block = None; self._last_prev = None
        self._logdir = Path.home() / "bankon-tools" / "oracle-logs"   # default: auto-persist as JSONL
        try: self._logdir.mkdir(parents=True, exist_ok=True)
        except Exception: pass
        self._auto_jsonl = self._logdir / "measurements.jsonl"; self._primed = False
        self._blk_logtime = 0.0; self._lastupd = 0.0
        self._clk = QtCore.QTimer(self); self._clk.timeout.connect(self._clock); self._clk.start(1000)
        # auto-measure: poll for new blocks every 8s (log-based, runs even when this tab isn't shown)
        self._mtimer = QtCore.QTimer(self); self._mtimer.timeout.connect(self._tick_measure); self._mtimer.start(8000)
    SERIES_N = 90                                                  # consistent block window feeding the graph
    def _apply_quadsizes(self):
        sp = self.qsplit
        if sp.orientation() == QtCore.Qt.Horizontal:
            sp.setSizes([460, 500])
        else:
            tot = max(sp.height(), 400)
            sp.setSizes([int(tot * 0.55), int(tot * 0.45)])
    def _dock_quad(self, zone):
        self.qsplit.setOrientation(QtCore.Qt.Vertical if zone == "bottom" else QtCore.Qt.Horizontal)
        QtCore.QSettings("BANKON", "bankon-qt").setValue("oracle/quaddock", zone)
        self._apply_quadsizes()
    def _dock_msg(self, s):
        self._hh.setText(s)                                        # transient status on the history header
        QtCore.QTimer.singleShot(3500, lambda: self._hh.setText(self._hh_base))
    def _tick_measure(self):
        if self.isVisible() and self.automeasure.isChecked():
            spawn_fn(lambda: fetch_json(f"/api/recentblocks?n={self.SERIES_N}").get("blocks", []), self._fill_blocks)
    def refresh(self):
        spawn_fn(lambda: fetch_json("/api/oracle").get("oracle", {}), self._fill)
        spawn_fn(synctip, self._fill_sync)
        spawn_fn(lambda: fetch_json(f"/api/recentblocks?n={self.SERIES_N}").get("blocks", []), self._fill_blocks)
        spawn_fn(lambda: fetch_json("/api/nethealth"), self._fill_net)
        spawn_fn(self._gather_xcheck, self._fill_xcheck)           # anti-clockblock sweep
        self.science.refresh()                                     # Q3: re-measure the running block
    # ---- anti-clockblock: multi-source · multi-node · history cross-verification ----
    def _gather_xcheck(self):
        """Worker thread: tip height from every independent source that answers."""
        from services.rpc_service import rpc_direct
        src = {}
        try: src["debug.log"] = synctip(timeout=5).get("height")
        except Exception: pass
        try: src["node:8332"] = rpc_direct("getblockcount", timeout=6)      # direct — cache bypass
        except Exception: pass
        try:
            src["console-cache"] = rpc("getblockcount", timeout=6)          # Console-first path
        except Exception: pass
        try:
            src["node:8342·pruned"] = rpc_direct("getblockcount", timeout=4,
                                                 url="http://127.0.0.1:8342",
                                                 cookie=str(Path.home() / ".bitcoin-pruned" / ".cookie"))
        except Exception: pass
        src = {k: v for k, v in src.items() if isinstance(v, int)}
        ivs = [m.get("interval_min") for m in self._measurements[-24:] if m.get("interval_min")]
        return src, ivs
    def _fill_xcheck(self, res):
        src, ivs = res or ({}, [])
        if not src:
            self.xcheck.setText("anti-clockblock: no time source answered (node down?)")
            self.xcheck.setStyleSheet("color:#f85149;font-family:'DejaVu Sans Mono',monospace;font-size:10px;border:0")
            return
        heights = sorted(set(src.values()))
        spread = heights[-1] - heights[0]
        parts = " · ".join(f"{k}={v:,}" for k, v in sorted(src.items()))
        hist = ""
        if len(ivs) >= 3:
            mu = sum(ivs) / len(ivs)
            sd = (sum((x - mu) ** 2 for x in ivs) / len(ivs)) ** 0.5
            hist = f"   history({len(ivs)} blk): μ {mu:.2f} min · σ {sd:.2f}"
        if spread <= 1:
            self.xcheck.setText(f"anti-clockblock ✓ {len(src)} sources agree @ {heights[-1]:,}   [{parts}]{hist}")
            self.xcheck.setStyleSheet("color:#16C784;font-family:'DejaVu Sans Mono',monospace;font-size:10px;border:0")
        else:
            self.xcheck.setText(f"anti-clockblock ⚠ sources DIVERGE by {spread} blocks   [{parts}]{hist}"
                                "   — trusting the highest direct-node reading")
            self.xcheck.setStyleSheet("color:#F7931A;font-weight:700;font-family:'DejaVu Sans Mono',monospace;font-size:10px;border:0")
    def _fill_blocks(self, rb):
        blocks = [b for b in (rb or []) if b.get("time") and b.get("height") is not None]
        srt = sorted(blocks, key=lambda b: b["height"])
        if srt: self.science.maybe_tip(srt[-1]["height"])          # follow-tip → new block = new analysis
        series = []
        for i in range(1, len(srt)):
            dt = (srt[i]["time"] - srt[i - 1]["time"]) / 60.0             # interval, minutes
            if dt >= 0: series.append(min(dt, 120))
        self.mesh.set_series(series)
        # accordion history — add any new blocks (ascending → insert at top so newest is on top)
        tmap = {b["height"]: b["time"] for b in srt}
        newly = []
        for b in srt:
            hgt = b["height"]
            if hgt in self._hist_heights: continue
            self._hist_heights.add(hgt)
            prev = tmap.get(hgt - 1); iv = f"{(b['time']-prev)/60.0:.1f} min" if prev else "—"
            when = datetime.fromtimestamp(b["time"], timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            title = f"#{hgt:,}   ·   {when} UTC   ·   Δ {iv}   ·   {b.get('nTx','?')} txs"
            row = Collapsible(title, on_expand=lambda lay, lbl, H=hgt: self._block_detail(H, lay, lbl))
            self.hist_lay.insertWidget(0, row)
            newly.append(b)
        if not self._primed:                                              # tab just opened: seed the log so it isn't blank
            if newly:
                self.mlog.appendPlainText(f"— ₿TC.oracle log · level: {self.verb.currentText()} · "
                                          f"persisting → {self._auto_jsonl}")
                for b in newly[-6:]: self._log_block(b, tmap.get(b["height"] - 1), seeded=True)
            self._primed = True
        elif self.automeasure.isChecked():                                # live: log each new arrival
            for b in newly: self._log_block(b, tmap.get(b["height"] - 1))
        while self.hist_lay.count() > 80:                                 # cap memory
            w = self.hist_lay.takeAt(self.hist_lay.count() - 1).widget()
            if w: w.setParent(None)
    def _log_block(self, b, prevtime, seeded=False):
        # per-block measurement (log-based — works during the RPC choke). The verbosity control
        # sets how much each line shows: Quiet → id only · Normal → +txs/interval · Verbose →
        # +hash/timestamp · Scientific → +derived rates and (async) getblockstats economics.
        self._last_block, self._last_prev = b, prevtime           # remember for live verbosity changes
        lvl = self.verb.currentText()
        h = b["height"]; iv = ((b["time"] - prevtime) / 60.0) if prevtime else None
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        tag = "seed " if seeded else "NEW block "
        if lvl == "Quiet":
            line = f"[{ts}] ⬢ #{h:,}"
        else:
            line = f"[{ts}] ⬢ {tag}#{h:,}  ·  {b.get('nTx','?')} txs"
            if iv is not None: line += f"  ·  Δ {iv:.1f} min"
        if lvl in ("Verbose", "Scientific"):
            hsh = b.get("hash", "")
            btime = datetime.fromtimestamp(b["time"], timezone.utc).strftime("%H:%M:%S") if b.get("time") else "—"
            line += f"  ·  mined {btime}Z"
            if hsh: line += f"  ·  {hsh[:20]}…"
            if lvl == "Scientific" and iv and b.get("nTx"):
                line += f"  ·  {dec18(b['nTx'], iv * 60)} tx/s"    # derived: exact Decimal, 18 dp
        self.mlog.appendPlainText(line)
        if lvl == "Scientific" and not seeded:                    # pull economics (fees/feerate) async
            spawn_fn(lambda H=h: self._blockstats(H), lambda st, H=h: self._log_sci(H, st))
        rec = {"ts": datetime.now(timezone.utc).isoformat(), "height": h, "time": b.get("time"),
               "nTx": b.get("nTx"), "interval_min": round(iv, 3) if iv is not None else None,
               "hash": b.get("hash"), "source": "seed" if seeded else "auto", "level": lvl}
        self._measurements.append(rec)
        try:
            with open(self._auto_jsonl, "a") as fh: fh.write(json.dumps(rec) + "\n")
        except Exception: pass
    def _log_sci(self, height, st):
        if not isinstance(st, dict): return
        fee = st.get("totalfee"); mn, mx = st.get("minfeerate"), st.get("maxfeerate"); med = st.get("medianfeerate")
        vb = st.get("total_weight"); sz = st.get("total_size")
        parts = [f"    ↳ #{height:,} science:"]
        if fee is not None: parts.append(f"fees {btc18(fee)}₿")   # exact int-sat → 18 dp (was float .4f)
        if med is not None: parts.append(f"feerate {mn}/{med}/{mx} s/vB")
        if sz is not None: parts.append(f"size {sz/1000:.0f} kB")
        if vb is not None: parts.append(f"weight {vb/1000:.0f} kWU")
        self.mlog.appendPlainText("  ".join(parts))
    def _verb_changed(self, lvl):
        # make the control visibly do something immediately: annotate + re-log the latest block at the new level
        self.mlog.appendPlainText(f"— logging level → {lvl}")
        if getattr(self, "_last_block", None):
            self._log_block(self._last_block, self._last_prev, seeded=True)
    @staticmethod
    def _blockstats(h):
        try: return rpc("getblockstats", [h], timeout=15)
        except Exception: return None
    @staticmethod
    def _blockheader(h):
        try: return rpc("getblockheader", [rpc("getblockhash", [h], timeout=10)], timeout=10)
        except Exception: return None
    def _block_detail(self, height, lay, loading_lbl):
        spawn_fn(lambda: self._blockstats(height), lambda s: self._block_detail_done(s, lay, loading_lbl, height))
    def _block_detail_done(self, s, lay, loading_lbl, height):
        loading_lbl.setParent(None)
        if not s:
            e = QtWidgets.QLabel("block stats unavailable (node RPC busy during IBD — expand again when synced)")
            e.setStyleSheet("color:#f85149"); lay.addWidget(e); return
        lvl = self.verb.currentText()
        fp = s.get("feerate_percentiles") or [None] * 5
        medfr = fp[2] if len(fp) > 2 else "—"
        sat = lambda x: f"{x:,} sat" if x is not None else "—"
        # measurement log — always records a concise, timestamped line (logging up to verbose)
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        self.mlog.appendPlainText(
            f"[{ts}] #{height:,}  txs={s.get('txs',0):,}  fee={s.get('totalfee',0):,}sat  "
            f"medFR={medfr}sat/vB  size={s.get('total_size',0):,}B  wt={s.get('total_weight',0):,}WU")
        # structured measurement record (for export) + default auto-persist as JSONL
        rec = {"ts": datetime.now(timezone.utc).isoformat(), "height": height,
               **{k: s.get(k) for k in ('txs', 'ins', 'outs', 'swtxs', 'total_size', 'total_weight',
                  'subsidy', 'totalfee', 'avgfee', 'medianfee', 'minfee', 'maxfee', 'avgfeerate',
                  'minfeerate', 'maxfeerate', 'avgtxsize', 'mediantxsize', 'utxo_increase', 'utxo_size_inc',
                  'time', 'mediantime')}}
        for i, p in enumerate((10, 25, 50, 75, 90)): rec[f"feerate_p{p}"] = fp[i] if i < len(fp) else None
        self._measurements.append(rec)
        try:
            with open(self._auto_jsonl, "a") as fh: fh.write(json.dumps(rec) + "\n")   # default: append JSONL
        except Exception: pass
        if lvl == "Quiet":
            q = QtWidgets.QLabel(f"txs {s.get('txs',0):,} · fees {s.get('totalfee',0):,} sat · "
                                 f"size {s.get('total_size',0):,} B · median feerate {medfr} sat/vB")
            q.setStyleSheet("color:#c9d4e0"); lay.addWidget(q); return
        # Normal+ : the full metric grid
        box, f = cardgrid([
            "txs", "inputs", "outputs", "segwit txs", "total size", "total weight",
            "block subsidy", "total fees", "avg fee", "median fee", "min fee", "max fee",
            "avg feerate", "min feerate", "max feerate", "feerate pct (10/25/50/75/90)",
            "avg tx size", "median tx size", "UTXO Δ", "UTXO size Δ", "block time", "median time"])
        f["txs"].setText(f"{s.get('txs',0):,}"); f["inputs"].setText(f"{s.get('ins',0):,}")
        f["outputs"].setText(f"{s.get('outs',0):,}"); f["segwit txs"].setText(f"{s.get('swtxs',0):,}")
        f["total size"].setText(f"{s.get('total_size',0):,} B"); f["total weight"].setText(f"{s.get('total_weight',0):,} WU")
        f["block subsidy"].setText(sat(s.get("subsidy"))); f["total fees"].setText(sat(s.get("totalfee")))
        f["avg fee"].setText(sat(s.get("avgfee"))); f["median fee"].setText(sat(s.get("medianfee")))
        f["min fee"].setText(sat(s.get("minfee"))); f["max fee"].setText(sat(s.get("maxfee")))
        f["avg feerate"].setText(f"{s.get('avgfeerate','—')} sat/vB"); f["min feerate"].setText(f"{s.get('minfeerate','—')} sat/vB")
        f["max feerate"].setText(f"{s.get('maxfeerate','—')} sat/vB")
        f["feerate pct (10/25/50/75/90)"].setText(" / ".join(str(x) for x in fp))
        f["avg tx size"].setText(f"{s.get('avgtxsize',0):,} B"); f["median tx size"].setText(f"{s.get('mediantxsize',0):,} B")
        f["UTXO Δ"].setText(f"{s.get('utxo_increase','—')}"); f["UTXO size Δ"].setText(f"{s.get('utxo_size_inc','—')} B")
        bt = s.get("time"); mt = s.get("mediantime")
        f["block time"].setText(datetime.fromtimestamp(bt, timezone.utc).strftime("%Y-%m-%d %H:%M:%S") + " UTC" if bt else "—")
        f["median time"].setText(datetime.fromtimestamp(mt, timezone.utc).strftime("%H:%M:%S") if mt else "—")
        lay.addWidget(box)
        # Verbose+ : the raw getblockstats JSON
        if lvl in ("Verbose", "Scientific"):
            import json as _json
            lay.addWidget(QtWidgets.QLabel("raw getblockstats →"))
            pre = QtWidgets.QPlainTextEdit(); pre.setReadOnly(True); pre.setMaximumHeight(200)
            pre.setStyleSheet("font-family:monospace;font-size:11px;background:#05080d"); pre.setPlainText(_json.dumps(s, indent=2))
            lay.addWidget(pre)
        # Scientific : derived measures + the block header
        if lvl == "Scientific":
            txs = s.get("txs", 0) or 1; tw = s.get("total_weight", 0) or 1; sub = s.get("subsidy", 0) or 1
            rew = sub + s.get("totalfee", 0); fee = s.get("totalfee", 0)
            dbox, df = cardgrid(["bytes / tx", "vbytes (weight/4)", "effective sat/vByte",
                                 "fees % of subsidy", "fees % of reward", "inputs / tx", "outputs / tx"])
            # scientific derived measures: exact Decimal division to 18 dp (never float)
            df["bytes / tx"].setText(dec18(s.get("total_size", 0), txs, "B"))
            df["vbytes (weight/4)"].setText(dec18(tw, 4, "vB"))
            df["effective sat/vByte"].setText(dec18(fee * 4, tw))
            df["fees % of subsidy"].setText(pct18(fee, sub))
            df["fees % of reward"].setText(pct18(fee, rew) if rew else "—")
            df["inputs / tx"].setText(dec18(s.get("ins", 0), txs)); df["outputs / tx"].setText(dec18(s.get("outs", 0), txs))
            lay.addWidget(QtWidgets.QLabel("derived measures (scientific) →")); lay.addWidget(dbox)
            hl = QtWidgets.QLabel("fetching block header…"); hl.setStyleSheet("color:#8aa0b4"); lay.addWidget(hl)
            spawn_fn(lambda: self._blockheader(height), lambda hd: self._sci_header(hd, lay, hl))
    def _sci_header(self, hd, lay, hl):
        hl.setParent(None)
        if not hd: lay.addWidget(QtWidgets.QLabel("header unavailable (RPC busy)")); return
        box, f = cardgrid(["version", "bits", "difficulty", "nonce", "merkle root", "chainwork", "header nTx"])
        f["version"].setText(hex(hd.get("version", 0))); f["bits"].setText(str(hd.get("bits", "—")))
        f["difficulty"].setText(f"{hd.get('difficulty',0):.3e}"); f["nonce"].setText(str(hd.get("nonce", "—")))
        f["merkle root"].setText((hd.get("merkleroot", "") or "")[:20] + "…")
        # chainwork = exact cumulative expected hashes (integer), not just a hex string slice
        cw = chainwork_int(hd.get("chainwork", ""))
        f["chainwork"].setText(f"{Decimal(cw):.6e} hashes (exact: …{(hd.get('chainwork','') or '').lstrip('0')[-12:]})"
                               if cw else (hd.get("chainwork", "") or "—").lstrip("0")[:16] + "…")
        f["header nTx"].setText(f"{hd.get('nTx','—')}")
        lay.addWidget(QtWidgets.QLabel("block header (scientific) →")); lay.addWidget(box)
    def _export(self, fmt):
        if not self._measurements:
            self.mlog.appendPlainText("— nothing to export yet (expand a block first)"); return
        default = str(self._logdir / f"bankon-oracle-measurements.{fmt}")
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, f"Export measurements ({fmt.upper()})", default, f"{fmt.upper()} (*.{fmt})")
        if not path: return
        try:
            if fmt == "json":
                with open(path, "w") as fh: json.dump(self._measurements, fh, indent=2)
            elif fmt == "jsonl":
                with open(path, "w") as fh:
                    for r in self._measurements: fh.write(json.dumps(r) + "\n")
            elif fmt == "csv":
                keys = []
                for r in self._measurements:
                    for k in r:
                        if k not in keys: keys.append(k)
                with open(path, "w") as fh:
                    fh.write(",".join(keys) + "\n")
                    for r in self._measurements:
                        fh.write(",".join("" if r.get(k) is None else str(r.get(k)) for k in keys) + "\n")
            self.mlog.appendPlainText(f"— exported {len(self._measurements)} measurements → {path}")
        except Exception as e:
            self.mlog.appendPlainText(f"— export failed: {e}")
    def _fill_sync(self, st):
        if st.get("logTime"): self._blk_logtime = _parse_iso(st["logTime"])
        bd = st.get("blockDate")
        if bd: self.f["tip block date"].setText(bd.replace("T", " ").replace("Z", " UTC"))
        self._clock()
    def _fill(self, o):
        self._lastupd = _now()
        mn = lambda s: dec18(s, 60, "min") if s else "—"   # exact Decimal minutes, 18 dp
        self.f["chain height"].setText(f"{o.get('height', 0):,}")
        a = o.get("avgBlockTimeAllTime")
        self.f["avg block time — all-time (from genesis)"].setText(mn(a))
        self.f["avg block time — recent (~2016 blk)"].setText(mn(o.get("avgBlockTimeWindow")))
        self.f["protocol target"].setText(mn(o.get("targetBlockTime")))
        self.f["basis used"].setText(mn(o.get("basisSeconds")))
        rp = o.get("recommendedPollMs")
        self.f["recommended poll"].setText(f"{rp/1000:.0f}s" if rp else "—")
        g = o.get("genesisTime")
        self.f["genesis"].setText(datetime.fromtimestamp(g, timezone.utc).strftime("%Y-%m-%d") if g else "—")
        # the mesh graphic keeps a compact headline (it's a display, not the measurement);
        # the exact 18-dp figure lives in the cards above
        self.mesh.set_headline(f"{a/60:.3f} min" if a else "—", "average block time · all-time, from genesis")
        self._clock()
    @staticmethod
    def _fmt_bytes(n, per_s=False):
        if n is None: return "—"
        u = "B";
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if n < 1024 or unit == "TB": u = unit; break
            n /= 1024.0
        return f"{n:.1f} {u}{'/s' if per_s else ''}"
    def _fill_net(self, d):
        if not d or not d.get("ok"):
            return
        ap, mp, pk = d.get("avgPingMs"), d.get("minPingMs"), d.get("peers")
        self.f["avg peer ping"].setText(
            f"{ap:.0f} ms   (min {mp:.0f} ms · {pk} peers)" if ap is not None else f"— ({pk or 0} peers)")
        ri, ro = d.get("rateIn"), d.get("rateOut")
        self.f["network ↓ / ↑ rate"].setText(
            f"↓ {self._fmt_bytes(ri, True)}   ·   ↑ {self._fmt_bytes(ro, True)}" if ri is not None else "measuring…")
        self.f["network total ↓ / ↑"].setText(
            f"↓ {self._fmt_bytes(d.get('totalRecv'))}   ·   ↑ {self._fmt_bytes(d.get('totalSent'))}")
    def _clock(self):
        if not self.isVisible(): return
        now = _now()
        if self._blk_logtime: self.f["time since last block"].setText(human_dt(now - self._blk_logtime))
        if self._lastupd: self.f["time since last update"].setText(human_dt(now - self._lastupd) + " ago")


class NetworkTab(QtWidgets.QWidget):
    """Connected peers + a node chooser: add/force/drop peers to grow the connection count."""
    SEEDS = ["seed.bitcoin.sipa.be:8333", "dnsseed.bluematt.me:8333", "seed.bitcoin.sprovoost.nl:8333",
             "dnsseed.emzy.de:8333", "seed.bitcoin.wiz.biz:8333", "seed.bitcoinstats.com:8333"]
    def __init__(self):
        super().__init__(); v = QtWidgets.QVBoxLayout(self)
        row = QtWidgets.QHBoxLayout()
        self.conns = QtWidgets.QLabel("connections: —"); self.conns.setStyleSheet("color:#16C784;font-weight:700")
        row.addWidget(self.conns); row.addStretch()
        row.addWidget(QtWidgets.QLabel("add node"))
        self.chooser = QtWidgets.QComboBox(); self.chooser.setEditable(True); self.chooser.addItems(self.SEEDS)
        self.chooser.setMinimumWidth(230); self.chooser.setToolTip("Pick a seed or type ip:port, then Add")
        row.addWidget(self.chooser)
        a = QtWidgets.QPushButton("＋ Add"); a.clicked.connect(lambda: self._addnode("add")); row.addWidget(a)
        o = QtWidgets.QPushButton("1-try"); o.setToolTip("Try once, don't keep"); o.clicked.connect(lambda: self._addnode("onetry")); row.addWidget(o)
        rm = QtWidgets.QPushButton("✕"); rm.setObjectName("danger"); rm.setToolTip("Remove added node"); rm.clicked.connect(lambda: self._addnode("remove")); row.addWidget(rm)
        v.addLayout(row)
        # Peer-scaling row — power-of-2 targets (1·2·4·8·16·32) + ⚡ enterprise tiers (64·128·256)
        # with auto-grow: continuous increase toward the target by dialing the fastest known peers.
        # HONESTY: outbound-only Core tops out ~10 automatic + 8 addnode slots (≈18); enterprise
        # tiers additionally need inbound reachability (:8333 open) and -maxconnections sized up.
        r2 = QtWidgets.QHBoxLayout()
        r2.addWidget(QtWidgets.QLabel("peer target"))
        self.tgt = Pow2SpinBox(); self.tgt.setRange(1, 1024)
        self.tgt.setValue(int(os.environ.get("BANKON_PEER_TARGET", "12")))
        self.tgt.setToolTip("Desired peer-connection FLOOR. Type ANY number; the ▲/▼ arrows jump in\n"
                            "powers of two (8·16·32·64…). 8 = standard · 16/32 = strong · 64+ = enterprise\n"
                            "(needs inbound :8333 + -maxconnections in bitcoin.conf).")
        self.tgt.valueChanged.connect(lambda _v: self._set_target()); r2.addWidget(self.tgt)
        self.autogrow = QtWidgets.QCheckBox("auto-grow (continuous)")
        self.autogrow.setToolTip("Enterprise mode: every refresh below target dials the fastest known peers\n"
                                 "(Console /api/node/connect-fast) until the target is met — throttled to 1/min")
        r2.addWidget(self.autogrow)
        g = QtWidgets.QPushButton("⚡ Grow now"); g.setToolTip("Dial fast peers toward the target immediately")
        g.clicked.connect(self._grow); r2.addWidget(g)
        self.fastpref = QtWidgets.QCheckBox("⚡ prefer fastest")
        self.fastpref.setToolTip("Continuously bias the peer set toward the fastest block-serving peers:\n"
                                 "promote the fastest, top up from saved fast nodes, and gently drop the\n"
                                 "single slowest non-favourite peer. Background loop; persists. (Console\n"
                                 "/api/node/fastpref) — the on/off switch for fast-peer preference.")
        self.fastpref.toggled.connect(self._toggle_fastpref); r2.addWidget(self.fastpref)
        r2.addStretch(); v.addLayout(r2)
        # ₿ network intelligence strip — self-sourced ₿itcoin facts: OUR addrman census (total nodes
        # this node has actually heard of — bitnodes-style, no third party) + difficulty / hashrate /
        # subsidy / halving countdown derived from getmininginfo.
        self.btcinfo = QtWidgets.QLabel("₿ network:  measuring…")
        self.btcinfo.setStyleSheet("color:#00BFFF;font-family:monospace;font-size:11px")
        self.btcinfo.setToolTip("Self-sourced from OUR node only. 'nodes in our addrman' = every address this node\n"
                                "has heard of (the reachable network is a subset — ~24k nodes as of mid-2026).")
        self.btcinfo.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        v.addWidget(self.btcinfo)
        self._btc = {}; self._census_ts = 0.0; self._lastgrow = 0.0
        self.status = QtWidgets.QLabel("Pick a seed (or type an ip:port) and Add to grow connections.")
        self.status.setStyleSheet("color:#8aa0b4"); v.addWidget(self.status)
        self.local_lbl = QtWidgets.QLabel("our node: —")
        self.local_lbl.setStyleSheet("color:#00BFFF;font-family:monospace;font-weight:600"); v.addWidget(self.local_lbl)
        self.t = QtWidgets.QTableWidget(); self.t.setColumnCount(6)
        self.t.setHorizontalHeaderLabels(["addr", "subver", "dir", "ping(ms)", "↓ KB/s", "height"])
        hh = self.t.horizontalHeader(); hh.setSectionResizeMode(QtWidgets.QHeaderView.Interactive); hh.setStretchLastSection(True)
        hh.setSectionsMovable(True); hh.setMinimumSectionSize(46); self.t.verticalHeader().setVisible(False)
        # Default widths tuned to content (addr widest) — drag any divider left/right to adjust; the
        # widths persist across refreshes (we only auto-fit once, see _fill).
        for col, w in enumerate((210, 150, 46, 74, 74, 90)): self.t.setColumnWidth(col, w)
        self._sized = False
        self.t.verticalHeader().setDefaultSectionSize(28); self.t.setShowGrid(False); self.t.setAlternatingRowColors(True)
        self.t.setSortingEnabled(True); self.t.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.t.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)              # right-click → Promote / ₿oot
        self.t.customContextMenuRequested.connect(self._peer_menu)
        self.t.setToolTip("Right-click a peer for actions: ★ Promote (favourite) · ⏏ ₿oot (disconnect)")
        v.addWidget(self.t, 1)
        # Connection ACTIVITY — log-based (works even when getpeerinfo is RPC-choked): attempts,
        # successes, failures, drops, inbound, + the node's local network addresses.
        self.actsum = QtWidgets.QLabel("connection activity — log-based (works during RPC choke)")
        self.actsum.setStyleSheet("color:#8aa0b4;font-weight:600"); v.addWidget(self.actsum)
        self.act = QtWidgets.QTableWidget(); self.act.setColumnCount(4)
        self.act.setHorizontalHeaderLabels(["time", "event", "addr / peer", "detail"])
        ah = self.act.horizontalHeader(); ah.setStretchLastSection(True); ah.setMinimumSectionSize(60)
        self.act.verticalHeader().setVisible(False); self.act.verticalHeader().setDefaultSectionSize(22)
        self.act.setShowGrid(False); self.act.setAlternatingRowColors(True)
        self.act.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers); v.addWidget(self.act, 1)
    def refresh(self):
        self._have_ni_conns = False        # getnetworkinfo owns the count each cycle; peerinfo is fallback
        if not getattr(self, "_fastpref_synced", False):   # reflect persisted fast-preference toggle once
            self._fastpref_synced = True; self._sync_fastpref()
        spawn("getpeerinfo", self._fill, timeout=10)
        spawn("getnetworkinfo", self._setni, timeout=8)                       # our local node address + conn count
        spawn_fn(lambda: fetch_json("/api/netactivity?n=50"), self._setact)   # log-based connection activity
        spawn("getmininginfo", self._btcstats, timeout=10)                    # difficulty/hashrate/halving strip
        if time.time() - self._census_ts > 180:    # addrman census is heavy (all addresses) — 3-min cadence
            self._census_ts = time.time()
            spawn_fn(self._census, self._census_done)
    # ---- ₿ network strip: addrman census + chain economics (all self-sourced) ----
    @staticmethod
    def _census():
        addrs = rpc("getnodeaddresses", [0], timeout=30) or []    # 0 = ALL addresses our node knows
        tally = {}
        for a in addrs:
            net = a.get("network", "?"); tally[net] = tally.get(net, 0) + 1
        return {"total": len(addrs), "tally": tally}
    def _census_done(self, d):
        self._btc.update(d or {}); self._render_btc()
    def _btcstats(self, m, stale):
        m = m or {}
        self._btc.update(difficulty=m.get("difficulty"), hashps=m.get("networkhashps"), blocks=m.get("blocks"))
        self._render_btc()
    def _render_btc(self):
        b = self._btc; parts = []
        if b.get("total"):
            det = " · ".join(f"{v:,} {k}" for k, v in sorted(b["tally"].items(), key=lambda kv: -kv[1]))
            parts.append(f"{b['total']:,} nodes in our addrman ({det})")
        if b.get("difficulty"):
            era = (b.get("blocks") or 0) // 210000
            left = (era + 1) * 210000 - (b.get("blocks") or 0)
            parts += [f"difficulty {b['difficulty']/1e12:.1f}T", f"~{(b.get('hashps') or 0)/1e18:.0f} EH/s",
                      f"subsidy {50/2**era:g} ₿TC",
                      f"halving in {left:,} blk (~{left*10/60/24/365.25:.1f} yr)"]
        if parts: self.btcinfo.setText("₿ network:  " + "   ·   ".join(parts))
    # ---- peer-target tiers + growth ----
    def _set_target(self):
        n = int(self.tgt.value())
        os.environ["BANKON_PEER_TARGET"] = str(n)                 # _render_conns reads this floor
        hint = "" if n <= 18 else f"   ⚡ enterprise: needs inbound :8333 reachable + -maxconnections ≥ {n+10}"
        self.status.setText(f"peer target set to {n}{hint}")
        self.refresh()
    def _grow(self):
        tgt = int(self.tgt.value()); n = len(getattr(self, "_peers", None) or [])
        want = max(4, min(48, tgt - n if tgt > n else 8))         # console clamps to 48/dispatch
        self._lastgrow = time.time()
        self.status.setText(f"⚡ dialing {want} fast peers toward target {tgt}…")
        spawn_fn(lambda: post_json("/api/node/connect-fast", {"count": want}, timeout=15),
                 lambda d: self.status.setText("⚡ " + str((d or {}).get("note") or (d or {}).get("error") or "dispatched")),
                 lambda e: self.status.setText(f"⚡ grow failed: {e}"))
    def _sync_fastpref(self):
        # reflect the server's persisted fast-preference state in the checkbox (without re-triggering)
        def apply(d):
            if not d or not d.get("ok"):
                return
            self.fastpref.blockSignals(True); self.fastpref.setChecked(bool(d.get("on"))); self.fastpref.blockSignals(False)
        spawn_fn(lambda: fetch_json("/api/node/fastpref"), apply)
    def _toggle_fastpref(self, on):
        self.status.setText("⚡ fast-peer preference " + ("ON — biasing toward the fastest peers" if on else "off"))
        spawn_fn(lambda: post_json("/api/node/fastpref", {"on": bool(on)}, timeout=10),
                 lambda d: None, lambda e: self.status.setText(f"⚡ fastpref failed: {e}"))
    def _render_conns(self, n, out, inb, stale):
        """Single place that renders the 'connections: N (out · in)' headline, so every source agrees."""
        tgt = int(os.environ.get("BANKON_PEER_TARGET", "12"))
        # The target is a FLOOR (minimum desired for healthy sync), not a cap — exceeding it is good.
        meet = "✓ above target" if n >= tgt else "building toward target…"
        self.conns.setText(f"connections: {n} ({out} out · {inb} in)   ·   target ≥ {tgt}  {meet}" + ("   (cached)" if stale else ""))
        self.conns.setStyleSheet("color:%s;font-weight:700" % ("#16C784" if n >= tgt else "#F7931A" if n >= 3 else "#f85149"))
    def _setni(self, ni, stale):
        ni = ni or {}
        la = ni.get("localaddresses") or []
        sub = (ni.get("subversion", "") or "").strip("/")
        if la:
            addrs = " · ".join(f"{a.get('address')}:{a.get('port')}" for a in la[:3])
            self.local_lbl.setText(f"our node:  {addrs}   ·   {sub}   ·   protocol {ni.get('protocolversion','?')}")
        else:
            self.local_lbl.setText(f"our node:  (no public address advertised — likely behind NAT)   ·   {sub or '—'}")
        # AUTHORITATIVE connection count — the SAME getnetworkinfo.connections the Overview tab reads
        # (bankon_qt.py:143), so the two tabs can never disagree (the old 18-vs-37 split came from this
        # tab counting len(getpeerinfo) off a differently-timed cache). getnetworkinfo is the cheap,
        # reliably-warmed RPC, so this stays live even while getpeerinfo is choked during IBD.
        c = ni.get("connections")
        if c is not None:
            self._have_ni_conns = True
            out = ni.get("connections_out")
            inb = ni.get("connections_in")
            if out is None: out = (c - inb) if inb is not None else c
            if inb is None: inb = c - out
            self._render_conns(c, out, inb, stale)
    def _setact(self, d):
        d = d or {}; ev = d.get("events", []); ty = d.get("tally", {}); local = d.get("local", [])
        live = d.get("livePeers")
        s = ((f"● {live} live now · " if live is not None else "") +
             f"log window (events) — ✓ {ty.get('connected',0)} connects · ✗ {ty.get('failed',0)} failed · "
             f"⟲ {ty.get('disconnect',0)} dropped · ⇣ {ty.get('inbound',0)} inbound")
        if local: s += "   ·   local: " + ", ".join(local[:3])
        self.actsum.setText(s)
        col = {"connected": "#16C784", "failed": "#f85149", "disconnect": "#F7931A", "inbound": "#00BFFF"}
        self.act.setRowCount(len(ev))
        for r, e in enumerate(reversed(ev)):                                   # newest first
            ap = e.get("addr") or (("peer=" + e["peer"]) if e.get("peer") else "—")
            cells = [(e.get("time", "") or "")[-8:], e.get("kind", ""), ap, (e.get("text", "") or "").strip()[:80]]
            for c, val in enumerate(cells):
                it = QtWidgets.QTableWidgetItem(str(val))
                if c == 1: it.setForeground(QtGui.QColor(col.get(e.get("kind"), "#c9d4e0")))
                self.act.setItem(r, c, it)
        self.act.resizeColumnsToContents()
    def _fill(self, peers, stale):
        peers = peers or []
        self._peers = peers                          # kept for the right-click context menu (full dicts)
        # auto-grow: continuous increase toward the target — dial fast peers whenever we're under,
        # throttled to one dispatch per minute so a slow-to-fill target can't spam addnode.
        tgt = int(os.environ.get("BANKON_PEER_TARGET", "12"))
        if self.autogrow.isChecked() and peers and len(peers) < tgt and time.time() - self._lastgrow > 60:
            self._grow()
        # The count headline is owned by _setni (getnetworkinfo) so it matches the Overview tab exactly.
        # Only fall back to the peer-list length if getnetworkinfo hasn't delivered a count this cycle.
        if not getattr(self, "_have_ni_conns", False):
            # Honesty: an empty + stale result means the node's RPC is busy (IBD/breaker open) and we
            # couldn't read peers — NOT that there are zero. Don't show a misleading "0".
            if stale and not peers:
                self.conns.setText("connections: — (node RPC busy — can't read peers during heavy IBD)")
                self.conns.setStyleSheet("color:#F7931A;font-weight:700")
            else:
                n = len(peers); inb = sum(1 for p in peers if p.get("inbound"))
                self._render_conns(n, n - inb, inb, stale)
        rows = peer_rows(peers)
        self.t.setSortingEnabled(False); self.t.setRowCount(len(rows))
        for r, (rw, p) in enumerate(zip(rows, peers)):
            for c, val in enumerate(rw):
                it = QtWidgets.QTableWidgetItem(str(val))
                it.setData(QtCore.Qt.UserRole, p.get("addr"))            # row → addr (survives sorting)
                if p.get("addnode"): it.setForeground(QtGui.QColor("#FFD37A"))   # ★ favourite rows gold
                self.t.setItem(r, c, it)
        # Auto-fit to content ONCE; afterwards the user's drag-resized widths persist across refreshes.
        if not self._sized and rows:
            self.t.resizeColumnsToContents(); self._sized = True
        self.t.setSortingEnabled(True)
    def _peer_menu(self, pos):
        it = self.t.itemAt(pos)
        if not it: return
        addr = it.data(QtCore.Qt.UserRole) or self.t.item(it.row(), 0).text()
        p = next((x for x in getattr(self, "_peers", []) if x.get("addr") == addr), {})
        m = QtWidgets.QMenu(self)
        if p.get("addnode"):
            m.addAction("★ Un-promote (drop favourite)").triggered.connect(lambda: self._do_peer("unpromote", addr, p))
        else:
            m.addAction("★ Promote (favourite + keep connected)").triggered.connect(lambda: self._do_peer("promote", addr, p))
        m.addAction("⏏ ₿oot (disconnect now)").triggered.connect(lambda: self._do_peer("boot", addr, p))
        m.addSeparator()
        m.addAction("⧉ Copy address").triggered.connect(lambda: QtWidgets.QApplication.clipboard().setText(addr))
        m.exec(self.t.viewport().mapToGlobal(pos))
    def _do_peer(self, kind, addr, p):
        self.status.setText(f"{kind} {addr} …")
        peer_action(kind, addr, on_done=lambda d: self._added(d), subver=p.get("subver", ""))
    def _addnode(self, command):
        addr = self.chooser.currentText().strip()
        if not addr: return
        self.status.setText(f"{command} {addr} …")
        spawn_fn(lambda: post_json("/api/node/addnode", {"addr": addr, "command": command}), self._added)
    def _added(self, d):
        if d and d.get("ok"):
            self.status.setText(f"✓ {d.get('command')} {d.get('addr')} — peers will update shortly"); self.refresh()
        else:
            self.status.setText(f"✗ {(d or {}).get('error', 'failed')}")


class ControlTab(QtWidgets.QWidget):
    """Localhost / local-machine CLIENT CONTROL CENTER — the client is the admin of the client,
    via the modular toolkit (Console endpoints, direct bitcoin-cli fallback). Three panels:
      🌡 Thermal & host   — live temp / CPU / mem / disk with the SAME severity calibration as the
                            toolbar chip, plus the thermal-protection threshold (two-way synced).
      🔌 Localhost checks — raw socket probes of every ₿ANKON port from 127.0.0.1's OWN perspective.
                            Deliberately not routed through the Console: a diagnostics panel must
                            not depend on one of the things it diagnoses.
      ⚙ Admin            — node ▶/■ + the AIRGAP switch (setnetworkactive) so the WaaS can generate
                            wallet keys with the machine's ₿itcoin network dark, then re-enable.
    """
    # Every localhost service in the toolkit: (name, port). Console/WaaS ports come from their URLs
    # so env overrides (BANKON_CONSOLE_URL / BANKON_WAAS_URL) stay honoured.
    @staticmethod
    def _port_of(url, default):
        try: return int(url.rsplit(":", 1)[1].split("/")[0])
        except Exception: return default
    def __init__(self):
        super().__init__(); v = QtWidgets.QVBoxLayout(self)
        self.SERVICES = [
            ("₿itcoin Core RPC (full)",  8332),
            ("₿itcoin P2P",              8333),
            ("Pruned node RPC",          8342),
            ("₿ANKON Console",           self._port_of(CONSOLE_URL, 8090)),
            ("₿ANKON WaaS",              self._port_of(WAAS_URL, 8088)),
            ("ZMQ blocks",               28332),
            ("ZMQ rawtx",                28333),
            ("ZMQ sequence",             28335),
        ]
        head = QtWidgets.QLabel("<b>🖥 Local Control Center</b> — this machine administers itself: "
                                "thermal · host · localhost service diagnostics · airgap")
        head.setStyleSheet("color:#00BFFF"); v.addWidget(head)

        row = QtWidgets.QHBoxLayout()
        # -- Thermal & host card (fed by Main's 5s /api/system poll — no second poller) --
        left = QtWidgets.QVBoxLayout()
        left.addWidget(QtWidgets.QLabel("<b>🌡 Thermal & host</b>"))
        box, self.f = cardgrid(["temperature", "sensor", "protection", "CPU", "load", "memory", "datadir disk"])
        left.addWidget(box)
        tr = QtWidgets.QHBoxLayout(); tr.addWidget(QtWidgets.QLabel("auto-pause pruned node at"))
        self.pausetemp = QtWidgets.QSpinBox(); self.pausetemp.setRange(80, 110); self.pausetemp.setValue(99); self.pausetemp.setSuffix("°C")
        self.pausetemp.setToolTip("Thermal protection threshold — two-way synced with the toolbar spinner")
        tr.addWidget(self.pausetemp); tr.addStretch(); left.addLayout(tr)
        left.addStretch(); row.addLayout(left, 1)

        # -- Localhost service probes --
        right = QtWidgets.QVBoxLayout()
        right.addWidget(QtWidgets.QLabel("<b>🔌 Localhost services</b> — socket probes from 127.0.0.1"))
        # ₿ANKON table formula: ODD column counts (1·3·5·7·9·11·13) — port folds into the service
        # name so this reads as 3 columns. Rationale in docs/design.md → 'The odd-column formula'.
        self.t = QtWidgets.QTableWidget(); self.t.setColumnCount(3)
        self.t.setHorizontalHeaderLabels(["service", "state", "latency"])
        hh = self.t.horizontalHeader(); hh.setStretchLastSection(True); hh.setMinimumSectionSize(56)
        self.t.verticalHeader().setVisible(False); self.t.verticalHeader().setDefaultSectionSize(24)
        self.t.setShowGrid(False); self.t.setAlternatingRowColors(True)
        self.t.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.t.setRowCount(len(self.SERVICES))
        right.addWidget(self.t, 1); row.addLayout(right, 2)
        v.addLayout(row, 1)

        # -- Admin: node control + AIRGAP switch --
        v.addWidget(QtWidgets.QLabel("<b>⚙ Admin</b> — node control · airgap for WaaS wallet generation"))
        ar = QtWidgets.QHBoxLayout()
        self.start_btn = QtWidgets.QPushButton("▶ Start node")
        self.start_btn.clicked.connect(self._start); ar.addWidget(self.start_btn)
        sp = QtWidgets.QPushButton("■ Stop node"); sp.setObjectName("danger"); sp.clicked.connect(self._stop); ar.addWidget(sp)
        ar.addSpacing(24)
        self.airgap = QtWidgets.QPushButton("…"); self.airgap.setEnabled(False)   # armed once state is known
        self.airgap.setToolTip("setnetworkactive — take the ₿itcoin network dark, generate wallet keys in the "
                               "WaaS with zero P2P traffic, then switch back ON")
        self.airgap.clicked.connect(self._toggle_net); ar.addWidget(self.airgap)
        self.waas_btn = QtWidgets.QPushButton("Open WaaS")
        self.waas_btn.clicked.connect(lambda: webbrowser.open(WAAS_URL)); ar.addWidget(self.waas_btn)
        ar.addStretch(); v.addLayout(ar)
        # -- pruned node controls: the lean WaaS backend (bankon-nodes.sh · RPC :8342) --
        pr = QtWidgets.QHBoxLayout()
        pr.addWidget(QtWidgets.QLabel("pruned WaaS node:"))
        self.pstart_btn = QtWidgets.QPushButton("▶ Start pruned")
        self.pstart_btn.setToolTip("Start the lean pruned node (bankon-nodes.sh start pruned — RPC :8342, "
                                   "prune=2048, plays nice with the archival node)")
        self.pstart_btn.clicked.connect(lambda: self._pruned("start")); pr.addWidget(self.pstart_btn)
        pstop = QtWidgets.QPushButton("■ Stop pruned"); pstop.setObjectName("danger")
        pstop.setToolTip("Stop the pruned node — the full archival node keeps running")
        pstop.clicked.connect(lambda: self._pruned("stop")); pr.addWidget(pstop)
        pr.addStretch(); v.addLayout(pr)
        # -- 🐧 host OS — shown ABOVE the tools so the platform is never a mystery --
        _pretty, _fam, _pkg = os_release()
        self._pkg_cmd = _pkg
        osl = QtWidgets.QLabel(
            f"🐧 OS: {_pretty} — " + ("Debian family ✓ (apt) — ₿ANKON ₿TC's first-class host" if _fam == "debian"
            else f"{_fam} family — supported, installs via {' '.join(_pkg)}" if _pkg
            else "unrecognized — install packages manually"))
        osl.setStyleSheet("color:%s;font-weight:600" % ("#16C784" if _fam == "debian"
                                                        else "#F7931A" if _pkg else "#f85149"))
        osl.setToolTip("/etc/os-release · ₿ANKON ₿TC is developed on Debian variants; other Linux "
                       "families are recognized and one-click installs adapt to their package manager")
        v.addWidget(osl)
        # -- External tools: EtherApe live wire visualizer (also surfaced in 🧊 ICE) --
        v.addWidget(QtWidgets.QLabel("<b>🔧 External tools</b> — forensic wire visualizer"))
        xr = QtWidgets.QHBoxLayout()
        _ok, _txt = etherape_status()
        self.eth_status = QtWidgets.QLabel(_txt)
        self.eth_status.setStyleSheet("color:%s" % ("#16C784" if _ok else "#8aa0b4"))
        self.eth_status.setToolTip("EtherApe — radial live-traffic visualizer (GTK/libpcap), the display reference "
                                   "₿ANKON's Net Map borrows from: node size ∝ traffic, protocol colors.\n"
                                   "docs/reference/etherape.md")
        xr.addWidget(self.eth_status, 1)
        self.eth_install = QtWidgets.QPushButton("⬇ Install EtherApe")
        self.eth_install.setToolTip("ONE CLICK: pkexec %s etherape — authorize in the prompt, the button "
                                    "flips to launch when done" % (" ".join(_pkg) if _pkg else "<pkg-mgr>"))
        self.eth_install.setVisible(not _ok); self.eth_install.setEnabled(_pkg is not None)
        self.eth_install.clicked.connect(self._install_etherape); xr.addWidget(self.eth_install)
        self.eth_launch = QtWidgets.QPushButton("🕸 EtherApe (port 8333)")
        self.eth_launch.setObjectName("secondary"); self.eth_launch.setEnabled(_ok)
        self.eth_launch.setToolTip("pkexec etherape -f 'port 8333' — live pcap of this node's ₿itcoin P2P traffic")
        self.eth_launch.clicked.connect(lambda: etherape_launch(self.eth_status.setText)); xr.addWidget(self.eth_launch)
        v.addLayout(xr)
        self.status = QtWidgets.QLabel("network state: checking…"); self.status.setStyleSheet("color:#8aa0b4")
        v.addWidget(self.status)
        self._netactive = None
        # visible-only re-probe cadence (10s) — same pattern as OverviewTab's sync ticker
        self._probet = QtCore.QTimer(self); self._probet.timeout.connect(self._tick); self._probet.start(10000)
    def _tick(self):
        if self.isVisible(): self._probe()
    def refresh(self):
        self._probe()
        spawn("getnetworkinfo", self._net_state, timeout=8)
        spawn_fn(lambda: fetch_json("/api/filesystem"), self._fs)
    # ---- localhost probes (worker thread; UI only touched via the queued signal) ----
    def _probe(self):
        services = list(self.SERVICES)
        def work():
            out = []
            for name, port in services:
                s = socket.socket(); s.settimeout(1.5); t0 = time.perf_counter()
                try: up = s.connect_ex(("127.0.0.1", port)) == 0
                finally: s.close()
                out.append((name, port, up, (time.perf_counter() - t0) * 1000))
            return out
        spawn_fn(work, self._probed)
    def _probed(self, rows):
        for r, (name, port, up, ms) in enumerate(rows or []):
            cells = [f"{name}  :{port}", "● UP" if up else "○ DOWN", f"{ms:.1f} ms" if up else "—"]
            for c, val in enumerate(cells):
                it = QtWidgets.QTableWidgetItem(val)
                if c == 1: it.setForeground(QtGui.QColor("#16C784" if up else "#f85149"))
                self.t.setItem(r, c, it)
        self.t.resizeColumnsToContents()
        up_by_port = {port: up for (name, port, up, ms) in rows or []}
        self._style_node_btn(up_by_port.get(8332))
        self._style_pruned_btn(up_by_port.get(8342))
        self._style_waas_btn(up_by_port.get(self._port_of(WAAS_URL, 8088)))
    def _style_node_btn(self, up):
        # launcher idiom: Start LOOKS PRESSED (sunken green) while the node it starts is running
        if up is None: return
        if up:
            self.start_btn.setText("● Node running")
            self.start_btn.setToolTip("₿itcoin Core RPC :8332 is up — Start is held pressed while it runs")
            self.start_btn.setStyleSheet(
                "QPushButton{background:#0a4a24;color:#bfe8cd;border:2px solid #06371b;"
                "border-radius:8px;padding:4px 12px;font-weight:800;}"
                "QPushButton:hover{background:#0a4a24;}")
        else:
            self.start_btn.setText("▶ Start node")
            self.start_btn.setToolTip("Start bitcoind")
            self.start_btn.setStyleSheet("")
    def _style_pruned_btn(self, up):
        if up is None: return
        if up:
            self.pstart_btn.setText("● Pruned running")
            self.pstart_btn.setToolTip("Pruned node RPC :8342 is up — held pressed while it runs")
            self.pstart_btn.setStyleSheet(
                "QPushButton{background:#0a4a24;color:#bfe8cd;border:2px solid #06371b;"
                "border-radius:8px;padding:4px 12px;font-weight:800;}"
                "QPushButton:hover{background:#0a4a24;}")
        else:
            self.pstart_btn.setText("▶ Start pruned")
            self.pstart_btn.setStyleSheet("")
    NODES_SH = os.path.expanduser("~/bankon-tools/bankon-nodes.sh")
    def _pruned(self, action):
        self.status.setText(f"pruned node: {action}…")
        def work():
            r = subprocess.run(["bash", self.NODES_SH, action, "pruned"],
                               capture_output=True, text=True, timeout=90)
            return ((r.stdout or "") + (r.stderr or "")).strip()[-160:] or f"{action} dispatched"
        spawn_fn(work, lambda s: (self.status.setText(f"pruned node: {s}"), self._probe()),
                 lambda e: self.status.setText(f"pruned {action} failed: {e}"))
    def _install_etherape(self):
        if not self._pkg_cmd:
            self.eth_status.setText("no known package manager — install etherape manually"); return
        self.eth_status.setText("⬇ installing etherape — authorize in the pkexec prompt…")
        self.eth_install.setEnabled(False)
        def work():
            r = subprocess.run(["pkexec"] + self._pkg_cmd + ["etherape"],
                               capture_output=True, text=True, timeout=600)
            return r.returncode
        def done(rc):
            ok, txt = etherape_status()
            self.eth_status.setText(txt if ok else f"install did not complete (rc {rc}) — {txt}")
            self.eth_status.setStyleSheet("color:%s" % ("#16C784" if ok else "#f85149"))
            self.eth_launch.setEnabled(ok)
            self.eth_install.setVisible(not ok); self.eth_install.setEnabled(not ok)
        spawn_fn(work, done, lambda e: (self.eth_status.setText(f"install failed: {e}"),
                                        self.eth_install.setEnabled(True)))
    def _style_waas_btn(self, up):
        # Open WaaS highlights CANDLE GREEN while the WaaS is live at its port
        if up is None: return
        if up:
            self.waas_btn.setStyleSheet(
                "QPushButton{background:#16C784;color:#04220f;border:1px solid #0b5d34;"
                "border-radius:8px;padding:4px 12px;font-weight:800;}"
                "QPushButton:hover{background:#27d96b;}")
            self.waas_btn.setToolTip(f"₿ANKON WaaS is LIVE — {WAAS_URL}")
        else:
            self.waas_btn.setStyleSheet("")
            self.waas_btn.setToolTip(WAAS_URL + "   (WaaS not running — ~/bankon-tools/bankon up)")
    # ---- thermal & host card (called by Main._sys so there's exactly one /api/system poller) ----
    def update_sys(self, d, paused):
        t = d.get("tempC")
        col = "#16C784"; sev = ""
        if t is not None:
            if t >= 99:   col, sev = "#ff2b2b", "  ⚠ DANGEROUS"
            elif t >= 96: col, sev = "#FF5E3A", "  concern"
            elif t >= 85: col, sev = "#F7931A", "  HOT"
            self.f["temperature"].setText(f"{t}°C{sev}")
            self.f["temperature"].setStyleSheet(f"color:{col};font-weight:700")
        self.f["sensor"].setText(d.get("tempLabel") or "—")
        self.f["protection"].setText(("⏸ PAUSED — cooling (re-arms 3°C below threshold)" if paused
                                      else f"✓ armed @ {self.pausetemp.value()}°C"))
        self.f["protection"].setStyleSheet("color:%s;font-weight:700" % ("#F7931A" if paused else "#16C784"))
        if d.get("cpuPct") is not None: self.f["CPU"].setText(f"{d['cpuPct']}% of {d.get('cores','?')} cores")
        if d.get("load1")  is not None: self.f["load"].setText(str(d["load1"]))
        if d.get("memUsedPct") is not None: self.f["memory"].setText(f"{d['memUsedPct']}% of {d.get('memTotalGB','?')} GB")
    def _fs(self, d):
        df = (d or {}).get("df") or {}
        if df:
            used = df.get("used", 0) / 1073741824; size = df.get("size", 0) / 1073741824
            rw, rwcol = disk_runway(((d or {}).get("components") or {}).get("total"), df.get("avail"))
            self.f["datadir disk"].setText(
                f"{used:,.0f} / {size:,.0f} GB ({df.get('pcent','?')}) on {df.get('source','?')}"
                + (f"   ·   <span style='color:{rwcol};font-weight:700'>{rw}</span>" if rw else ""))
    # ---- admin: node ▶/■ (Console first, direct fallback — client stays admin if Console is down) ----
    def _start(self):
        def work():
            try: return post_json("/api/node/start", {}, timeout=8)
            except Exception:
                subprocess.Popen([str(Path(BTC_BIN)/"bitcoind"), f"-datadir={DATADIR}", "-daemon"],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
                return {"ok": True, "note": "launched directly (Console down)"}
        spawn_fn(work, lambda d: self.status.setText((d or {}).get("note") or (d or {}).get("error") or "starting…"))
    def _stop(self):
        if QtWidgets.QMessageBox.question(self, "Stop", "Stop ₿itcoin Core?") != QtWidgets.QMessageBox.Yes: return
        def work():
            try: return post_json("/api/node/stop", {}, timeout=15)
            except Exception:
                r = subprocess.run([str(Path(BTC_BIN)/"bitcoin-cli"), f"-datadir={DATADIR}", "stop"],
                                   capture_output=True, text=True, timeout=15)
                return {"ok": r.returncode == 0, "message": r.stdout.strip() or r.stderr.strip()}
        spawn_fn(work, lambda d: self.status.setText((d or {}).get("message") or "stopping…"))
    # ---- AIRGAP switch ----
    def _net_state(self, ni, stale):
        self._netactive = bool((ni or {}).get("networkactive", True))
        self._render_net(stale)
    def _render_net(self, stale=False):
        on = self._netactive
        self.airgap.setEnabled(True)
        if on:
            self.airgap.setText("🔒 Go AIRGAP (network OFF)"); self.airgap.setObjectName("danger")
            self.status.setText("network state: 🌐 ACTIVE — P2P live" + ("   (cached)" if stale else ""))
            self.status.setStyleSheet("color:#16C784;font-weight:600")
        else:
            self.airgap.setText("🔓 Re-enable network (go LIVE)"); self.airgap.setObjectName("")
            self.status.setText("network state: 🔒 AIRGAPPED — ₿itcoin P2P dark; safe to generate wallet keys in the WaaS")
            self.status.setStyleSheet("color:#F7931A;font-weight:700")
        self.airgap.style().unpolish(self.airgap); self.airgap.style().polish(self.airgap)   # re-apply QSS after objectName change
    def _toggle_net(self):
        want = not self._netactive
        if not want:      # going dark is a state change worth confirming, like Stop
            if QtWidgets.QMessageBox.question(
                self, "Airgap", "Take the ₿itcoin network DARK (setnetworkactive false)?\n"
                "All P2P connections drop until you re-enable.") != QtWidgets.QMessageBox.Yes:
                return
        self.airgap.setEnabled(False); self.status.setText("switching network state…")
        def work():
            try:
                d = post_json("/api/node/netactive", {"on": want}, timeout=10)
                if not (d or {}).get("ok"): raise RuntimeError((d or {}).get("error", "console refused"))
                return d
            except Exception:
                # Console down/refusing → direct bitcoin-cli with the node cookie. Fixed argv, no shell.
                r = subprocess.run([str(Path(BTC_BIN)/"bitcoin-cli"), f"-datadir={DATADIR}",
                                    "setnetworkactive", "true" if want else "false"],
                                   capture_output=True, text=True, timeout=10)
                if r.returncode != 0: raise RuntimeError(r.stderr.strip() or "setnetworkactive failed")
                return {"ok": True, "networkactive": want}
        def done(d):
            self._netactive = bool((d or {}).get("networkactive", want)); self._render_net()
        def fail(e):
            self.airgap.setEnabled(True); self.status.setText(f"✗ {e}"); self.status.setStyleSheet("color:#f85149")
        spawn_fn(work, done, fail)


class NetLogTab(QtWidgets.QWidget):
    """📡 Network Activity Log — every connect / inbound / disconnect / fail event
    from the ₿ANKON ₿TC WaaS node (via /api/netactivity), parsed from debug.log so it
    works even during the IBD RPC choke. Rich detail per event: peer id, direction/role
    (outbound-full-relay / block-relay-only / manual / inbound / feeler), BIP324 transport
    (v1 legacy / v2 encrypted), protocol version, the peer's tip height at connect, network
    class (IPv4 / IPv6 / Tor / I2P / CJDNS), and the disconnect / failure reason."""
    KIND_COLOR = {"connected": "#16C784", "inbound": "#00BFFF", "disconnect": "#F7931A",
                  "failed": "#f85149", "feeler": "#9d7bd8", "local": "#8aa0b4", "info": "#6a7f92"}
    COLS = ["time", "event", "peer", "role", "tr", "client", "peer height", "net", "address", "note"]
    def __init__(self):
        super().__init__(); v = QtWidgets.QVBoxLayout(self)
        top = QtWidgets.QHBoxLayout()
        self.info = QtWidgets.QLabel("Network activity — peer connect / inbound / disconnect / fail events")
        self.info.setStyleSheet("color:#16C784;font-weight:600"); top.addWidget(self.info, 1)
        top.addWidget(QtWidgets.QLabel("show"))
        self.filter = QtWidgets.QComboBox()
        self.filter.addItems(["all", "connected", "inbound", "disconnect", "failed", "feeler", "local"])
        self.filter.setToolTip("Filter the table by event type")
        self.filter.currentTextChanged.connect(lambda _: self._render()); top.addWidget(self.filter)
        top.addWidget(QtWidgets.QLabel("fmt"))
        self.fmt = QtWidgets.QComboBox()
        self.fmt.addItems(["human", "scientific", "18-dec exact"])
        self.fmt.setToolTip("Numeric display mode — human, scientific notation (18 significant digits),\n"
                            "or exact 18-decimal per the scientific-audit spec (Decimal, never float).\n"
                            "Decimals acknowledged: SAT 8 dp (₿ native) · USDC 6 dp · EVM 18 dp.")
        self.fmt.currentTextChanged.connect(lambda _: self._render()); top.addWidget(self.fmt)
        self.live = QtWidgets.QCheckBox("live"); self.live.setChecked(True); top.addWidget(self.live)
        clr = QtWidgets.QPushButton("clear")
        clr.clicked.connect(lambda: (self._events.clear(), self._seen.clear(), self._render())); top.addWidget(clr)
        v.addLayout(top)
        # live summary bar — running tallies + transport/network mix + this node's local addresses
        self.summary = QtWidgets.QLabel("…"); self.summary.setWordWrap(True)
        self.summary.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        self.summary.setStyleSheet("font-family:monospace;background:#070c14;color:#c8d6e5;"
                                   "border:1px solid #0e3d57;border-radius:6px;padding:6px")
        v.addWidget(self.summary)
        self.t = QtWidgets.QTableWidget(); self.t.setColumnCount(len(self.COLS))
        self.t.setHorizontalHeaderLabels(self.COLS)
        self.t.verticalHeader().setVisible(False)
        self.t.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.t.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.t.setAlternatingRowColors(True)
        # Interactive + one resizeColumnsToContents() per render — NOT ResizeToContents mode,
        # which re-measures every column on EVERY setItem: O(rows²·cols) per fill, freezing
        # the event loop for tens of seconds at n=200 on this CPU (found by faulthandler)
        hh = self.t.horizontalHeader(); hh.setSectionResizeMode(QtWidgets.QHeaderView.Interactive)
        hh.setStretchLastSection(False)   # every column hugs its content — no wide empty note column
        hh.setDefaultAlignment(QtCore.Qt.AlignLeft)
        hh.setMaximumSectionSize(240)     # cap any one column (long IPv6 addresses) — elide the overflow
        self.t.setTextElideMode(QtCore.Qt.ElideMiddle)   # keep both ends of an address visible
        self.t.setStyleSheet("QTableWidget{font-family:monospace;background:#05080d;color:#d6e3ef;"
                             "gridline-color:#0e2436;border:1px solid #0e3d57;border-radius:6px;"
                             "alternate-background-color:#080d16}"
                             "QHeaderView::section{background:#0c1a28;color:#8aa0b4;border:0;padding:4px}")
        v.addWidget(self.t, 1)
        self._events, self._seen, self._latest = [], set(), {}
        self._t = QtCore.QTimer(self); self._t.timeout.connect(self.refresh); self._t.start(3000)
    def refresh(self):
        if self.isVisible() and self.live.isChecked():
            spawn_fn(lambda: fetch_json("/api/netactivity?n=200"), self._on)
    def _on(self, d):
        d = d or {}; self._latest = d
        added = 0
        for e in sorted(d.get("events", []), key=lambda x: str(x.get("time") or "")):
            key = (e.get("time"), e.get("addr"), e.get("kind"), e.get("peer"))
            if key in self._seen:
                continue
            self._seen.add(key); self._events.append(e); added += 1
        self._events = self._events[-8000:]
        if added:
            self._render()
        else:
            self._render_summary()   # livePeers still moves even when the log window is quiet
    def _num(self, n):
        """One integer, three truths: human grouping, scientific (18 sig digits), or exact
        18-dp Decimal — all from the same exact int, so the modes can never disagree."""
        if n in (None, ""): return "—"
        try: n = int(n)
        except (TypeError, ValueError): return str(n)
        m = self.fmt.currentText()
        if m == "scientific": return sci_int(n, 18)
        if m == "18-dec exact": return dec18(n)
        return f"{n:,}"
    def _render(self):
        want = self.filter.currentText()
        rows = [e for e in self._events if want == "all" or e.get("kind") == want]
        self.t.setUpdatesEnabled(False)   # batch: one paint + one column measure per render
        self.t.setRowCount(len(rows))
        DASH = "—"
        for r, e in enumerate(rows):
            k = e.get("kind", "info"); col = QtGui.QColor(self.KIND_COLOR.get(k, "#d6e3ef"))
            role = e.get("conntype", "") or ("→ dial" if k == "failed" else "")
            tr = e.get("transport", "")
            addr = e.get("addr", "")
            # client column shows the peer's software (subver), e.g. Satoshi:31.0.0 — the useful,
            # varying version, not the near-constant protocol number
            client = e.get("subver", "")
            cells = [str(e.get("time", "")).split(" ")[-1] if e.get("time") else DASH,
                     k, ("#" + e["peer"]) if e.get("peer") else DASH, role or DASH, tr or DASH,
                     client or DASH, self._num(e.get("blocks")) if e.get("blocks") else DASH,
                     e.get("net", "") or DASH,
                     addr or DASH, e.get("reason", "") or DASH]   # note: only the failure/disconnect cause
            for c, val in enumerate(cells):
                it = QtWidgets.QTableWidgetItem(str(val))
                if str(val) == DASH:                             # dim the intentional blanks
                    it.setForeground(QtGui.QColor("#3a4b5c"))
                elif c == 1:                                     # colour the event-kind cell
                    it.setForeground(col); f = it.font(); f.setBold(True); it.setFont(f)
                elif c == 4 and tr == "v2":                      # BIP324 encrypted transport — flag green
                    it.setForeground(QtGui.QColor("#16C784"))
                elif c == 4 and tr == "v1":
                    it.setForeground(QtGui.QColor("#8aa0b4"))
                self.t.setItem(r, c, it)
        # the "note" column earns its space only when a visible row actually has a note
        has_note = any(e.get("reason") for e in rows)
        self.t.setColumnHidden(self.COLS.index("note"), not has_note)
        self.t.resizeColumnsToContents()  # single measure per render (respects max section size)
        self.t.setUpdatesEnabled(True)
        self.t.scrollToBottom()
        self._render_summary()
    def _render_summary(self):
        d = self._latest; tally = d.get("tally", {}) or {}
        tr = d.get("transports", {}) or {}; nets = d.get("nets", {}) or {}
        ct = d.get("conntypes", {}) or {}; local = d.get("local", []) or []
        N = self._num
        def seg(label, m):
            parts = [f"{k} {v}" for k, v in sorted(m.items(), key=lambda x: -x[1]) if v]
            return f"{label}: " + (" · ".join(parts) if parts else "—")
        # the number the dashboard quotes vs the numbers the log tallies: livePeers is
        # getpeerinfo NOW (null → RPC choked, shown as —); the tally counts log EVENTS
        # over the window — after an airgap toggle those diverge wildly, both correct.
        live = d.get("livePeers")
        counts = (f"<span style='color:#16C784;font-weight:700'>● {N(live) if live is not None else '—'} "
                  f"peers live now</span> <span style='color:#5a6b7b'>(RPC getpeerinfo)</span>  ·  "
                  f"log window — <b>{N(len(self._events))}</b> events shown: "
                  f"<span style='color:#16C784'>connects {N(tally.get('connected',0))}</span> · "
                  f"<span style='color:#00BFFF'>inbound {N(tally.get('inbound',0))}</span> · "
                  f"<span style='color:#F7931A'>disconnects {N(tally.get('disconnect',0))}</span> · "
                  f"<span style='color:#f85149'>failed {N(tally.get('failed',0))}</span>")
        line2 = (seg("transport", {"v2 (encrypted)": tr.get("v2", 0), "v1 (legacy)": tr.get("v1", 0)})
                 + "   |   " + seg("net", nets) + "   |   " + seg("roles", ct))
        line3 = ("local: " + (" · ".join(local) if local else "—"))
        if self.fmt.currentText() != "human":
            line3 += ("<br><span style='color:#5a6b7b'>decimals acknowledged: SAT 8 dp (₿ native) · "
                      "USDC 6 dp · EVM 18 dp — exact Decimal, never float · sync % is 4 dp by design</span>")
        self.summary.setText(f"{counts}<br>{line2}<br>{line3}")
        self.info.setText(f"Network activity — {len(self._events)} events (₿ANKON ₿TC WaaS) · "
                          f"parsed live from debug.log")


class OrdinalsTab(QtWidgets.QWidget):
    """🜚 Ordinals — OPTIONAL panel over the bankon-ord module (which wraps the `ord` CLI).
    Reads run in-process; MUTATIONS (create/inscribe/etch/mint/send) go through the shared
    webbridge subprocess with the same two-step protocol as the web Console: dry-run (gate
    verdict + exact command/batchfile) → explicit ⚠ ₿ROADCAST + confirm dialog. The node-RPC
    surface stays read-only; ord's own fail-closed gates guard every mutation.
    Degrades honestly: no module → says so; no `ord` binary → the preflight report says so."""
    def __init__(self):
        super().__init__(); v = QtWidgets.QVBoxLayout(self)
        h = QtWidgets.QLabel("🜚 Ordinals — inscriptions · runes · sat hunting (gated interaction)")
        h.setStyleSheet("font-weight:700;font-size:15px;color:#F7931A"); v.addWidget(h)
        top = QtWidgets.QHBoxLayout()
        top.addWidget(QtWidgets.QLabel("network:"))
        self.netbox = QtWidgets.QComboBox(); self.netbox.addItems(["mainnet", "testnet", "signet", "regtest"])
        top.addWidget(self.netbox)
        pf = QtWidgets.QPushButton("▶ preflight"); pf.setToolTip("Honest readiness report — never mutates")
        pf.clicked.connect(self.preflight); top.addWidget(pf)
        self.status = QtWidgets.QLabel("—"); top.addWidget(self.status); top.addStretch(1)
        v.addLayout(top)
        wl = QtWidgets.QHBoxLayout()
        wl.addWidget(QtWidgets.QLabel("wallet:"))
        self.wname = QtWidgets.QLineEdit(); self.wname.setPlaceholderText("ord wallet name (e.g. ord-main)")
        wl.addWidget(self.wname, 1)
        self.iso = QtWidgets.QLabel(""); wl.addWidget(self.iso)
        for label, meth in [("balance", "wallet_balance"), ("inscriptions", "wallet_inscriptions"),
                            ("outputs", "wallet_outputs")]:
            b = QtWidgets.QPushButton(label); b.clicked.connect(lambda _, m=meth: self.inspect(m))
            wl.addWidget(b)
        v.addLayout(wl)
        self.out = QtWidgets.QPlainTextEdit(); self.out.setReadOnly(True)
        self.out.setStyleSheet("font-family:monospace"); v.addWidget(self.out, 1)
        # ---- INTERACT (parity with the web Console 🜚 tab): create · inscribe · runes · send ----
        # Same two-step protocol via the shared webbridge: dry-run shows the gate verdict + the
        # exact command/batchfile, then an explicit ⚠ ₿ROADCAST. The bridge enforces the same
        # fail-closed gates as the CLI; node-RPC surface stays read-only — mutations go via ord.
        box = QtWidgets.QGroupBox("Interact — two-step: Dry-run → ⚠ ₿roadcast (gated by bankon-ord)")
        gl = QtWidgets.QGridLayout(box)
        gl.addWidget(QtWidgets.QLabel("ord server:"), 0, 0)
        self.srv = QtWidgets.QLineEdit(); self.srv.setPlaceholderText("http://127.0.0.1:8080 (wallet ops need `ord server`)")
        gl.addWidget(self.srv, 0, 1, 1, 5)
        cw = QtWidgets.QPushButton("create wallet"); cw.clicked.connect(lambda: self._simple("create_wallet"))
        rc = QtWidgets.QPushButton("receive"); rc.clicked.connect(lambda: self._simple("receive"))
        gl.addWidget(cw, 0, 6); gl.addWidget(rc, 0, 7)
        gl.addWidget(QtWidgets.QLabel("inscribe:"), 1, 0)
        self.ifile = QtWidgets.QLineEdit(); self.ifile.setPlaceholderText("file path")
        gl.addWidget(self.ifile, 1, 1, 1, 3)
        pick = QtWidgets.QPushButton("…"); pick.setFixedWidth(28)
        pick.clicked.connect(lambda: self.ifile.setText(QtWidgets.QFileDialog.getOpenFileName(self, "file to inscribe")[0] or self.ifile.text()))
        gl.addWidget(pick, 1, 4)
        self.ifee = QtWidgets.QDoubleSpinBox(); self.ifee.setRange(0.1, 5000); self.ifee.setValue(2); self.ifee.setSuffix(" sat/vB")
        gl.addWidget(self.ifee, 1, 5)
        bi = QtWidgets.QPushButton("Dry-run inscribe"); bi.clicked.connect(lambda: self._dry("inscribe"))
        gl.addWidget(bi, 1, 6, 1, 2)
        gl.addWidget(QtWidgets.QLabel("rune:"), 2, 0)
        self.rune = QtWidgets.QLineEdit(); self.rune.setPlaceholderText("RUNE•NAME"); gl.addWidget(self.rune, 2, 1)
        self.rdiv = QtWidgets.QSpinBox(); self.rdiv.setRange(0, 38); self.rdiv.setPrefix("div "); gl.addWidget(self.rdiv, 2, 2)
        self.rsup = QtWidgets.QLineEdit("0"); self.rsup.setPlaceholderText("supply"); self.rsup.setFixedWidth(70); gl.addWidget(self.rsup, 2, 3)
        self.rpre = QtWidgets.QLineEdit("0"); self.rpre.setPlaceholderText("premine"); self.rpre.setFixedWidth(70); gl.addWidget(self.rpre, 2, 4)
        self.rfee = QtWidgets.QDoubleSpinBox(); self.rfee.setRange(0.1, 5000); self.rfee.setValue(2); gl.addWidget(self.rfee, 2, 5)
        be = QtWidgets.QPushButton("Dry-run etch"); be.clicked.connect(lambda: self._dry("etch")); gl.addWidget(be, 2, 6)
        bm = QtWidgets.QPushButton("Dry-run mint"); bm.clicked.connect(lambda: self._dry("mint")); gl.addWidget(bm, 2, 7)
        gl.addWidget(QtWidgets.QLabel("send:"), 3, 0)
        self.sto = QtWidgets.QLineEdit(); self.sto.setPlaceholderText("destination address"); gl.addWidget(self.sto, 3, 1, 1, 2)
        self.sout = QtWidgets.QLineEdit(); self.sout.setPlaceholderText("inscription id · sat · '10 RUNE'"); gl.addWidget(self.sout, 3, 3, 1, 2)
        self.sfee = QtWidgets.QDoubleSpinBox(); self.sfee.setRange(0.1, 5000); self.sfee.setValue(2); gl.addWidget(self.sfee, 3, 5)
        bs = QtWidgets.QPushButton("Dry-run send"); bs.clicked.connect(lambda: self._dry("send")); gl.addWidget(bs, 3, 6, 1, 2)
        v.addWidget(box)
        self.go = QtWidgets.QPushButton("⚠ ₿ROADCAST (irreversible — spends a fee)")
        self.go.setStyleSheet("background:#3a2d10;color:#e3b341;font-weight:800")
        self.go.setEnabled(False); self.go.clicked.connect(self._broadcast); v.addWidget(self.go)
        self._pending = None
        note = QtWidgets.QLabel("Node-RPC surface stays read-only; ordinals mutations run through bankon-ord's "
                                "fail-closed gates (ordinal-wallet isolation · ≥0.1 ₿TC refusal · unknown balance refused) "
                                "— dry-run first, always. Same engine as the web Console 🜚 tab and the CLI.")
        note.setWordWrap(True); note.setStyleSheet("color:#8aa0b4"); v.addWidget(note)
        self.wname.textChanged.connect(self._iso_badge)

    def refresh(self):
        # participate quietly in the central refresh loop — ordinals work only on explicit clicks
        # (preflight spawns a subprocess; polling it every tick would be waste, not diagnostics).
        pass

    # ---- shared webbridge (subprocess; identical gating/receipts to the web Console) ----
    def _bridge(self, body, on_done):
        import subprocess
        req = dict(body); req.setdefault("net", self.netbox.currentText())
        if self.srv.text().strip(): req.setdefault("server_url", self.srv.text().strip())
        cwd = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bankon-ord")
        def run():
            p = subprocess.run(["python3", "-m", "bankon_ord.webbridge"], input=json.dumps(req),
                               capture_output=True, text=True, timeout=180, cwd=cwd)
            try: return json.loads(p.stdout)
            except Exception: return {"ok": False, "error": (p.stderr or p.stdout or "bridge failed")[:400]}
        spawn_fn(run, on_done=on_done,
                 on_fail=lambda e: self.out.setPlainText(f"bridge error: {e}"))

    def _feedback(self, tag, r):
        stamp = time.strftime("%H:%M:%S")
        self.out.setPlainText(f"[{stamp}] {tag} → {'✓' if r.get('ok') else '✗'} "
                              f"({r.get('elapsed_ms', '?')} ms)\n" + json.dumps(r, indent=2, default=str))
        self.status.setText(("● " if r.get("ok") else "○ ") + tag)

    def _mut_body(self, kind):
        w = self.wname.text().strip()
        return {"inscribe": {"op": "inscribe", "wallet": w, "file": self.ifile.text().strip(),
                             "fee_rate": self.ifee.value()},
                "etch": {"op": "etch", "wallet": w, "rune": self.rune.text().strip(),
                         "divisibility": self.rdiv.value(), "supply": self.rsup.text().strip() or "0",
                         "premine": self.rpre.text().strip() or "0", "fee_rate": self.rfee.value()},
                "mint": {"op": "mint", "wallet": w, "rune": self.rune.text().strip(),
                         "fee_rate": self.rfee.value()},
                "send": {"op": "send", "wallet": w, "to": self.sto.text().strip(),
                         "outgoing": self.sout.text().strip(), "fee_rate": self.sfee.value()}}[kind]

    def _simple(self, op):
        self.status.setText(f"… {op}")
        self._bridge({"op": op, "wallet": self.wname.text().strip()},
                     lambda r: self._feedback(op, r))

    def _dry(self, kind):
        body = self._mut_body(kind)
        self.status.setText(f"… dry-run {kind}")
        self.go.setEnabled(False); self._pending = None
        def done(r):
            self._feedback(f"dry-run {kind}", r)
            if r.get("ok"):
                self._pending = body
                self.go.setText(f"⚠ ₿ROADCAST {kind} (irreversible — spends a fee)")
                self.go.setEnabled(True)
        self._bridge(body, done)

    def _broadcast(self):
        if not self._pending: return
        body = dict(self._pending, confirm=True, approved=True)
        if QtWidgets.QMessageBox.warning(
                self, "₿roadcast for real?",
                f"{body['op']} on wallet {body.get('wallet')!r} — this spends a fee and cannot be undone.\n"
                "The dry-run you just reviewed is what will run.",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.Cancel) != QtWidgets.QMessageBox.Yes:
            return
        self.go.setEnabled(False); self._pending = None
        self.status.setText(f"… LIVE {body['op']}")
        self._bridge(body, lambda r: self._feedback(f"LIVE {body['op']}", r))

    def _ord(self):
        """Lazy import of the sibling bankon-ord module; None (with an honest status) if absent."""
        try:
            import sys as _s
            p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bankon-ord")
            if p not in _s.path: _s.path.insert(0, p)
            from bankon_ord import OrdCli
            return OrdCli(self.netbox.currentText())
        except Exception as e:
            self.status.setText(f"bankon-ord module unavailable: {e}")
            return None

    def _iso_badge(self, name):
        try:
            import sys as _s
            p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bankon-ord")
            if p not in _s.path: _s.path.insert(0, p)
            from bankon_ord import is_ordinal_wallet
            ok = is_ordinal_wallet(name)
            self.iso.setText("🜚 ordinal" if ok else "⛔ cardinal")
            self.iso.setStyleSheet("color:%s;font-weight:700" % ("#16C784" if ok else "#f85149"))
        except Exception:
            self.iso.setText("")

    def preflight(self):
        o = self._ord()
        if o is None: return
        self.status.setText("… preflight")
        spawn_fn(o.preflight,
                 on_done=lambda r: (self.out.setPlainText(json.dumps(r, indent=2, default=str)),
                                    self.status.setText("● ord ready" if r.get("ord_installed")
                                                        else "○ ord binary not installed (see report)")),
                 on_fail=lambda e: self.status.setText(f"preflight failed: {e}"))

    def inspect(self, method):
        o = self._ord()
        if o is None: return
        name = self.wname.text().strip()
        if not name:
            self.status.setText("enter a wallet name"); return
        self.status.setText(f"… {method}")
        fn = getattr(o, method)
        spawn_fn(lambda: fn(name),
                 on_done=lambda r: (self.out.setPlainText(json.dumps(r, indent=2, default=str)),
                                    self.status.setText(f"● {method} ok")),
                 on_fail=lambda e: (self.out.setPlainText(str(e)),
                                    self.status.setText(f"○ {method} failed (is `ord` installed & the "
                                                        f"{self.netbox.currentText()} node running?)")))


def os_release():
    """(pretty, family, pkg_install_cmd) from /etc/os-release. ₿ANKON ₿TC targets Debian
    variants first-class (apt); other Linux families are recognized and get their own
    package-manager install command so one-click installs still work."""
    info = {}
    try:
        for ln in open("/etc/os-release"):
            if "=" in ln:
                k, val = ln.rstrip().split("=", 1); info[k] = val.strip('"')
    except Exception:
        pass
    ids = (info.get("ID", "") + " " + info.get("ID_LIKE", "")).lower()
    pretty = info.get("PRETTY_NAME") or "unknown Linux"
    import shutil as _sh
    if "debian" in ids or "ubuntu" in ids or _sh.which("apt-get"):
        return pretty, "debian", ["apt-get", "install", "-y"]
    if _sh.which("dnf"):    return pretty, "fedora", ["dnf", "install", "-y"]
    if _sh.which("pacman"): return pretty, "arch", ["pacman", "-S", "--noconfirm"]
    if _sh.which("zypper"): return pretty, "suse", ["zypper", "--non-interactive", "install"]
    return pretty, "unknown", None


def etherape_status():
    """(found: bool, text) — EtherApe is the classic live network visualizer (GTK/libpcap);
    ₿ANKON documents it as the display reference the Net Map borrows its idioms from."""
    import shutil as _sh
    p = _sh.which("etherape")
    return (True, f"etherape found: {p}") if p else (False, "etherape not installed — sudo apt install etherape")


def etherape_launch(status_cb=None):
    """Launch EtherApe pre-filtered to ₿itcoin P2P traffic (port 8333). Live capture needs
    pcap privileges → pkexec (same escalation pattern as the ICE rfkill wall)."""
    ok, txt = etherape_status()
    if not ok:
        if status_cb: status_cb(txt)
        return
    try:
        # -f = capture filter (BPF). pkexec grants the capture privilege interactively.
        subprocess.Popen(["pkexec", "etherape", "-f", "port 8333"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        if status_cb: status_cb("EtherApe launching (BPF filter: port 8333) — authorize in the pkexec prompt")
    except Exception as e:
        if status_cb: status_cb(f"EtherApe launch failed: {e}")


class IceTab(QtWidgets.QWidget):
    """🧊 ICE — Intrusion Countermeasures Electronics: the wall between the network and the
    wallet, now with the forensic toolkit. The wall: CPU heat gate + radio kill (AIRGAP severs
    ₿luetooth/Wi-Fi/WWAN/NFC, via pkexec). The toolkit: geo/IP forensics (offline GeoLite2),
    Net Map cross-links, 18-decimal precision metrics, the exportable/shreddable `.history`
    connectivity evidence trail, and EtherApe live wire capture.
    Namesake: ICE, coined by Tom Maddox, popularized by William Gibson — see docs/ICE.md."""
    ICE_APP = os.path.expanduser("~/ICE/ice.py")
    RADIOS = [("bluetooth", "₿luetooth"), ("wifi", "Wi-Fi"), ("wwan", "Cellular"), ("nfc", "NFC")]
    def __init__(self):
        super().__init__()
        outer = QtWidgets.QVBoxLayout(self)
        scroll = QtWidgets.QScrollArea(); scroll.setWidgetResizable(True); scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        body = QtWidgets.QWidget(); v = QtWidgets.QVBoxLayout(body)
        scroll.setWidget(body); outer.addWidget(scroll)
        h = QtWidgets.QLabel("🧊 ICE — Intrusion Countermeasures Electronics")
        h.setStyleSheet("font-weight:700;font-size:15px;color:#00BFFF"); v.addWidget(h)
        sub = QtWidgets.QLabel('"…ice from ICE, Intrusion Countermeasures Electronics." — W. Gibson, Burning Chrome (1982) · docs/ICE.md')
        sub.setStyleSheet("color:#5a6b7b;font-size:10px"); v.addWidget(sub)
        # ---- the wall (unchanged behavior): CPU heat + radio kill ----
        self.temp = QtWidgets.QLabel("CPU: — °C"); self.temp.setStyleSheet("font-size:22px;font-weight:700"); v.addWidget(self.temp)
        # 🛡 UI-interception shield — blocks synthetic/injected input + the accessibility bridge
        shrow = QtWidgets.QHBoxLayout()
        self.shield = QtWidgets.QCheckBox("🛡 UI shield — block interception")
        self.shield.setToolTip("Drop input the window system didn't originate (synthetic/injected mouse+keys, "
                               "UI-automation, AT-SPI introspection). Does NOT stop OS screenshots or kernel "
                               "injection — for those, AIRGAP.")
        self.shield.toggled.connect(self._toggle_shield); shrow.addWidget(self.shield)
        self.shield_lbl = QtWidgets.QLabel("shield OFF"); self.shield_lbl.setStyleSheet("color:#f85149;font-weight:700")
        shrow.addWidget(self.shield_lbl); shrow.addStretch(1); v.addLayout(shrow)
        # persistent AIRGAP recommendation — the only countermeasure the shield can't provide itself
        self.airgap_rec = QtWidgets.QLabel("🛡 UI shield blocks in-process interception. For total isolation "
                                           "(screenshots, kernel injection) the recommended countermeasure is AIRGAP ↓")
        self.airgap_rec.setWordWrap(True)
        self.airgap_rec.setStyleSheet("background:#2a1200;border:1px solid #F7931A;border-radius:6px;"
                                      "color:#F7931A;padding:6px;font-weight:600")
        v.addWidget(self.airgap_rec)
        rl = QtWidgets.QHBoxLayout()
        ag = QtWidgets.QPushButton("🛑 AIRGAP (cut all radios)"); ag.clicked.connect(lambda: self._rfk("block")); rl.addWidget(ag)
        rs = QtWidgets.QPushButton("📡 Restore radios"); rs.clicked.connect(lambda: self._rfk("unblock")); rl.addWidget(rs)
        rl.addStretch(1); v.addLayout(rl)
        self.rlabel = QtWidgets.QLabel("radios — …"); self.rlabel.setStyleSheet("font-family:monospace"); v.addWidget(self.rlabel)
        launch = QtWidgets.QPushButton("Open full ICE controller (scaling · auto-cool · persistence · radios)")
        launch.clicked.connect(self._launch); v.addWidget(launch)
        # ---- forensic toolkit ----
        self.netmap_link = None                      # wired by Main → jump-to-Net-Map
        v.addWidget(self._geo_panel())
        self.transport = TransportSwitches("  (shared with ⟲ SPINTRADE)"); v.addWidget(self.transport)
        v.addWidget(self._evidence_panel())
        v.addWidget(self._precision_panel())
        v.addWidget(self._txmon_panel())
        v.addWidget(self._capture_panel())
        note = QtWidgets.QLabel("ICE gates CPU heat and the machine's radios; the forensic toolkit works offline "
                                "(local GeoLite2, local .history). AIRGAP severs every RF path between the network "
                                "and the wallet. No wallet data is involved.")
        note.setWordWrap(True); note.setStyleSheet("color:#8aa0b4"); v.addWidget(note); v.addStretch(1)
        self._ni, self._peers = {}, []
        self._t = QtCore.QTimer(self); self._t.timeout.connect(self.refresh); self._t.start(2000)
    # ---- 🔎 geo/IP forensics ----
    def _geo_panel(self):
        fr = QtWidgets.QFrame(); fr.setStyleSheet("QFrame{border:1px solid #0e3d57;border-radius:6px}")
        fl = QtWidgets.QVBoxLayout(fr)
        hd = QtWidgets.QLabel("🔎 Geo/IP forensics — offline GeoLite2 (City + ASN)")
        hd.setStyleSheet("color:#F7931A;font-weight:700;border:0"); fl.addWidget(hd)
        row = QtWidgets.QHBoxLayout()
        self.geo_in = QtWidgets.QLineEdit(); self.geo_in.setPlaceholderText("ip[:port] — or pick a connected peer →")
        self.geo_in.returnPressed.connect(self._geo_lookup); row.addWidget(self.geo_in, 1)
        self.geo_pick = QtWidgets.QComboBox(); self.geo_pick.setMinimumWidth(190)
        self.geo_pick.activated.connect(lambda i: (self.geo_in.setText(self.geo_pick.currentText()), self._geo_lookup()))
        row.addWidget(self.geo_pick)
        gb = QtWidgets.QPushButton("Lookup"); gb.clicked.connect(self._geo_lookup); row.addWidget(gb)
        self.geo_map_btn = QtWidgets.QPushButton("◎ show on Net Map"); self.geo_map_btn.setObjectName("secondary")
        self.geo_map_btn.setEnabled(False)
        self.geo_map_btn.clicked.connect(self._show_on_map); row.addWidget(self.geo_map_btn)
        fl.addLayout(row)
        self.geo_out = QtWidgets.QLabel("—"); self.geo_out.setStyleSheet("font-family:monospace;border:0;color:#d6e3ef")
        self.geo_out.setWordWrap(True); self.geo_out.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        fl.addWidget(self.geo_out)
        return fr
    def _geo_lookup(self):
        raw = self.geo_in.text().strip()
        ip = raw.rsplit(":", 1)[0].strip("[]") if raw else ""
        if not ip:
            self.geo_out.setText("enter an IP (or pick a peer)"); return
        g = geolocate(ip); a = asn_lookup(ip) or {}
        if not g:
            self.geo_out.setText(f"{ip} — no geolocation (Tor/I2P/unmapped){'' if HAVE_GEOIP else ' · GeoIP DB missing'}")
            self.geo_map_btn.setEnabled(any(p.get("addr", "").startswith(ip) for p in self._peers))
            return
        _nc = nearest_city_entry(g["lat"], g["lon"])
        stats = ("pop " + f"{_nc[5]:,}" + (f" · elev {_nc[6]} m" if _nc[6] else "") +
                 (f" · tz {_nc[7]}" if _nc[7] else "")) if len(_nc) > 5 else ""
        city = f"{g['city']} (GeoIP)" if g.get("city") else f"near {_nc[0]} (~{_nc[4]:.0f} km)"
        my = self._my_latlon()
        from services.geodesy import haversine_km
        dist = f"{haversine_km(my[0], my[1], g['lat'], g['lon']):,.0f} km from this node" if my else "node location unknown"
        ds = cities_stats()
        self.geo_out.setText(f"{ip}\n{flag(g['iso'])} {g['country']} ({g['iso']}) · {city}"
                             + (f"\nnearest-city stats: {stats}" if stats else "") +
                             f"\nlat {g['lat']:.4f} · lon {g['lon']:.4f} · {dist}\n"
                             f"AS{a.get('asn','?')} {a.get('org','—')}\n"
                             f"city dataset: {ds['source']} · {ds['cities']:,} cities"
                             + (f" · {ds['countries']} countries" if ds.get('countries') else "")
                             + ("" if ds.get("complete") else f" · {ds.get('note','')}"))
        self.geo_map_btn.setEnabled(any(p.get("addr", "").startswith(ip) for p in self._peers))
    def _show_on_map(self):
        raw = self.geo_in.text().strip()
        addr = next((p.get("addr") for p in self._peers if p.get("addr", "").startswith(raw.rsplit(':', 1)[0])), raw)
        if self.netmap_link: self.netmap_link(addr)
    def _my_latlon(self):
        la = (self._ni or {}).get("localaddresses") or []
        for a in la:
            g = geolocate(a.get("address", ""))
            if g: return g["lat"], g["lon"]
        for p in self._peers:
            ip = (p.get("addrlocal") or "").rsplit(":", 1)[0].strip("[]")
            if ip:
                g = geolocate(ip)
                if g: return g["lat"], g["lon"]
        return None
    # ---- 📜 connectivity evidence (.history) ----
    def _evidence_panel(self):
        fr = QtWidgets.QFrame(); fr.setStyleSheet("QFrame{border:1px solid #0e3d57;border-radius:6px}")
        fl = QtWidgets.QVBoxLayout(fr)
        hd = QtWidgets.QHBoxLayout()
        t = QtWidgets.QLabel("📜 Connectivity evidence — .history (rotates at 1 MB × 5)")
        t.setStyleSheet("color:#F7931A;font-weight:700;border:0"); hd.addWidget(t, 1)
        self.ev_size = QtWidgets.QLabel("—"); self.ev_size.setStyleSheet("color:#5a6b7b;border:0"); hd.addWidget(self.ev_size)
        fl.addLayout(hd)
        self.ev = QtWidgets.QTableWidget(0, 4)
        self.ev.setHorizontalHeaderLabels(["time", "kind", "addr / peer", "detail"])
        self.ev.horizontalHeader().setStretchLastSection(True); self.ev.verticalHeader().setVisible(False)
        self.ev.setEditTriggers(QtWidgets.QTableWidget.NoEditTriggers); self.ev.setMaximumHeight(230)
        self.ev.setStyleSheet("font-family:monospace;font-size:11px")
        fl.addWidget(self.ev)
        br = QtWidgets.QHBoxLayout()
        rb = QtWidgets.QPushButton("↻ Refresh"); rb.clicked.connect(self._ev_refresh); br.addWidget(rb)
        for fmt in ("csv", "json"):
            b = QtWidgets.QPushButton(f"⬇ Export {fmt.upper()}"); b.setObjectName("secondary")
            b.clicked.connect(lambda _c, f=fmt: self._ev_export(f)); br.addWidget(b)
        # mint the evidence trail as an NFT for extra (on-chain) verification — chain chooser
        self.mint_chain = QtWidgets.QComboBox()
        self.mint_chain.addItems(["Bitcoin Ordinals", "Bitcoin OP_RETURN", "Ethereum", "Polygon"])
        self.mint_chain.setToolTip("Where to inscribe the .history digest for tamper-evident verification.\n"
                                   "Ordinals = inscribe the SHA-256 as an inscription; OP_RETURN = timestamp anchor.")
        br.addWidget(self.mint_chain)
        mb = QtWidgets.QPushButton("⧉ Mint as NFT"); mb.setObjectName("secondary")
        mb.setToolTip("Hash the current .history and inscribe/anchor the digest on the chosen chain")
        mb.clicked.connect(self._ev_mint); br.addWidget(mb)
        br.addStretch(1)
        # 'care' = 7-pass secure erase (shred -n 7 -z -u). Default ON.
        self.care = QtWidgets.QCheckBox("care (7×)"); self.care.setChecked(True)
        self.care.setToolTip("Care = coreutils shred(1) with 7 overwrite passes + zero pass + unlink.\n"
                             "https://manpages.debian.org/testing/coreutils/shred.1.en.html\n"
                             "Off = a single-pass wipe (faster, less thorough).")
        br.addWidget(self.care)
        # local public history is STILL public — wipe it automatically on close. Default ON.
        self.autowipe = QtWidgets.QCheckBox("auto-wipe on exit"); self.autowipe.setChecked(True)
        self.autowipe.setToolTip("The local .history is a public record of your connectivity — wipe it on exit "
                                 "so it never lingers. Anchor/mint first if you want a permanent, verifiable copy.")
        br.addWidget(self.autowipe)
        # wipe intensity: casual (default) · recommended 93% · immediate 100% CPU
        self.wipe_intensity = QtWidgets.QComboBox()
        self.wipe_intensity.addItems(["casual (background)", "recommended (93%)", "immediate (100% CPU)"])
        self.wipe_intensity.setToolTip("How hard the secure wipe runs:\n"
                                       "• casual — niced background (ionice idle · nice 19), won't fight the UI (default)\n"
                                       "• recommended — cpulimit to ~93%\n"
                                       "• immediate — all-out, 100% CPU")
        br.addWidget(self.wipe_intensity)
        db = QtWidgets.QPushButton("🗑 Delete"); db.setObjectName("danger")
        db.setToolTip("Unlink every .history segment"); db.clicked.connect(self._ev_delete); br.addWidget(db)
        sb = QtWidgets.QPushButton("🔥 Shred"); sb.setObjectName("danger")
        sb.setToolTip("Secure removal via coreutils shred(1) — 7 passes when 'care' is on, else 1.\n"
                      "https://manpages.debian.org/testing/coreutils/shred.1.en.html")
        sb.clicked.connect(self._ev_shred); br.addWidget(sb)
        fl.addLayout(br)
        ref = QtWidgets.QLabel("secure erase: coreutils <a href='https://manpages.debian.org/testing/coreutils/shred.1.en.html'>shred(1)</a> "
                               "· 'care' = 7 overwrite passes + zero + unlink · public history is still public")
        ref.setOpenExternalLinks(True); ref.setStyleSheet("border:0;color:#5a6b7b;font-size:10px"); fl.addWidget(ref)
        self.ev_status = QtWidgets.QLabel(""); self.ev_status.setStyleSheet("color:#8aa0b4;border:0"); fl.addWidget(self.ev_status)
        return fr
    def _ev_mint(self):
        import hashlib
        from services import history_service as H
        recs = H.read_recent(100000)
        if not recs:
            self.ev_status.setText("nothing to mint — .history is empty"); return
        blob = "\n".join(json.dumps(r, separators=(",", ":"), sort_keys=True) for r in recs).encode()
        digest = hashlib.sha256(blob).hexdigest()
        chain = self.mint_chain.currentText()
        # measure the gas required — in SAT, from the LOCAL node (no external feed). An ordinal
        # inscription of a 32-byte digest ≈ commit + reveal; ~250 vB total is a safe estimate.
        gas = self._estimate_gas(chain)
        gas_line = (f"gas ≈ {gas['sat']:,} sat  ({gas['vb']} vB × {gas['rate']} sat/vB)  ·  pay {gas['sat']:,} PAI-sat\n"
                    if gas else "gas: (node fee estimate unavailable — will use node default)\n")
        if QtWidgets.QMessageBox.question(
                self, "⧉ Ordinal minter — inscribe .history digest",
                f"Inscribe the .history digest for extra verification?\n\n"
                f"records: {len(recs)}\nSHA-256: {digest}\nchain: {chain}\n{gas_line}\n"
                "Only the DIGEST goes on-chain — the evidence stays local (and is still public until wiped).\n"
                "After broadcast, BANKON follows the transaction on the ₿itcoin network from your own node.",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No) != QtWidgets.QMessageBox.Yes:
            return
        def work():
            if chain == "Bitcoin OP_RETURN":
                return post_json("/api/anchor", {"hash": digest}, timeout=30)   # existing WaaS anchor
            if chain == "Bitcoin Ordinals":
                return post_json("/api/ord", {"action": "inscribe-digest", "sha256": digest}, timeout=30)
            return post_json("/api/mint", {"chain": chain.lower(), "sha256": digest}, timeout=30)
        self.ev_status.setText(f"minting on {chain} — digest {digest[:16]}…")
        spawn_fn(work, lambda d: self._minted(d, chain, digest),
                 lambda e: self.ev_status.setText(f"mint via WaaS unavailable ({e}). Digest (verify manually): {digest}"))
    @staticmethod
    def _estimate_gas(chain):
        """Inscription gas in SAT, measured from the local node's fee estimate. ~250 vB covers
        a minimal ordinal digest inscription (commit + reveal); OP_RETURN is ~150 vB."""
        vb = 150 if "OP_RETURN" in chain else 250
        try:
            f = (rpc("estimatesmartfee", [1], timeout=6) or {}).get("feerate")
            rate = max(1, round(f * 1e5)) if f else None
        except Exception:
            rate = None
        if rate is None:
            return None
        return {"vb": vb, "rate": rate, "sat": vb * rate}
    def _minted(self, d, chain, digest):
        d = d or {}
        txid = d.get("txid") or d.get("reveal") or d.get("commit")
        insc = d.get("inscription")
        tag = insc or txid or d.get("note") or "submitted"
        self.ev_status.setText(f"✓ {chain}: {tag}")
        # streamline: FOLLOW the Bitcoin tx from our own node — no explorer, no external API
        if txid and chain.startswith("Bitcoin"):
            self._follow_txid = txid; self._follow_insc = insc
            self._followt = QtCore.QTimer(self); self._followt.timeout.connect(self._follow_tx)
            self._followt.start(6000); self._follow_tx()
    def _follow_tx(self):
        txid = getattr(self, "_follow_txid", None)
        if not txid: return
        def work():
            try:
                r = rpc("getmempoolentry", [txid], timeout=6)          # still pending
                return ("mempool", r)
            except Exception:
                pass
            try:
                gt = rpc("gettransaction", [txid], timeout=6)          # wallet-visible, may be confirmed
                return ("wallet", gt)
            except Exception:
                pass
            try:
                raw = rpc("getrawtransaction", [txid, True], timeout=6)  # txindex path
                return ("chain", raw)
            except Exception:
                return (None, None)
        spawn_fn(work, self._follow_render)
    def _follow_render(self, res):
        where, r = res or (None, None)
        txid = getattr(self, "_follow_txid", "")
        insc = getattr(self, "_follow_insc", None)
        head = f"⧉ inscription {insc[:18]}… · " if insc else ""
        if where == "mempool":
            fee = (r or {}).get("fees", {}).get("base")
            self.ev_status.setText(f"{head}tx {txid[:16]}… in mempool (unconfirmed{f', fee {fee} ₿TC' if fee else ''}) — following on local node")
        elif where in ("wallet", "chain"):
            conf = (r or {}).get("confirmations", 0) or 0
            if conf >= 1:
                self.ev_status.setText(f"{head}tx {txid[:16]}… CONFIRMED ({conf} conf) — evidence anchored on ₿itcoin")
                if conf >= 6 and getattr(self, "_followt", None):
                    self._followt.stop()                               # deeply confirmed → stop polling
            else:
                self.ev_status.setText(f"{head}tx {txid[:16]}… seen, 0 conf — following on local node")
        else:
            self.ev_status.setText(f"{head}tx {txid[:16]}… broadcast — not yet visible to local node")
    def _ev_refresh(self):
        from services import history_service as H
        recs = H.read_recent(400)
        self.ev.setRowCount(len(recs))
        for r, e in enumerate(recs):
            cells = [e.get("ts", "")[-14:], e.get("kind", ""), e.get("addr", "") or e.get("peer", "") or "—",
                     ", ".join(f"{k}={v}" for k, v in e.items() if k not in ("ts", "kind", "addr", "peer"))[:120]]
            for c, val in enumerate(cells):
                self.ev.setItem(r, c, QtWidgets.QTableWidgetItem(str(val)))
        self.ev.resizeColumnsToContents()
        self.ev_size.setText(f"{H.size_bytes()/1024:.0f} KiB on disk")
    def _ev_export(self, fmt):
        from services import history_service as H
        recs = H.read_recent(5000)
        if not recs:
            self.ev_status.setText("nothing to export"); return
        exp = Path.home() / "bankon-tools" / "exports"
        exp.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = exp / f"bankon-evidence-{stamp}.{fmt}"
        try:
            if fmt == "json":
                path.write_text(json.dumps(recs, indent=2))
            else:
                import csv as _csv
                keys = sorted({k for e in recs for k in e})
                with open(path, "w", newline="") as fh:
                    w = _csv.DictWriter(fh, fieldnames=keys); w.writeheader(); w.writerows(recs)
            self.ev_status.setText(f"✓ exported {len(recs)} records → {path}")
        except Exception as e:
            self.ev_status.setText(f"export failed: {e}")
    def _ev_delete(self):
        from services import history_service as H
        if QtWidgets.QMessageBox.question(self, "Delete", "Delete the .history evidence trail?") != QtWidgets.QMessageBox.Yes: return
        self.ev_status.setText(f"deleted {H.delete()} segment(s)"); self._ev_refresh()
    def _wipe_opts(self):
        passes = 7 if self.care.isChecked() else 1
        cpu = {0: None, 1: 93, 2: 100}[self.wipe_intensity.currentIndex()]     # casual · 93% · 100%
        return passes, cpu
    def _ev_shred(self):
        from services import history_service as H
        passes, cpu = self._wipe_opts()
        how = f"{passes}-pass, " + ("casual" if cpu is None else f"{cpu}% CPU")
        if QtWidgets.QMessageBox.question(self, "Shred",
                f"SHRED the .history evidence trail ({how}, unrecoverable)?") != QtWidgets.QMessageBox.Yes: return
        self.ev_status.setText(f"shredding ({how})…")
        spawn_fn(lambda: H.shred(passes=passes, cpu_pct=cpu),
                 lambda n: (self.ev_status.setText(f"shredded {n} segment(s) — {how}"), self._ev_refresh()))
    # ---- 📐 precision metrics (shared Decimal core with ₿TC.oracle) ----
    def _precision_panel(self):
        fr = QtWidgets.QFrame(); fr.setStyleSheet("QFrame{border:1px solid #0e3d57;border-radius:6px}")
        fl = QtWidgets.QVBoxLayout(fr)
        hd = QtWidgets.QLabel("📐 Precision metrics — exact Decimal, 18 dp (same core as ₿TC.oracle)")
        hd.setStyleSheet("color:#F7931A;font-weight:700;border:0"); fl.addWidget(hd)
        self.prec = QtWidgets.QLabel("—"); self.prec.setStyleSheet("font-family:monospace;border:0;color:#d6e3ef")
        self.prec.setWordWrap(True); fl.addWidget(self.prec)
        return fr
    def _fill_precision(self, nt, stale):
        up = getattr(self, "_uptime_s", None)
        rin, rout = nt.get("totalbytesrecv", 0), nt.get("totalbytessent", 0)
        lines = []
        lines.append(f"total received   {rin:,} B  ({dec18(rin, 1048576)} MiB)")
        lines.append(f"total sent       {rout:,} B  ({dec18(rout, 1048576)} MiB)")
        if up:
            lines.append(f"node uptime      {up:,} s  ({dec18(up, 3600)} h)")
            lines.append(f"mean rate in     {dec18(rin, up)} B/s")
            lines.append(f"mean rate out    {dec18(rout, up)} B/s")
        self.prec.setText("\n".join(lines) + ("   (cached)" if stale else ""))
    # ---- ⛓ blackICE — blockchain transaction monitor (ZMQ rawtx, exact integer sats) ----
    def _txmon_panel(self):
        fr = QtWidgets.QFrame(); fr.setStyleSheet("QFrame{border:1px solid #0e3d57;border-radius:6px}")
        fl = QtWidgets.QVBoxLayout(fr)
        hd = QtWidgets.QLabel("⛓ blackICE — blockchain transaction monitor (ZMQ rawtx · exact integer sats)")
        hd.setStyleSheet("color:#00BFFF;font-weight:700;border:0"); fl.addWidget(hd)
        row = QtWidgets.QHBoxLayout()
        self.txmon = QtWidgets.QCheckBox("monitor live transactions")
        self.txmon.setToolTip("Subscribe to the node's zmqpubrawtx feed (tcp://127.0.0.1:28333) and parse\n"
                              "every mempool arrival locally: txid, vsize and the exact output sum in\n"
                              "INTEGER SATOSHIS (never float). Off by default — during IBD this feed floods.")
        self.txmon.toggled.connect(self._txmon_toggle); row.addWidget(self.txmon)
        self.txmon_status = QtWidgets.QLabel("off"); self.txmon_status.setStyleSheet("color:#8aa0b4;border:0")
        row.addWidget(self.txmon_status); row.addStretch(1); fl.addLayout(row)
        self.txmon_stats = QtWidgets.QLabel("—")
        self.txmon_stats.setStyleSheet("font-family:monospace;border:0;color:#d6e3ef")
        self.txmon_stats.setWordWrap(True)
        self.txmon_stats.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        fl.addWidget(self.txmon_stats)
        self.txmon_tbl = QtWidgets.QTableWidget(); self.txmon_tbl.setColumnCount(5)
        self.txmon_tbl.setHorizontalHeaderLabels(["time", "txid", "vB", "in→out", "Σ outputs (₿ · 18 dp exact)"])
        self.txmon_tbl.verticalHeader().setVisible(False)
        self.txmon_tbl.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.txmon_tbl.setMaximumHeight(150)
        hh = self.txmon_tbl.horizontalHeader(); hh.setSectionResizeMode(QtWidgets.QHeaderView.ResizeToContents)
        self.txmon_tbl.setTextElideMode(QtCore.Qt.ElideMiddle)
        self.txmon_tbl.setStyleSheet("QTableWidget{font-family:monospace;background:#05080d;color:#d6e3ef;"
                                     "gridline-color:#0e2436;border:1px solid #14405c;border-radius:4px}"
                                     "QHeaderView::section{background:#0c1a28;color:#8aa0b4;border:0;padding:3px}")
        fl.addWidget(self.txmon_tbl)
        self._txz = None
        self._txbuf = deque(maxlen=8)          # newest txs for the table
        self._txtimes = deque(maxlen=1200)     # arrival stamps → tx/s over 60 s
        self._txseen = 0; self._txsats = 0; self._txvb = 0
        self._tx_mempool = None                # last RPC getmempoolinfo (the accuracy cross-check)
        self._txtimer = QtCore.QTimer(self); self._txtimer.timeout.connect(self._txmon_render)
        self._txticks = 0
        return fr
    def _txmon_toggle(self, on):
        if on:
            self._txz = ZmqService(self, with_tx=True)
            self._txz.txraw.connect(self._txmon_tx)
            self._txz.status.connect(lambda ok, m: (
                self.txmon_status.setText(("● " if ok else "○ ") + m),
                self.txmon_status.setStyleSheet(f"color:{'#16C784' if ok else '#f85149'};border:0;font-weight:700")))
            self._txz.start()
            self._txtimer.start(1000)
        else:
            if self._txz: self._txz.stop(); self._txz = None
            self._txtimer.stop()
            self.txmon_status.setText("off"); self.txmon_status.setStyleSheet("color:#8aa0b4;border:0")
    def _txmon_tx(self, raw):
        """One ZMQ rawtx arrival — parse and count; RENDERING waits for the 1 s tick (thermal)."""
        p = parse_tx(raw)
        if not p: return
        self._txseen += 1; self._txsats += p["out_sats"]; self._txvb += p["vsize"]
        self._txtimes.append(time.time())
        p["at"] = datetime.now().strftime("%H:%M:%S")
        self._txbuf.appendleft(p)
    def _txmon_render(self):
        if not anim_on(self): return
        now = time.time()
        rate = sum(1 for t in self._txtimes if now - t <= 60)
        self._txticks += 1
        if self._txticks % 5 == 1:            # cross-check against RPC truth every 5 s
            spawn("getmempoolinfo", self._txmon_mp, timeout=6)
        mp = self._tx_mempool or {}
        avg = self._txvb // self._txseen if self._txseen else 0
        # same live-vs-events honesty as the net log: ZMQ counts ARRIVALS since enable;
        # RPC counts the CURRENT SET — blocks mining txs out make them diverge, both correct.
        if isinstance(mp.get("size"), int):
            mpline = f"{mp['size']:,} tx · {mp.get('bytes', 0)/1e6:,.2f} MvB (current set — arrivals minus mined/evicted)"
        else:
            mpline = "—"
        self.txmon_stats.setText(
            f"arrivals since enable   {self._txseen:,} tx  ·  {dec18(rate, 60)} tx/s (60 s)  ·  avg {avg:,} vB\n"
            f"Σ outputs observed      {btc18(self._txsats)} ₿  (exact — integer sats, SAT 8 dp native)\n"
            f"mempool now (RPC)       {mpline}")
        self.txmon_tbl.setRowCount(len(self._txbuf))
        for r, p in enumerate(self._txbuf):
            for c, val in enumerate([p["at"], p["txid"], f"{p['vsize']:,}",
                                     f"{p['nin']}→{p['nout']}", btc18(p["out_sats"])]):
                self.txmon_tbl.setItem(r, c, QtWidgets.QTableWidgetItem(val))
    def _txmon_mp(self, m, stale):
        self._tx_mempool = m or {}
    # ---- 🕸 live wire capture ----
    def _capture_panel(self):
        fr = QtWidgets.QFrame(); fr.setStyleSheet("QFrame{border:1px solid #0e3d57;border-radius:6px}")
        fl = QtWidgets.QVBoxLayout(fr)
        hd = QtWidgets.QLabel("🕸 Live wire capture — EtherApe (display reference: docs/reference/etherape.md)")
        hd.setStyleSheet("color:#F7931A;font-weight:700;border:0"); fl.addWidget(hd)
        row = QtWidgets.QHBoxLayout()
        ok, txt = etherape_status()
        self.cap_status = QtWidgets.QLabel(txt); self.cap_status.setStyleSheet("border:0;color:%s" % ("#16C784" if ok else "#8aa0b4"))
        row.addWidget(self.cap_status, 1)
        lb = QtWidgets.QPushButton("▶ Launch (port 8333 filter)"); lb.setEnabled(ok)
        lb.setToolTip("pkexec etherape -f 'port 8333' — live pcap of ₿itcoin P2P traffic, radial traffic-proportional display")
        lb.clicked.connect(lambda: etherape_launch(self.cap_status.setText)); row.addWidget(lb)
        fl.addLayout(row)
        return fr
    def _cpu_temp(self):
        import glob as _g
        best = None
        for p in _g.glob("/sys/class/thermal/thermal_zone*/temp"):
            try:
                val = int(open(p).read().strip()) / 1000.0
            except Exception:
                continue
            if 0 < val < 150 and (best is None or val > best):
                best = val
        return best
    def refresh(self):
        if not self.isVisible(): return        # no 2s /sys+rfkill probing while the tab is hidden
        # forensic feeds at a gentler cadence (every 5th tick ≈ 10 s): peers for the picker
        # + cross-link, nettotals/uptime for the precision strip, evidence table size
        self._fc = (getattr(self, "_fc", -1) + 1) % 5
        if self._fc == 0:
            spawn("getpeerinfo", self._on_peers, timeout=10)
            spawn("getnetworkinfo", lambda ni, s: setattr(self, "_ni", ni or {}), timeout=8)
            spawn("getnettotals", self._fill_precision, timeout=8)
            spawn("uptime", lambda u, s: setattr(self, "_uptime_s", u), timeout=6)
            self._ev_refresh()
        t = self._cpu_temp()
        if t is not None:
            col = "#f85149" if t >= 85 else "#f0a020" if t >= 70 else "#16C784"
            self.temp.setText(f"CPU: {t:.0f} °C"); self.temp.setStyleSheet(f"font-size:22px;font-weight:700;color:{col}")
        states = []
        for kind, label in self.RADIOS:
            try:
                o = subprocess.run(["rfkill", "list", kind], capture_output=True, text=True).stdout
            except Exception:
                o = ""
            if not o.strip():
                continue
            states.append(f"{label}: {'on' if 'Soft blocked: yes' not in o else 'OFF'}")
        self.rlabel.setText("radios — " + ("   ·   ".join(states) if states else "none present"))
        # keep the shield's live block tally + tune the airgap recommendation to the current state
        if self.shield.isChecked():
            self.shield_lbl.setText("shield ON · %d blocked" % ICE_SHIELD.blocked)
        radios_up = any("OFF" not in s for s in states)
        if radios_up:
            self.airgap_rec.setText("🛡 UI shield blocks in-process interception. Radios are LIVE — for total "
                                    "isolation the recommended countermeasure is AIRGAP ↓")
            self.airgap_rec.setStyleSheet("background:#2a1200;border:1px solid #F7931A;border-radius:6px;color:#F7931A;padding:6px;font-weight:600")
        else:
            self.airgap_rec.setText("🛡 UI shield active · ✓ radios dark (AIRGAP engaged) — isolation at its strongest")
            self.airgap_rec.setStyleSheet("background:#04220f;border:1px solid #16C784;border-radius:6px;color:#16C784;padding:6px;font-weight:600")
    def _on_peers(self, peers, stale):
        self._peers = peers or []
        cur = self.geo_pick.currentText()
        self.geo_pick.blockSignals(True); self.geo_pick.clear()
        self.geo_pick.addItems([p.get("addr", "") for p in self._peers])
        i = self.geo_pick.findText(cur)
        if i >= 0: self.geo_pick.setCurrentIndex(i)
        self.geo_pick.blockSignals(False)
    def _toggle_shield(self, on):
        ICE_SHIELD.arm(on)
        self.shield_lbl.setText(("shield ON · %d blocked" % ICE_SHIELD.blocked) if on else "shield OFF")
        self.shield_lbl.setStyleSheet("color:%s;font-weight:700" % ("#16C784" if on else "#f85149"))
        from services import history_service as H
        H.append("ice-shield", state="armed" if on else "disarmed")
    def _rfk(self, action):
        try:
            subprocess.Popen(["pkexec", "rfkill", action, "all"])
        except Exception as e:
            self.rlabel.setText(f"rfkill failed: {e}")
        QtCore.QTimer.singleShot(1200, self.refresh)
    def _launch(self):
        import shutil as _sh
        if not os.path.exists(self.ICE_APP):
            self.rlabel.setText("full ICE app not found at ~/ICE/ice.py"); return
        term = _sh.which("x-terminal-emulator") or _sh.which("gnome-terminal") or _sh.which("xterm")
        subprocess.Popen([term, "-e", self.ICE_APP] if term else [self.ICE_APP])


class ICEShield(QtCore.QObject):
    """🛡 ICE UI-interception guard. Intrusion Countermeasures for the surface itself: an
    application-wide event filter that DROPS input the window system did not originate —
    synthesized/programmatically-posted mouse & key events (the shape of UI automation and
    injection) never reach the widgets. While armed it also tears down the accessibility
    bridge so another process can't introspect or drive the widget tree.

    Honest scope: this blocks in-process/AT-SPI/synthetic-event interception. It does NOT stop
    a raw OS screenshot or kernel-level input injection — for THOSE, the only real countermeasure
    is to go dark, which is why the shield persistently recommends AIRGAP."""
    def __init__(self):
        super().__init__(); self.armed = False; self.blocked = 0
    def arm(self, on):
        app = QtWidgets.QApplication.instance()
        if on and not self.armed:
            os.environ["QT_ACCESSIBILITY"] = "0"          # deny the AT-SPI automation bridge
            os.environ["NO_AT_BRIDGE"] = "1"
            app.installEventFilter(self)
            self.armed = True
        elif not on and self.armed:
            app.removeEventFilter(self)
            self.armed = False
    _INPUT = {QtCore.QEvent.MouseButtonPress, QtCore.QEvent.MouseButtonRelease,
              QtCore.QEvent.MouseButtonDblClick, QtCore.QEvent.KeyPress, QtCore.QEvent.KeyRelease}
    def eventFilter(self, obj, ev):
        if ev.type() in self._INPUT:
            # spontaneous() is True only for events the window system delivered; sendEvent/postEvent
            # from another automation path is False → drop it. Synthesized mouse sources are dropped too.
            if not ev.spontaneous():
                self.blocked += 1
                return True
            try:
                if ev.type() in (QtCore.QEvent.MouseButtonPress, QtCore.QEvent.MouseButtonRelease,
                                 QtCore.QEvent.MouseButtonDblClick) and \
                   ev.source() != QtCore.Qt.MouseEventNotSynthesized:
                    self.blocked += 1
                    return True
            except Exception:
                pass
        return False


ICE_SHIELD = ICEShield()


class TransportSwitches(QtWidgets.QFrame):
    """ICE transport controls — the physical links SPINTRADE rides. Just switches:
    VPN · Bluetooth · Ethernet · Infrared, each on/off. Lives ONLY in the 🧊 ICE tab and
    the ⟲ SPINTRADE tab; both read the OS as the single source of truth, so a switch flipped
    in one is reflected in the other (shared state, no duplicate bookkeeping). Every mutation
    escalates through pkexec — the same wall the ICE AIRGAP uses. Compatible with ICE by
    construction: AIRGAP cuts the radios beneath these switches, and they re-probe to match."""
    def __init__(self, host_label=""):
        super().__init__()
        self.setStyleSheet("QFrame{border:1px solid #0e3d57;border-radius:6px}")
        v = QtWidgets.QVBoxLayout(self)
        hd = QtWidgets.QLabel(f"🔀 ICE transport switches{host_label}")
        hd.setStyleSheet("color:#F7931A;font-weight:700;border:0"); v.addWidget(hd)
        self._rows = {}
        grid = QtWidgets.QGridLayout()
        # (key, label, tooltip)
        defs = [
            ("vpn", "VPN", "Route the exchange through a VPN exit (NetworkManager vpn/wireguard) — shortest-route leg"),
            ("bluetooth", "₿luetooth", "Bluetooth radio (rfkill) — the SPINTRADE bluetooth exchange path"),
            ("ethernet", "Ethernet", "Wired NIC up/down (ip link) — the wired exchange path"),
            ("infrared", "Infrared", "IrDA / rc-core receiver — the infrared exchange path"),
        ]
        for r, (k, lab, tip) in enumerate(defs):
            name = QtWidgets.QLabel(lab); name.setStyleSheet("border:0;color:#c9d4e0"); name.setToolTip(tip)
            state = QtWidgets.QLabel("—"); state.setStyleSheet("border:0;font-family:'DejaVu Sans Mono',monospace")
            btn = QtWidgets.QPushButton("…"); btn.setFixedWidth(64); btn.setEnabled(False)
            btn.clicked.connect(lambda _c, key=k: self._flip(key))
            grid.addWidget(name, r, 0); grid.addWidget(state, r, 1); grid.addWidget(btn, r, 2)
            self._rows[k] = {"state": state, "btn": btn, "on": None, "extra": None}
        v.addLayout(grid)
        self.note = QtWidgets.QLabel(""); self.note.setStyleSheet("border:0;color:#5a6b7b;font-size:10px")
        self.note.setWordWrap(True); v.addWidget(self.note)
    def _set_row(self, k, on, present=True, extra=None):
        row = self._rows[k]; row["on"] = on; row["extra"] = extra
        s, b = row["state"], row["btn"]
        if not present:
            s.setText("not present"); s.setStyleSheet("border:0;color:#5a6b7b;font-family:'DejaVu Sans Mono',monospace")
            b.setEnabled(False); b.setText("—"); return
        b.setEnabled(True)
        s.setText(("ON" if on else "OFF") + (f"  {extra}" if extra else ""))
        s.setStyleSheet("border:0;font-weight:700;font-family:'DejaVu Sans Mono',monospace;color:%s"
                        % ("#16C784" if on else "#f85149"))
        b.setText("turn OFF" if on else "turn ON")
    def refresh(self):
        from services import ice_transport as T
        self._set_row("bluetooth", T.bluetooth_state() == "on", present=T.bluetooth_state() is not None)
        es = T.ethernet_state()
        self._set_row("ethernet", es == "on", present=es is not None,
                      extra=" ".join(f"{n}:{o}" for n, o in T.ethernet_ifaces()[:2]))
        ir = T.infrared_state()
        self._set_row("infrared", ir == "on", present=ir is not None)
        active, avail = (T.vpn_state() or (None, []))
        if not avail:
            self._set_row("vpn", False, present=False)
            self.note.setText("VPN: no NetworkManager vpn/wireguard profiles configured. "
                              "Switches escalate via pkexec; AIRGAP (🧊 ICE) overrides the radios.")
        else:
            self._rows["vpn"]["avail"] = avail
            self._set_row("vpn", active is not None, present=True, extra=(f"· {active}" if active else f"· {len(avail)} avail"))
            self.note.setText("VPN routes the exchange through the shortest-route exit. "
                              "Switches escalate via pkexec; AIRGAP (🧊 ICE) overrides the radios.")
    def _flip(self, k):
        from services import ice_transport as T
        row = self._rows[k]; on = row["on"]
        if k == "bluetooth": T.bluetooth_set(not on)
        elif k == "ethernet": T.ethernet_set(not on)
        elif k == "infrared": T.infrared_set(not on)
        elif k == "vpn":
            avail = row.get("avail") or []
            if on:
                a, _ = T.vpn_state()
                if a: T.vpn_down(a)
            elif avail:
                T.vpn_up(avail[0])
        QtCore.QTimer.singleShot(1200, self.refresh)    # re-probe → both tabs converge on OS truth


# uniform "chip" base for the toolbar heartbeat band — corporate: quiet dark well, rounded,
# semantic color carried by the text (survives ◐ invert since color is set at runtime)
CHIP = "padding:2px 8px; border-radius:8px; background:#0d1724; font-weight:600;"


class BannerBar(QtWidgets.QFrame):
    """'₿ the wallet you can ₿ANKON' — now a live control surface, not a static label.
    DRAGGABLE: grab and drop it above the tabs (top, default) or below them (bottom);
    the choice persists across sessions. The CORE dynamic control sits immediately to the
    right of the title text: red = OFF (click to start) · orange = ON (click = graceful
    stop) · green ring = feeding from a fresh tip."""
    def __init__(self, main):
        super().__init__(); self.main = main
        self.setObjectName("titlebar"); self.setCursor(QtCore.Qt.OpenHandCursor)
        lay = QtWidgets.QHBoxLayout(self); lay.setContentsMargins(11, 5, 11, 5)
        # live diagnostics ride the banner: chain state on the left, wire+thermal on the right —
        # dim monospace so the title stays the headline
        _dstyle = "background:transparent;border:0;color:#8aa0b4;font-family:'DejaVu Sans Mono',monospace;font-size:10px"
        self.diagL = QtWidgets.QLabel("⛓ —"); self.diagL.setStyleSheet(_dstyle); lay.addWidget(self.diagL)
        lay.addStretch(1)
        self.text = QtWidgets.QLabel("₿  the wallet you can ₿ANKON")
        self.text.setObjectName("titletext"); lay.addWidget(self.text)
        lay.addSpacing(12)
        self.corebtn = QtWidgets.QPushButton("● CORE"); self.corebtn.setObjectName("corebanner")
        self.corebtn.setCursor(QtCore.Qt.PointingHandCursor)
        self.corebtn.clicked.connect(self._core_click)
        lay.addWidget(self.corebtn)
        lay.addStretch(1)
        self.diagR = QtWidgets.QLabel("⇅ —"); self.diagR.setStyleSheet(_dstyle); lay.addWidget(self.diagR)
        self.setToolTip("Drag this banner to dock it above the tabs (top) or below them (bottom, default) — position is remembered\n"
                        "© 2026 ₿ANKON — all rights preserved")
        self._press = None; self._core_up = None; self._diag = {}
        self.set_core(None, False, "₿itcoin Core state — probing…")
    def set_diag(self, **parts):
        """Merge diagnostic fragments (block/peers/net/temp) and re-render both banner sides."""
        self._diag.update({k: v for k, v in parts.items() if v})
        d = self._diag
        left = " · ".join(x for x in (f"⛓ {d.get('block')}" if d.get("block") else "",
                                      f"⇶ {d.get('peers')}" if d.get("peers") else "") if x)
        right = " · ".join(x for x in (d.get("net", ""), f"🌡 {d.get('temp')}" if d.get("temp") else "") if x)
        self.diagL.setText(left or "⛓ —")
        self.diagR.setText(right or "⇅ —")
    def set_core(self, up, feeding, tip):
        self._core_up = up
        col = "#8aa0b4" if up is None else ("#F7931A" if up else "#f85149")
        ring = "#16C784" if feeding else "#2e4a63"
        self.corebtn.setText("● CORE …" if up is None else ("● CORE ON" if up else "● CORE OFF"))
        self.corebtn.setStyleSheet(
            f"QPushButton#corebanner{{background:#0d1724;color:{col};border:2px solid {ring};"
            f"border-radius:10px;font-weight:800;padding:3px 12px;letter-spacing:1px}}"
            f"QPushButton#corebanner:hover{{border:2px solid #00BFFF;background:#10202e}}")
        self.corebtn.setToolTip(tip + "\nclick: " + ("start ₿itcoin Core" if up is False else "graceful stop (bitcoin-cli stop)"))
    def _core_click(self):
        if self._core_up is None: return
        sb = self.main.statusBar()
        if self._core_up:
            if QtWidgets.QMessageBox.question(self, "Stop ₿itcoin Core",
                                              "Gracefully stop ₿itcoin Core?") != QtWidgets.QMessageBox.Yes:
                return
            def work():
                try: return post_json("/api/node/stop", {}, timeout=15)
                except Exception:
                    r = subprocess.run([str(Path(BTC_BIN) / "bitcoin-cli"), f"-datadir={DATADIR}", "stop"],
                                       capture_output=True, text=True, timeout=15)
                    return {"note": r.stdout.strip() or r.stderr.strip() or "stopping…"}
            sb.showMessage("Stopping ₿itcoin Core (graceful)…", 8000)
        else:
            def work():
                try: return post_json("/api/node/start", {}, timeout=8)
                except Exception:
                    subprocess.Popen([str(Path(BTC_BIN) / "bitcoind"), f"-datadir={DATADIR}", "-daemon"],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
                    return {"note": "bitcoind launched directly (Console down)"}
            sb.showMessage("Starting ₿itcoin Core…", 8000)
        spawn_fn(work, lambda d: sb.showMessage(str((d or {}).get("note") or (d or {}).get("error") or "…"), 8000))
    # ---- drag-to-dock ----
    def mousePressEvent(self, e):
        if e.button() == QtCore.Qt.LeftButton:
            self._press = e.globalPosition(); self.setCursor(QtCore.Qt.ClosedHandCursor)
        super().mousePressEvent(e)
    def mouseReleaseEvent(self, e):
        self.setCursor(QtCore.Qt.OpenHandCursor)
        if self._press is None:
            return super().mouseReleaseEvent(e)
        moved = (e.globalPosition() - self._press).manhattanLength(); self._press = None
        if moved >= 18:                                   # a real drag, not a click
            cw = self.main.centralWidget()
            mid_y = cw.mapToGlobal(QtCore.QPoint(0, cw.height() // 2)).y()
            self.main._dock_banner("bottom" if e.globalPosition().y() > mid_y else "top")
        super().mouseReleaseEvent(e)


class SpintradeTab(QtWidgets.QWidget):
    """⟲ SPINTRADE — OPTIONAL module with ABSOLUTE attach/detach (toolbar toggle: built on
    enable, destroyed on disable — no timers, no polling, zero footprint when off).
    The ₿itcoin blockchain expressed as chain-native trading pairs, PRICES IN SAT:
    SATPAY (what an on-chain payment costs right now), the SAT/vB blockspace book
    (mempool = order book, last block = last fill), ₿TC/BLOCK, SAT/HASH, ₿TC/DAY.
    No external feed, no fiat — the venue is the blockchain. Data: WaaS /api/pairs
    (bankon-waas/pairs.mjs proposal module) when up; direct node RPC otherwise."""
    def __init__(self):
        super().__init__(); v = QtWidgets.QVBoxLayout(self)
        h = QtWidgets.QLabel("⟲ SPINTRADE — chain-native pairs · prices in SAT")
        h.setStyleSheet("color:#F7931A;font-weight:800;font-size:15px;letter-spacing:1px"); v.addWidget(h)
        pol = QtWidgets.QLabel("no external feed · no fiat · the blockchain is the venue — mempool = order book · last block = last fill")
        pol.setStyleSheet("color:#5a6b7b;font-size:10px"); v.addWidget(pol)
        # SATPAY hero — the headline price in SAT
        hero = QtWidgets.QFrame(); hero.setStyleSheet("QFrame{border:2px solid #F7931A;border-radius:10px}")
        hv = QtWidgets.QVBoxLayout(hero)
        ht = QtWidgets.QLabel("SATPAY — price of an on-chain payment, right now")
        ht.setStyleSheet("color:#F7931A;font-weight:700;border:0"); hv.addWidget(ht)
        self.satpay = QtWidgets.QLabel("— sat")
        self.satpay.setStyleSheet("color:#eef3f8;font-family:'DejaVu Sans Mono',monospace;font-size:30px;font-weight:800;border:0")
        self.satpay.setToolTip("typical payment = 140 vB (1 input · 2 outputs, P2WPKH) × the next-block SAT/vB ask")
        hv.addWidget(self.satpay)
        self.satpay_ladder = QtWidgets.QLabel("ladder: —")
        self.satpay_ladder.setStyleSheet("color:#8aa0b4;font-family:'DejaVu Sans Mono',monospace;border:0")
        hv.addWidget(self.satpay_ladder)
        v.addWidget(hero)
        # pairs board
        self.board = QtWidgets.QPlainTextEdit(); self.board.setReadOnly(True)
        self.board.setStyleSheet("font-family:'DejaVu Sans Mono',monospace;font-size:12px;background:#05080d")
        v.addWidget(self.board, 1)
        # ICE transport switches (shared with 🧊 ICE) + shortest-route locator
        self.transport = TransportSwitches("  (shared with 🧊 ICE)"); v.addWidget(self.transport)
        self.route = QtWidgets.QLabel("shortest route: —")
        self.route.setStyleSheet("color:#8aa0b4;font-family:'DejaVu Sans Mono',monospace;font-size:11px")
        self.route.setWordWrap(True); v.addWidget(self.route)
        self.src = QtWidgets.QLabel("source: —"); self.src.setStyleSheet("color:#5a6b7b;font-size:10px"); v.addWidget(self.src)
    def refresh(self):
        # ICE transport switches + shortest-route locator refresh with the tab
        if hasattr(self, "transport"): self.transport.refresh()
        self._locate_route()
        # ICE compatibility: when the 🧊 wall is up (AIRGAP / setnetworkactive=false) the venue
        # suspends honestly instead of quoting a dark network
        spawn("getnetworkinfo", self._gate, self._node_down, timeout=8)
    def _locate_route(self):
        # shortest exchange route: our node's location (via ICE geo) → nearest connected peer,
        # the natural first hop for an on-chain swap. Reuses the geo forensics ICE already has.
        def work():
            from services.geodesy import haversine_km
            peers = rpc("getpeerinfo", timeout=8) or []
            me = None
            ni = rpc("getnetworkinfo", timeout=6) or {}
            for a in (ni.get("localaddresses") or []):
                g = geolocate(a.get("address", ""))
                if g: me = (g["lat"], g["lon"]); break
            best = None
            for p in peers:
                host = (p.get("addr") or "").rsplit(":", 1)[0].strip("[]")
                g = geolocate(host)
                if not g: continue
                if me:
                    d = haversine_km(me[0], me[1], g["lat"], g["lon"])
                    if best is None or d < best[0]:
                        best = (d, p.get("addr"), g)
            return me, best
        spawn_fn(work, self._show_route)
    def _show_route(self, res):
        me, best = res or (None, None)
        if not best:
            self.route.setText("shortest route: locating peers…"); return
        d, addr, g = best
        _nc = nearest_city_entry(g["lat"], g["lon"])
        where = f"{flag(g['iso'])} {g['country']} · near {_nc[0]}"
        self.route.setText(f"shortest exchange route → {addr}  ({where}, ~{d:,.0f} km)  "
                           + ("via VPN exit if switched" if True else ""))
    def _node_down(self, _e):
        self.satpay.setText("— sat")
        self.board.setPlainText("node unreachable — no venue without the blockchain")
        self.src.setText("source: none (₿itcoin Core down)")
    def _gate(self, ni, _stale):
        if ni.get("networkactive") is False:
            self.satpay.setText("⛔ AIRGAP")
            self.satpay_ladder.setText("ladder: suspended")
            self.board.setPlainText("🧊 ICE wall engaged — setnetworkactive=false.\n"
                                    "SPINTRADE suspends while the network is dark: no fresh mempool, no honest quotes.\n"
                                    "Restore the network (Control → AIRGAP, or ICE) to resume.")
            self.src.setText("source: suspended by ICE AIRGAP — SPINTRADE and the wall are compatible by design")
            return
        def waas():
            import urllib.request as _u
            with _u.urlopen(WAAS_URL + "/api/pairs", timeout=8) as r:
                return json.loads(r.read())
        spawn_fn(waas, lambda d: self._render(d, "WaaS /api/pairs (bankon-waas/pairs.mjs)"),
                 lambda _e: spawn_fn(self._local_pairs, lambda d: self._render(d, "direct node RPC (WaaS down)")))
    @staticmethod
    def _local_pairs():
        """Same pair definitions as pairs.mjs, computed straight from the node — exact ints."""
        tip = rpc("getbestblockhash", timeout=8)
        hdr = rpc("getblockheader", [tip], timeout=8)
        st = rpc("getblockstats", [tip], timeout=10)
        mp = rpc("getmempoolinfo", timeout=8)
        asks = {}
        for k, blocks in (("nextBlock", 1), ("3blk", 3), ("6blk", 6), ("1day", 144)):
            try:
                f = (rpc("estimatesmartfee", [blocks], timeout=8) or {}).get("feerate")
                asks[k] = max(1, round(f * 1e5)) if f else None
            except Exception:
                asks[k] = None
        fees, sub = int(st.get("totalfee", 0) or 0), int(st.get("subsidy", 0) or 0)
        work = work_from_bits(int(hdr.get("bits", "0"), 16))
        sat_hash = dec18(fees, work) if work else None
        return {"ok": True, "asOfBlock": st.get("height"),
                "pairs": [
                    {"pair": "SAT/vB", "book": {"asks": asks,
                        "floor_minRelay": max(1, round((mp.get("mempoolminfee") or 0) * 1e5))},
                     "lastFill": {"block": st.get("height"),
                                  "feerate_percentiles_p10_p90": st.get("feerate_percentiles"),
                                  "avg": st.get("avgfeerate"), "unit": "sat/vB"}},
                    {"pair": "SATPAY", "last": asks["nextBlock"] * 140 if asks.get("nextBlock") else None,
                     "ladder": {k: (v * 140 if v else None) for k, v in asks.items()}, "typicalVb": 140, "unit": "sat"},
                    {"pair": "₿TC/BLOCK", "subsidy": btc18(sub), "fees": btc18(fees),
                     "last": btc18(sub + fees), "lastSat": sub + fees, "unit": "₿TC"},
                    {"pair": "SAT/HASH", "last": sat_hash, "expectedHashes": str(work), "unit": "sat (18 dp)"},
                    {"pair": "₿TC/DAY", "last": btc18(sub * 144), "lastSat": sub * 144, "unit": "₿TC"},
                    {"pair": "vB/BLOCK", "last": 1000000,
                     "lastBlockUsed": round((st.get("total_weight") or 0) / 4), "unit": "vB"},
                ]}
    def _render(self, d, src):
        if not d or not d.get("ok"):
            self.src.setText("source: unavailable — " + str((d or {}).get("error", "no data"))); return
        P = {p["pair"]: p for p in d.get("pairs", [])}
        sp = P.get("SATPAY", {})
        self.satpay.setText(f"{sp.get('last'):,} sat" if sp.get("last") else "— sat (no estimate yet)")
        lad = sp.get("ladder") or {}
        self.satpay_ladder.setText("ladder:  " + "  ·  ".join(
            f"{k} {v:,} sat" if v else f"{k} —" for k, v in lad.items()))
        lines = []
        sv = P.get("SAT/vB", {})
        b = sv.get("book", {}); a = b.get("asks", {}); lf = sv.get("lastFill", {})
        lines.append("SAT/vB     blockspace market (asks by depth · floor · last fill)")
        lines.append(f"           asks: next {a.get('nextBlock') or '—'} · 3blk {a.get('3blk') or '—'} · "
                     f"6blk {a.get('6blk') or '—'} · 1day {a.get('1day') or '—'}   floor {b.get('floor_minRelay','—')}")
        lines.append(f"           last fill @ block {lf.get('block','—')}: p10-p90 {lf.get('feerate_percentiles_p10_p90','—')} · avg {lf.get('avg','—')}")
        bb = P.get("₿TC/BLOCK", {})
        _sat = f"   = {bb['lastSat']:,} sat" if bb.get("lastSat") else ""
        lines.append(f"₿TC/BLOCK  reward last block: {bb.get('last','—')} ₿TC{_sat}")
        lines.append(f"           subsidy {bb.get('subsidy','—')} + fees {bb.get('fees','—')}")
        sh = P.get("SAT/HASH", {})
        lines.append(f"SAT/HASH   {sh.get('last','—')}   (expected hashes {int(sh.get('expectedHashes') or 0):.3e})"
                     if sh.get("expectedHashes") else f"SAT/HASH   {sh.get('last','—')}")
        dy = P.get("₿TC/DAY", {})
        _dsat = f"   = {dy['lastSat']:,} sat" if dy.get("lastSat") else ""
        lines.append(f"₿TC/DAY    issuance {dy.get('last','—')} ₿TC{_dsat}")
        vb = P.get("vB/BLOCK", {})
        lines.append(f"vB/BLOCK   supply {vb.get('last','—'):,} vB · last block used {vb.get('lastBlockUsed') or '—':,} vB")
        self.board.setPlainText("\n".join(lines))
        self.src.setText(f"source: {src} · as of block {d.get('asOfBlock','—')} · prices in SAT · exact to 18 dp")


class AdminWindow(QtWidgets.QWidget):
    """🛠 ADMIN — every toolbar toggle plus admin actions in ONE POPUP, styled after the
    ₿ANKON launcher's window choreography: ⚓ DOCK parks it, 📞 CALL summons the console,
    so the two windows always FIND EACH OTHER — even across multiple displays. Resizable;
    drag the ⠿ grip and DROP it — near a console edge or a screen corner it SNAPS to that
    dock, anywhere else it docks to that OPEN SPACE — and both the dock choice and the
    exact position are remembered across sessions. Read-only invariants hold: nothing in
    this window can spend or touch a key.

    BIRTH DOCK: with nothing remembered yet, the popup begins life docked on the GEO MAP
    (the geo display's corner when the tab is up, the tab area otherwise) and can then be
    moved to any preference — every move is remembered. It docks with the ₿ANKON LAUNCHER
    too (a separate GTK process, found via wmctrl exactly the way the launcher finds the
    console), not only with the console window."""
    DOCK_FREE = "open space (remembered)"
    DOCKS = ["geo map", "console right", "console left", "console banner",
             "launcher right", "launcher left",
             "screen ↖", "screen ↗", "screen ↙", "screen ↘", DOCK_FREE]

    def __init__(self, main):
        super().__init__(None, QtCore.Qt.Window)
        self.main = main
        self.setWindowTitle("🛠 ₿ANKON Admin")
        self.setMinimumSize(300, 340)          # resizable — this is only the floor
        self.resize(420, 540)
        v = QtWidgets.QVBoxLayout(self)
        grow = QtWidgets.QHBoxLayout()
        self.grip = QtWidgets.QLabel("⠿ drag")
        self.grip.setStyleSheet("color:#8aa0b4;font-weight:800;padding:2px 8px;border:1px solid #14405c;border-radius:6px")
        self.grip.setToolTip("Drag the popup by this grip. DROP near a console edge or a screen corner to "
                             "snap-dock there; drop anywhere else to dock in that open space. Remembered.")
        self.grip.setCursor(QtCore.Qt.OpenHandCursor)
        self.grip.installEventFilter(self); self._gdrag = None
        grow.addWidget(self.grip)
        t = QtWidgets.QLabel("🛠 ADMIN — toggles & controls"); t.setStyleSheet("color:#F7931A;font-weight:800")
        grow.addWidget(t); grow.addStretch(1)
        v.addLayout(grow)
        # ── toggles: two-way mirrors of the console toolbar (flip here or there — same switch) ──
        tg = QtWidgets.QGroupBox("Toggles"); tv = QtWidgets.QVBoxLayout(tg)
        for text, src in (("🌍 Geo Map tab", main.geo_chk),
                          ("⟲ SPINTRADE (consent-gated)", main.spin_chk),
                          ("◐ invert theme", main.inv_chk),
                          ("🖤 blackICE theme", main.blackice_chk)):
            tv.addWidget(self._mirror_chk(text, src))
        rrow = QtWidgets.QHBoxLayout()
        rrow.addWidget(QtWidgets.QLabel("↻ auto-refresh"))
        self.rate = QtWidgets.QComboBox()
        for i in range(main.rate.count()):
            self.rate.addItem(main.rate.itemText(i), main.rate.itemData(i))
        self.rate.setCurrentIndex(main.rate.currentIndex())
        self.rate.currentIndexChanged.connect(main.rate.setCurrentIndex)
        main.rate.currentIndexChanged.connect(
            lambda i: (self.rate.blockSignals(True), self.rate.setCurrentIndex(i), self.rate.blockSignals(False)))
        rrow.addWidget(self.rate); rrow.addStretch(1)
        rrow.addWidget(QtWidgets.QLabel("⏸@"))
        self.ptemp = QtWidgets.QSpinBox(); self.ptemp.setRange(80, 110); self.ptemp.setSuffix("°C")
        self.ptemp.setValue(main.pausetemp.value()); self.ptemp.setToolTip(main.pausetemp.toolTip())
        self.ptemp.valueChanged.connect(main.pausetemp.setValue)      # same-value set doesn't loop
        main.pausetemp.valueChanged.connect(self.ptemp.setValue)
        rrow.addWidget(self.ptemp)
        tv.addLayout(rrow)
        v.addWidget(tg)
        # ── admin actions ──
        ag = QtWidgets.QGroupBox("Admin"); av = QtWidgets.QGridLayout(ag)
        acts = [("↻ refresh all now", "Refresh the active tab immediately", main.do_refresh),
                ("🌐 open web Console", CONSOLE_URL, lambda: webbrowser.open(CONSOLE_URL)),
                ("＋ open WaaS", WAAS_URL, lambda: webbrowser.open(WAAS_URL)),
                ("🧹 clear RPC cache", "Drop every cached RPC result — next refresh pulls fresh", self._clear_cache),
                ("🗑 wipe .history", "Delete the local connectivity evidence trail (confirmed first)", self._wipe_history),
                ("🧭 reset saved layout", "Forget tab order, geomap toggles and this popup's dock memory", self._reset_layout)]
        for i, (txt, tip, cb) in enumerate(acts):
            b = QtWidgets.QPushButton(txt); b.setToolTip(tip); b.clicked.connect(cb)
            av.addWidget(b, i // 2, i % 2)
        v.addWidget(ag)
        # ── 🧊 I.C.E — the TOOL (Intrusion Countermeasures Electronics): thermal wall +
        # RF kill-switch (AIRGAP) + forensics. NOT the 🖤 blackICE THEME above. Per
        # docs/ICE.md the admin only LINKS to ICE — every ICE action executes in ICE
        # itself ("ICE has precedence"; no remote-control surface).
        ig = QtWidgets.QGroupBox("🧊 I.C.E — the tool"); iv = QtWidgets.QHBoxLayout(ig)
        it = QtWidgets.QPushButton("🧊 ICE tab")
        it.setToolTip("Open the console's 🧊 ICE tab — CPU temp · AIRGAP (cut all radios) · "
                      "restore radios · forensics · evidence trail")
        it.clicked.connect(self._show_ice_tab); iv.addWidget(it)
        ic = QtWidgets.QPushButton("🛠 full controller")
        ic.setToolTip("Launch the standalone ICE controller (~/ICE/ice.py — GTK tray app, "
                      "self-elevates via sudo in a terminal)")
        ic.clicked.connect(self._launch_ice); iv.addWidget(ic)
        inote = QtWidgets.QLabel("🖤 blackICE above is the THEME — this is the wall.")
        inote.setStyleSheet("color:#5a7891"); inote.setWordWrap(True); iv.addWidget(inote, 1)
        v.addWidget(ig)
        # ── window choreography (the launcher's DOCK / CALL, in-process) ──
        wg = QtWidgets.QGroupBox("Window"); wv = QtWidgets.QHBoxLayout(wg)
        self.dockbox = QtWidgets.QComboBox(); self.dockbox.addItems(self.DOCKS)
        saved = QtCore.QSettings("BANKON", "bankon-qt").value("admin/dock", "geo map")
        if saved in self.DOCKS: self.dockbox.setCurrentText(saved)
        self.dockbox.setToolTip("Where ⚓ DOCK parks this popup — the geo map (its birth dock), a console "
                                "edge, the ₿ANKON launcher's side, a screen corner, or the remembered open space")
        wv.addWidget(self.dockbox, 1)
        dk = QtWidgets.QPushButton("⚓ DOCK"); dk.setToolTip("Park the popup at the chosen dock (remembered); "
                                                            "'geo map' also brings the Geo Map tab up")
        dk.clicked.connect(lambda: self._apply_dock(switch=True)); wv.addWidget(dk)
        cl = QtWidgets.QPushButton("📞 CALL console")
        cl.setToolTip("Bring the console window HERE — onto this popup's display — and raise both; "
                      "they find each other even across multiple displays")
        cl.clicked.connect(self._call_console); wv.addWidget(cl)
        v.addWidget(wg)
        self.status = QtWidgets.QLabel(""); self.status.setStyleSheet("color:#8aa0b4"); self.status.setWordWrap(True)
        v.addWidget(self.status); v.addStretch(1)

    def _mirror_chk(self, text, src):
        c = QtWidgets.QCheckBox(text)
        c.setChecked(src.isChecked()); c.setToolTip(src.toolTip())
        c.toggled.connect(src.setChecked)                 # admin → toolbar (real switch)
        src.toggled.connect(                              # toolbar → admin (silent echo)
            lambda on, cc=c: (cc.blockSignals(True), cc.setChecked(on), cc.blockSignals(False)))
        return c

    # ── grip drag: move the whole popup; on drop, snap-dock or claim the open space ──
    def eventFilter(self, obj, ev):
        if obj is self.grip:
            t = ev.type()
            if t == QtCore.QEvent.MouseButtonPress and ev.button() == QtCore.Qt.LeftButton:
                self._gdrag = ev.globalPosition().toPoint() - self.frameGeometry().topLeft()
                self.grip.setCursor(QtCore.Qt.ClosedHandCursor); return True
            if t == QtCore.QEvent.MouseMove and self._gdrag is not None:
                self.move(ev.globalPosition().toPoint() - self._gdrag); return True
            if t == QtCore.QEvent.MouseButtonRelease and self._gdrag is not None:
                self._gdrag = None; self.grip.setCursor(QtCore.Qt.OpenHandCursor)
                self._drop_snap(); return True
        return super().eventFilter(obj, ev)

    def _drop_snap(self):
        fg = self.frameGeometry(); mg = self.main.frameGeometry()
        scr = (self.screen() or QtGui.QGuiApplication.primaryScreen()).availableGeometry()
        lg = self._launcher_geom()
        mode = self.DOCK_FREE
        if self._geomap_rect().contains(fg.center()) and getattr(self.main, "geo", None) is not None \
                and self.main.geo.isVisible():
            mode = "geo map"                 # dropped ON the geo display → its birth dock
        elif abs(fg.left() - mg.right()) < 48 and abs(fg.top() - mg.top()) < 220:
            mode = "console right"
        elif abs(fg.right() - mg.left()) < 48 and abs(fg.top() - mg.top()) < 220:
            mode = "console left"
        elif lg is not None and abs(fg.left() - lg.right()) < 48 and abs(fg.top() - lg.top()) < 220:
            mode = "launcher right"
        elif lg is not None and abs(fg.right() - lg.left()) < 48 and abs(fg.top() - lg.top()) < 220:
            mode = "launcher left"
        elif mg.contains(fg.center()) and fg.center().y() > mg.center().y():
            mode = "console banner"
        else:
            for corner, cx, cy in (("screen ↖", scr.left(), scr.top()), ("screen ↗", scr.right(), scr.top()),
                                   ("screen ↙", scr.left(), scr.bottom()), ("screen ↘", scr.right(), scr.bottom())):
                if (min(abs(fg.left() - cx), abs(fg.right() - cx)) < 56
                        and min(abs(fg.top() - cy), abs(fg.bottom() - cy)) < 56):
                    mode = corner; break
        self.dockbox.blockSignals(True); self.dockbox.setCurrentText(mode); self.dockbox.blockSignals(False)
        QtCore.QSettings("BANKON", "bankon-qt").setValue("admin/dock", mode)
        if mode != self.DOCK_FREE:
            self._apply_dock(mode)
            self.status.setText(f"⚓ snapped: {mode} (remembered)")
        else:
            self.status.setText("⚓ docked to this open space — position remembered")

    @staticmethod
    def _launcher_geom():
        """The ₿ANKON launcher's window rect — a SEPARATE GTK process, found via wmctrl
        exactly the way the launcher finds the console. None when it isn't running (or
        wmctrl is absent), so callers can say so instead of docking to nowhere."""
        import shutil
        if not shutil.which("wmctrl"):
            return None
        try:
            out = subprocess.run(["wmctrl", "-lG"], capture_output=True, text=True, timeout=5).stdout
        except Exception:
            return None
        for ln in out.splitlines():
            p = ln.split(None, 7)
            if len(p) >= 8 and "launcher" in p[7].lower() and (
                    "bankon" in p[7].lower() or "₿ankon" in p[7].lower()):
                try:
                    return QtCore.QRect(int(p[2]), int(p[3]), int(p[4]), int(p[5]))
                except ValueError:
                    pass
        return None

    def _geomap_rect(self):
        """Global rect of the geo map's display when the tab is up; the tab area otherwise
        — the popup's BIRTH dock anchors here."""
        geo = getattr(self.main, "geo", None)
        if geo is not None and geo.isVisible():
            return QtCore.QRect(geo.stack.mapToGlobal(QtCore.QPoint(0, 0)), geo.stack.size())
        tabs = self.main.tabs
        return QtCore.QRect(tabs.mapToGlobal(QtCore.QPoint(0, 0)), tabs.size())

    def _apply_dock(self, mode=None, switch=False):
        mode = mode or self.dockbox.currentText()
        QtCore.QSettings("BANKON", "bankon-qt").setValue("admin/dock", mode)
        fg = self.frameGeometry(); mg = self.main.frameGeometry()
        scr = (self.screen() or QtGui.QGuiApplication.primaryScreen()).availableGeometry()
        if mode == "geo map":                # birth dock — the globe's LEFT flank,
            geo = getattr(self.main, "geo", None)   # below the 🏠 node overlay so both show
            if switch and geo is not None:
                self.main.tabs.setCurrentWidget(geo)
            r = self._geomap_rect()
            y = r.top() + max(130, (r.height() - fg.height()) // 2)
            pos = QtCore.QPoint(r.left() + 12,
                                min(max(r.top() + 8, y), max(r.top() + 8, r.bottom() - fg.height() - 8)))
        elif mode == "console right":
            pos = QtCore.QPoint(mg.right() + 8, mg.top())
        elif mode == "console left":
            pos = QtCore.QPoint(mg.left() - fg.width() - 8, mg.top())
        elif mode == "console banner":       # the '₿ the wallet you can ₿ANKON' field, bottom-left
            pos = QtCore.QPoint(mg.left() + 16, mg.bottom() - fg.height() - 16)
        elif mode.startswith("launcher"):
            lg = self._launcher_geom()
            if lg is None:
                self.show(); self.raise_(); self.activateWindow()
                self.status.setText("⚓ launcher not found — is the ₿ANKON launcher running (and wmctrl installed)?")
                return
            pos = (QtCore.QPoint(lg.right() + 8, lg.top()) if mode.endswith("right")
                   else QtCore.QPoint(lg.left() - fg.width() - 8, lg.top()))
        elif mode.startswith("screen"):
            c = mode.split()[-1]
            pos = QtCore.QPoint(scr.left() + 12 if c in ("↖", "↙") else scr.right() - fg.width() - 12,
                                scr.top() + 12 if c in ("↖", "↗") else scr.bottom() - fg.height() - 12)
        else:                                # open space — stay at the remembered free spot
            self.show(); self.raise_(); self.activateWindow(); return
        self.move(pos); self.show(); self.raise_(); self.activateWindow()
        self.status.setText(f"⚓ docked: {mode}")

    def _call_console(self):
        m = self.main
        fg = self.frameGeometry()
        scr = (self.screen() or QtGui.QGuiApplication.primaryScreen()).availableGeometry()
        mw, mh = m.frameGeometry().width(), m.frameGeometry().height()
        x = fg.left() - mw - 8                             # console to the popup's LEFT if it fits…
        if x < scr.left(): x = fg.right() + 8              # …otherwise to its right
        x = max(scr.left(), min(x, scr.right() - mw))
        y = max(scr.top(), min(fg.top() - 24, scr.bottom() - mh))
        m.showNormal(); m.move(x, y)                       # crosses displays to reach the popup
        m.raise_(); m.activateWindow()
        self.raise_(); self.activateWindow()               # popup back on top — DOCK relation
        self.status.setText("📞 console called to this display — popup at its side.")

    # ── 🧊 I.C.E — links only; the tool itself acts (docs/ICE.md: "ICE has precedence") ──
    def _show_ice_tab(self):
        self.main.tabs.setCurrentWidget(self.main.ice)
        self.main.showNormal(); self.main.raise_(); self.main.activateWindow()
        self.raise_()
        self.status.setText("🧊 ICE tab opened in the console — the wall, not the theme.")
    def _launch_ice(self):
        try:
            self.main.ice._launch()
            self.status.setText("🧊 launching the full ICE controller — sudo prompt in the terminal.")
        except Exception as e:
            self.status.setText(f"ICE launch failed: {e}")

    def _clear_cache(self):
        try:
            from services.rpc_service import clear_cache
            clear_cache(); self.status.setText("🧹 RPC cache cleared — next refresh pulls fresh.")
        except Exception as e:
            self.status.setText(f"cache clear failed: {e}")

    def _wipe_history(self):
        r = QtWidgets.QMessageBox.question(
            self, "Wipe .history?",
            "Delete the local .history evidence trail (a PUBLIC record of connectivity)?\n"
            "Mint/anchor first in 🧊 ICE if you want a verifiable copy. This cannot be undone.",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No, QtWidgets.QMessageBox.No)
        if r == QtWidgets.QMessageBox.Yes:
            try:
                from services import history_service as H
                H.delete(); self.status.setText("🗑 .history wiped.")
            except Exception as e:
                self.status.setText(f"wipe failed: {e}")

    def _reset_layout(self):
        st = QtCore.QSettings("BANKON", "bankon-qt")
        for k in ("tabs/order", "tabs/geomap", "admin/dock", "admin/geometry", "admin/open",
                  "banner/dock", "geomap/borders", "geomap/cities", "geomap/accuracy",
                  "geomap/price", "geomap/tz", "geomap/ovl_node", "geomap/ovl_net", "geomap/ovl_blocks",
                  "geomap/feed", "geomap/marks"):
            st.remove(k)
        self.status.setText("🧭 saved layout forgotten — defaults return next launch.")

    # memory of position/size — every move or resize is written (multi-display safe)
    def moveEvent(self, e):
        QtCore.QSettings("BANKON", "bankon-qt").setValue("admin/geometry", self.saveGeometry())
        super().moveEvent(e)
    def resizeEvent(self, e):
        QtCore.QSettings("BANKON", "bankon-qt").setValue("admin/geometry", self.saveGeometry())
        super().resizeEvent(e)
    def closeEvent(self, e):
        QtCore.QSettings("BANKON", "bankon-qt").setValue("admin/open", "false")
        e.ignore(); self.hide()                # popup hides — reopen from the 🛠 toolbar button


class Main(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__(); self.setWindowTitle("₿ANKON ₿ITCOIN Wallet as a Service")
        # Universal sizing: fill ~92% of whatever screen we open on (laptop, 4K, anything),
        # centred, and never larger than that screen. Percent-of-screen instead of fixed px.
        scr = self.screen() or QtGui.QGuiApplication.primaryScreen()
        avail = scr.availableGeometry() if scr else QtCore.QRect(0, 0, 1280, 800)
        self.resize(int(avail.width() * 0.92), int(avail.height() * 0.92))
        self.setMinimumSize(min(720, avail.width()), min(520, avail.height()))
        self.move(avail.center() - self.rect().center())
        self.tabs = QtWidgets.QTabWidget()
        # Central content inset by a margin; an electric-blue glow renders in that
        # margin = an "auric" shimmer on the outer periphery.
        central = QtWidgets.QWidget(); cl = QtWidgets.QVBoxLayout(central); cl.setContentsMargins(13, 13, 13, 13); cl.setSpacing(8)
        self.titlebar = BannerBar(self)
        cl.addWidget(self.titlebar); cl.addWidget(self.tabs); self.setCentralWidget(central)
        self._central_lay = cl
        # bottom is the DEFAULT starting position (drag it up to re-dock above the tabs)
        if QtCore.QSettings("BANKON", "bankon-qt").value("banner/dock", "bottom") == "bottom":
            self._dock_banner("bottom", save=False)
        self._glow = QtWidgets.QGraphicsDropShadowEffect(self)
        self._glow.setColor(QtGui.QColor("#00BFFF")); self._glow.setOffset(0, 0); self._glow.setBlurRadius(22)
        self.tabs.setGraphicsEffect(self._glow)
        self._glowAnim = QtCore.QPropertyAnimation(self._glow, b"blurRadius")   # shimmer = pulsing blur
        self._glowAnim.setDuration(2600); self._glowAnim.setLoopCount(-1)
        self._glowAnim.setKeyValueAt(0.0, 12); self._glowAnim.setKeyValueAt(0.5, 38); self._glowAnim.setKeyValueAt(1.0, 12)
        self._glowAnim.start()
        self.ov = OverviewTab(); self.node = NodeTab()
        self.logs = LogsTab()            # ₿itcoin Core logs — tail/search/verbosity/export
        self.net = NetworkTab()
        self.mp = CardsTab(["txs", "virtual size", "memory / max", "min relay fee", "mempool min fee",
                            "total fee", "unbroadcast", "RBF / loaded"], mp_fill, ["getmempoolinfo"])
        self.blk = BlocksTab()
        self.idx = IndexesTab()
        self.map = NetworkMapTab()
        self.geo = None          # Geo Map is OPTIONAL (toolbar toggle, default OFF) — built lazily so
                                 # its GeoIP lookups + globe spin-timer cost nothing unless enabled.
        self.spin = None         # ⟲ SPINTRADE is OPTIONAL — always starts OFF; consent-gated attach.
        self.admin = None        # 🛠 ADMIN popup — built on first open (toolbar button).
        self.ords = OrdinalsTab()   # 🜚 Ordinals — a STANDARD tab (idle-free: no timers; work only on click)
        self.oracle = OracleTab()
        self.con = ConsoleTab()
        self.ctl = ControlTab()          # localhost / local-machine client control center
        self.netlog = NetLogTab()        # live network activity log (₿ANKON ₿TC WaaS)
        self.ice = IceTab()              # 🧊 ICE — network↔wallet wall (CPU + radios)
        # 🧊 / 📡 ride as REAL tab icons (hi-res color-emoji renders, smoothly downscaled) —
        # sharper than inline-text emoji at tab font size
        self.tabs.setIconSize(QtCore.QSize(22, 22))
        for w, name in [(self.ov,"Overview"),(self.node,"Node"),(self.logs,"Logs"),(self.net,"Network"),(self.map,"Net Map"),
                        (self.netlog,("📡", "Net Log")),(self.mp,"Mempool"),(self.blk,"₿locks"),(self.oracle,"₿TC.oracle"),
                        (self.ords,"🜚 Ordinals"),(self.idx,"Indexes"),(self.ctl,"🖥 Control"),
                        (self.ice,("🧊", "ICE")),(self.con,"RPC Console")]:
            if isinstance(name, tuple):
                self.tabs.addTab(w, emoji_icon(name[0]), name[1])
            else:
                self.tabs.addTab(w, name)
        # tabs are DRAG-AND-DROP re-orderable — the client's chosen order persists across
        # sessions (restore BEFORE connecting tabMoved, so restoring doesn't re-save)
        self.tabs.setMovable(True)
        self._restore_tab_order()
        self.tabs.tabBar().tabMoved.connect(self._save_tab_order)
        self.tabs.currentChanged.connect(self.do_refresh)
        # ICE forensics → Net Map cross-link: jump to the map with the peer selected
        self.ice.netmap_link = self._show_peer_on_map

        bar = self.addToolBar("main")
        ref = QtWidgets.QPushButton("↻ Refresh"); ref.clicked.connect(self.do_refresh); bar.addWidget(ref)
        bar.addWidget(QtWidgets.QLabel("  refresh "))
        self.rate = QtWidgets.QComboBox()
        for label, ms in [("off",0),("10s",10000),("30s",30000),("1 min",60000),("5 min",300000)]:
            self.rate.addItem(label, ms)
        self.rate.setCurrentText("1 min"); self.rate.currentIndexChanged.connect(self.apply_rate); bar.addWidget(self.rate)
        self.geo_chk = QtWidgets.QCheckBox(" 🌍 Geo Map")          # GeoIP map tab — PERSISTED, default ON
        self.geo_chk.setToolTip("Show the Geo Map tab (needs geoip/*.mmdb). Remembered across sessions.")
        self.geo_chk.toggled.connect(self._toggle_geo); bar.addWidget(self.geo_chk)
        # ⟲ SPINTRADE — optional module, ALWAYS starts OFF; the client chooses to open it and
        # consents through the innerstand gate first. Absolute attach/detach: built on enable,
        # destroyed on disable.
        self.spin_chk = QtWidgets.QCheckBox(" ⟲ SPINTRADE OFF")
        self.spin_chk.setToolTip("Open SPINTRADE — the blockchain as chain-native trading pairs, prices in SAT.\n"
                                 "Starts OFF every session; enabling asks for your informed consent first.")
        self.spin_chk.toggled.connect(self._toggle_spintrade); bar.addWidget(self.spin_chk)
        self._style_spin_chk(False)                       # OFF is obvious at all times
        self.inv_chk = QtWidgets.QCheckBox(" ◐ invert")           # polarity inversion — whole window
        self.inv_chk.setToolTip("Polarity inversion ('reverse video'): invert the entire window's theme.\n"
                                "Computed from the dark palette — see docs/design.md → Polarity inversion.")
        self.inv_chk.toggled.connect(self._toggle_invert); bar.addWidget(self.inv_chk)
        self.blackice_chk = QtWidgets.QCheckBox(" 🖤 blackICE")     # transparent · black font · black outlines
        self.blackice_chk.setToolTip("blackICE theme — transparent fields, black font, thin prominent black outline "
                                     "on every field (mutually exclusive with ◐ invert).")
        self.blackice_chk.toggled.connect(self._toggle_blackice); bar.addWidget(self.blackice_chk)
        adm = QtWidgets.QPushButton("🛠 Admin")
        adm.setToolTip("Open the ADMIN popup — every toggle + admin actions in one resizable window,\n"
                       "with ⚓ DOCK / 📞 CALL choreography (launcher-style) so it and the console\n"
                       "find each other even across multiple displays. Dock spot is remembered.")
        adm.clicked.connect(self._show_admin); bar.addWidget(adm)
        self.status_lbl = QtWidgets.QLabel("  ● checking…"); self.status_lbl.setStyleSheet("color:#8aa0b4; " + CHIP); bar.addWidget(self.status_lbl)
        self.core_lbl = QtWidgets.QLabel(" ● CORE"); bar.addWidget(self.core_lbl)
        self.core_lbl.setToolTip("₿itcoin Core monitor — orange ON · red OFF · green ring = connecting/feeding")
        self._core_base = "padding:1px 7px; border-radius:7px; font-weight:800; border:2px solid transparent;"
        self.core_lbl.setStyleSheet("color:#f85149; " + self._core_base)
        self.refresh_lbl = QtWidgets.QLabel("  ↻ —"); self.refresh_lbl.setStyleSheet("color:#0AC18E; " + CHIP); bar.addWidget(self.refresh_lbl)
        spacer = QtWidgets.QWidget(); spacer.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred); bar.addWidget(spacer)
        waas_btn = QtWidgets.QPushButton("+ Create Wallet (WaaS)"); waas_btn.setObjectName("waas")
        waas_btn.setToolTip(WAAS_URL); waas_btn.clicked.connect(lambda: webbrowser.open(WAAS_URL)); bar.addWidget(waas_btn)

        # bandwidth evidence chip: live in/out KB/s (comparison) + session totals since node
        # start (getnettotals) + uptime — sampled into the .history evidence trail (~1/min)
        self.net_lbl = QtWidgets.QLabel("  ⇅ —"); self.net_lbl.setStyleSheet("color:#8aa0b4; font-family:'DejaVu Sans Mono',monospace; " + CHIP)
        self.net_lbl.setToolTip("₿andwidth evidence — ▼ in / ▲ out live KB/s · Σ session totals since node start · uptime.\n"
                                "Sampled to ~/.bankon/.history (1 MB rotation) — delete/shred any time in 🧊 ICE.")
        bar.addWidget(self.net_lbl)
        self.zmq_lbl = QtWidgets.QLabel("  ⚡ zmq —"); self.zmq_lbl.setStyleSheet("color:#5a6b7b; " + CHIP)
        self.zmq_lbl.setToolTip("ZMQ push — real-time block events from bitcoind (no polling)")
        bar.addWidget(self.zmq_lbl)
        self.sys_lbl = QtWidgets.QLabel("  🖥 —"); self.sys_lbl.setStyleSheet("color:#8aa0b4; " + CHIP)
        self.sys_lbl.setToolTip("Host CPU usage · temperature · memory"); bar.addWidget(self.sys_lbl)
        bar.addWidget(QtWidgets.QLabel(" ⏸@"))
        self.pausetemp = QtWidgets.QSpinBox(); self.pausetemp.setRange(80, 110); self.pausetemp.setValue(99); self.pausetemp.setSuffix("°C")
        self.pausetemp.setToolTip("Thermal protection: auto-pause the pruned node at/above this temperature")
        bar.addWidget(self.pausetemp); self._thermal_paused = False
        # Two-way sync toolbar ↔ Control tab threshold (valueChanged doesn't fire on same-value
        # set, so this can't loop). ONE protection engine (_sys below); two views of its dial.
        self.pausetemp.valueChanged.connect(self.ctl.pausetemp.setValue)
        self.ctl.pausetemp.valueChanged.connect(self.pausetemp.setValue)

        # © rides the status bar as a PERMANENT widget — visible on every tab, not just Overview
        _cpr = QtWidgets.QLabel("© 2026 ₿ANKON — all rights preserved  ")
        _cpr.setStyleSheet("color:#5a6b7b;font-size:10px")
        self.statusBar().addPermanentWidget(_cpr)
        self.timer = QtCore.QTimer(); self.timer.timeout.connect(self.do_refresh)
        self.health = QtCore.QTimer(); self.health.timeout.connect(self.poll_health); self.health.start(12000)  # gentle
        self.systimer = QtCore.QTimer(); self.systimer.timeout.connect(self.poll_sys); self.systimer.start(5000); self.poll_sys()
        self._nt_prev = None; self._nt_hist_ts = 0.0; self._uptime_s = None
        self.nettimer = QtCore.QTimer(); self.nettimer.timeout.connect(self.poll_net); self.nettimer.start(5000); self.poll_net()
        self.coretimer = QtCore.QTimer(); self.coretimer.timeout.connect(self.poll_coremon); self.coretimer.start(5000); self.poll_coremon()
        self.logt = QtCore.QTimer(); self.logt.timeout.connect(lambda: self.node.load_log() if self.current() is self.node else None); self.logt.start(6000)
        # ZMQ push: refresh on each new block (event-driven) — the rate timer is now a fallback heartbeat.
        self.zmq = ZmqService(self)
        self.zmq.block.connect(self.on_zmq_block)
        self.zmq.status.connect(self.on_zmq_status)
        self.zmq.start()
        # Geo Map tab is PERSISTED (default ON) — restored last, once the whole toolbar
        # exists, so the insert's currentChanged→do_refresh finds every widget it touches
        if QtCore.QSettings("BANKON", "bankon-qt").value("tabs/geomap", "true") == "true":
            self.geo_chk.setChecked(True)              # builds + inserts the Geo Map tab now…
            self.tabs.setCurrentIndex(0)               # …while the session still opens on Overview
        # 🛠 ADMIN popup auto-opens when it was open last time (default ON — so it is SEEN,
        # docked beside the globe); deferred so the console is shown and placed first
        if QtCore.QSettings("BANKON", "bankon-qt").value("admin/open", "true") == "true":
            QtCore.QTimer.singleShot(700, self._show_admin)
        self.apply_rate(); self.poll_health(); self.do_refresh()
    def current(self): return self.tabs.currentWidget()
    def _save_tab_order(self, *_):
        QtCore.QSettings("BANKON", "bankon-qt").setValue(
            "tabs/order", [self.tabs.tabText(i) for i in range(self.tabs.count())])
    def _restore_tab_order(self):
        saved = QtCore.QSettings("BANKON", "bankon-qt").value("tabs/order") or []
        if isinstance(saved, str): saved = [saved]          # QSettings collapses 1-item lists
        bar, pos = self.tabs.tabBar(), 0
        for name in saved:                                  # stable re-order; unknown names skip,
            for i in range(pos, self.tabs.count()):         # new tabs keep their default place
                if self.tabs.tabText(i) == name:
                    if i != pos: bar.moveTab(i, pos)
                    pos += 1
                    break
    def _toggle_invert(self, on):
        # invert + blackICE are mutually exclusive themes; enabling one clears the other
        if on and self.blackice_chk.isChecked():
            self.blackice_chk.blockSignals(True); self.blackice_chk.setChecked(False); self.blackice_chk.blockSignals(False)
        apply_theme("invert" if on else "dark")
    def _toggle_blackice(self, on):
        if on and self.inv_chk.isChecked():
            self.inv_chk.blockSignals(True); self.inv_chk.setChecked(False); self.inv_chk.blockSignals(False)
        apply_theme("blackice" if on else "dark")
    def _style_spin_chk(self, on):
        # state is unmistakable at ALL times: candle green + ON while attached, red OFF otherwise
        self.spin_chk.setText(" ⟲ SPINTRADE ON" if on else " ⟲ SPINTRADE OFF")
        self.spin_chk.setStyleSheet("QCheckBox{color:%s;font-weight:800}" % ("#16C784" if on else "#f85149"))
    def _toggle_spintrade(self, on):
        from services import history_service as H
        if on:
            if self.spin is None:
                # the innerstand gate: the client consents BEFORE the module attaches
                m = QtWidgets.QMessageBox(self)
                m.setWindowTitle("Open SPINTRADE — innerstand first")
                m.setText("⟲ SPINTRADE\n\nThis software is used to swap cryptocurrency for other assets.")
                m.setInformativeText("What you will see is the ₿itcoin blockchain expressed as chain-native "
                                     "trading pairs, priced in SAT (including SATPAY — the live cost of an "
                                     "on-chain payment). No external feed, no fiat: the venue is the blockchain.\n\n"
                                     "SPINTRADE starts OFF every session, turns candle green while ON, suspends "
                                     "under the 🧊 ICE AIRGAP, and its attach/detach is recorded in the .history "
                                     "evidence trail.\n\nOpen SPINTRADE?")
                m.setStandardButtons(QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No)
                m.setDefaultButton(QtWidgets.QMessageBox.No)
                if m.exec() != QtWidgets.QMessageBox.Yes:
                    self.spin_chk.blockSignals(True); self.spin_chk.setChecked(False); self.spin_chk.blockSignals(False)
                    self._style_spin_chk(False)
                    return
                self.spin = SpintradeTab()
            i = self.tabs.indexOf(self.oracle) + 1
            self.tabs.insertTab(i, self.spin, "⟲ SPINTRADE")
            self.tabs.setCurrentWidget(self.spin)          # currentChanged → refresh
            H.append("spintrade", state="attached", consent="yes")
        elif self.spin is not None:
            i = self.tabs.indexOf(self.spin)
            if i != -1: self.tabs.removeTab(i)
            self.spin.deleteLater(); self.spin = None      # ABSOLUTE detach — nothing keeps running
            H.append("spintrade", state="detached")
        self._style_spin_chk(on)
    def _show_admin(self):
        st = QtCore.QSettings("BANKON", "bankon-qt")
        st.setValue("admin/open", "true")              # visibility is remembered (closes → false)
        if self.admin is None:
            self.admin = AdminWindow(self)
            g = st.value("admin/geometry")
            if g is not None:
                self.admin.restoreGeometry(g)          # memory: size + position, any display
        mode = st.value("admin/dock", "geo map")       # BIRTH dock: the geo map, until moved
        self.admin.show()
        if mode != AdminWindow.DOCK_FREE:
            self.admin._apply_dock(mode)               # relative docks re-park live
        self.admin.raise_(); self.admin.activateWindow()
    def _toggle_geo(self, on):
        # Build the Geo Map on enable (insert right after Net Map); destroy on disable so its
        # globe spin-timer + GeoIP work fully stop. The choice is REMEMBERED (default ON).
        QtCore.QSettings("BANKON", "bankon-qt").setValue("tabs/geomap", "true" if on else "false")
        if on:
            if self.geo is None: self.geo = GeoMapTab()
            i = self.tabs.indexOf(self.map) + 1
            self.tabs.insertTab(i, self.geo, "🌍 Geo Map")
            self.tabs.setCurrentWidget(self.geo)                  # currentChanged → refresh
        elif self.geo is not None:
            try: self.geo.close_aux_windows()   # watcher globes + fullscreen retract first
            except Exception: pass
            i = self.tabs.indexOf(self.geo)
            if i != -1: self.tabs.removeTab(i)
            self.geo.deleteLater(); self.geo = None
    def on_zmq_block(self, block_hash, seq):
        # push-driven refresh — a new block connected; update the active tab + stamp.
        self.zmq_lbl.setText(f"  ⚡ zmq ● block {block_hash[:10]}…"); self.zmq_lbl.setStyleSheet("color:#16C784; " + CHIP)
        self.do_refresh()
    def on_zmq_status(self, ok, msg):
        self.zmq_lbl.setText(f"  ⚡ zmq {'●' if ok else '○'} {msg}")
        self.zmq_lbl.setStyleSheet(("color:%s; " % ("#16C784" if ok else "#5a6b7b")) + CHIP)
    def closeEvent(self, e):
        # EXIT IS EXIT — BANKON closes quickly and cleanly. Stop every timer → no new work;
        # stop the ZMQ thread; join live workers; then scrub memory. No long blocking wipe here:
        # secure erase of .history is offered as a recommendation (below) and done via the ICE
        # button; exit stays fast.
        for name in ("timer", "health", "systimer", "coretimer", "logt", "nettimer"):
            try: getattr(self, name).stop()
            except Exception: pass
        try:                                  # the 🛠 ADMIN popup must not outlive the console
            if self.admin is not None: self.admin.hide()
        except Exception: pass
        for t in self.findChildren(QtCore.QTimer):    # child-widget timers (map pulse, globe spin, oracle throb)
            try: t.stop()
            except Exception: pass
        # Recommend wiping the public .history before leaving (it's a public record of connectivity).
        # One quick prompt — not a blocking wipe. Honors the ICE 'auto-wipe on exit' choice.
        try:
            from services import history_service as H
            if H.size_bytes() > 0:
                aw = getattr(self.ice, "autowipe", None)
                do_wipe = aw.isChecked() if aw is not None else False
                if not do_wipe:
                    r = QtWidgets.QMessageBox.question(
                        self, "Wipe before exit?",
                        "The local .history is a PUBLIC record of your connectivity.\n"
                        "Recommended: wipe it before exiting (mint/anchor first to keep a verifiable copy).\n\nWipe now?",
                        QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No, QtWidgets.QMessageBox.Yes)
                    do_wipe = (r == QtWidgets.QMessageBox.Yes)
                if do_wipe:
                    H.delete()               # fast unlink on exit; the button offers full 7-pass shred
        except Exception:
            pass
        try: self.zmq.stop()                 # joins the subscriber thread (≤2.5s)
        except Exception: pass
        try: shutdown_workers()              # wait out any in-flight RPC workers
        except Exception: pass
        # INSIST on clearing transient memory: RPC caches + any private-key/signature material
        # (Qt is non-custodial and holds none by design, but scrub regardless) + the cache dir.
        try: scrub_memory()
        except Exception: pass
        super().closeEvent(e)
    def do_refresh(self):
        # central refresh — drives every tab from the timer/button and stamps the time
        try: self.current().refresh()
        except Exception: pass
        rate = self.rate.currentText()
        self.refresh_lbl.setText("  ↻ " + QtCore.QTime.currentTime().toString("HH:mm:ss") + f" · every {rate}")
    def apply_rate(self):
        self.timer.stop(); ms = self.rate.currentData()
        if ms: self.timer.start(ms)
    def poll_health(self):
        if getattr(self, "_hb", False): return          # don't stack health probes
        self._hb = True
        def ok(b, s):
            self._hb = False
            self.status_lbl.setText(f"  ● node :8332 · block {b:,}" + (" (cached)" if s else "")); self.status_lbl.setStyleSheet("color:#0AC18E; " + CHIP)
            self.titlebar.set_diag(block=f"{b:,}")
        def bad(e):
            self._hb = False
            if "refus" in e.lower() or "connect" in e.lower():
                self.status_lbl.setText("  ● node stopped"); self.status_lbl.setStyleSheet("color:#f85149; " + CHIP)
            elif "-28" in e or "warm" in e.lower() or "load" in e.lower():
                self.status_lbl.setText("  ● node booting…"); self.status_lbl.setStyleSheet("color:#F7931A; " + CHIP)
            else:
                self.status_lbl.setText("  ● node validating…"); self.status_lbl.setStyleSheet("color:#F7931A; " + CHIP)
        spawn("getblockcount", ok, bad, timeout=6)
        # banner diagnostics: live peer count (cheap, warm-cached getnetworkinfo)
        def _bp(ni, _s):
            c, o, i = ni.get("connections"), ni.get("connections_out"), ni.get("connections_in")
            if c is not None:
                self.titlebar.set_diag(peers=f"{c} peers" + (f" ({o}↑ {i}↓)" if o is not None and i is not None else ""))
        spawn("getnetworkinfo", _bp, timeout=8)
    def poll_coremon(self):
        # Console down ≠ Core down: fall back to a direct loopback probe of :8332 so the
        # banner CORE control stays live-and-clickable even with no Console running
        def probe():
            try:
                return fetch_json("/api/coremon")
            except Exception:
                s = socket.socket(); s.settimeout(1.5)
                up = s.connect_ex(("127.0.0.1", 8332)) == 0
                s.close()
                return {"up": up, "direct": True}
        spawn_fn(probe, self._coremon)
    def _coremon(self, d):
        d = d or {}
        if not d.get("up"):
            self.core_lbl.setText(" ● CORE OFF"); self.core_lbl.setStyleSheet("color:#f85149; " + self._core_base)
            tip = "₿itcoin Core not reachable on :" + str(d.get("port", "8332"))
            self.core_lbl.setToolTip(tip)
            self.titlebar.set_core(False, False, tip)
        elif d.get("feeding"):
            self.core_lbl.setText(" ● CORE ON")   # orange ON + green surround = feeding from connect
            self.core_lbl.setStyleSheet("color:#F7931A; padding:1px 7px; border-radius:7px; font-weight:800; border:2px solid #16C784;")
            tip = f"₿itcoin Core ON · feeding from connect (block {d.get('height')}, tip {d.get('logAgeSec')}s ago)"
            self.core_lbl.setToolTip(tip)
            self.titlebar.set_core(True, True, tip)
        else:
            self.core_lbl.setText(" ● CORE ON"); self.core_lbl.setStyleSheet("color:#F7931A; " + self._core_base)
            tip = f"₿itcoin Core ON (block {d.get('height') or '?'})"
            self.core_lbl.setToolTip(tip)
            self.titlebar.set_core(True, False, tip)
    def _dock_banner(self, where, save=True):
        """Reinsert the banner at the top (above tabs) or bottom (below tabs) of the window."""
        cl = self._central_lay
        cl.removeWidget(self.titlebar)
        cl.insertWidget(0 if where == "top" else cl.count(), self.titlebar)
        if save:
            QtCore.QSettings("BANKON", "bankon-qt").setValue("banner/dock", where)
            self.statusBar().showMessage(f"banner docked {where}", 4000)
    def _show_peer_on_map(self, addr):
        self.tabs.setCurrentWidget(self.map)
        self.map.select_addr(addr)
    def poll_net(self):
        spawn("getnettotals", self._nettotals, timeout=8)
        if self._uptime_s is None or int(time.time()) % 60 < 5:
            spawn("uptime", lambda u, s: setattr(self, "_uptime_s", u), timeout=6)
    @staticmethod
    def _gb(n): return f"{n/1073741824:.2f} GiB" if n >= 1073741824 else f"{n/1048576:.0f} MiB"
    def _nettotals(self, nt, stale):
        rin, rout = nt.get("totalbytesrecv", 0), nt.get("totalbytessent", 0)
        tms = nt.get("timemillis", 0)
        kin = kout = None
        if self._nt_prev:
            p_in, p_out, p_t = self._nt_prev
            dt = max(0.001, (tms - p_t) / 1000.0)
            if tms > p_t and rin >= p_in:                      # node restart resets totals — skip that delta
                kin, kout = (rin - p_in) / dt / 1024.0, (rout - p_out) / dt / 1024.0
        self._nt_prev = (rin, rout, tms)
        up = self._uptime_s
        uptxt = (f"up {up//3600}h{(up%3600)//60:02d}" if up and up >= 3600 else (f"up {up//60}m" if up else ""))
        live = (f"▼{kin:,.1f} ▲{kout:,.1f} KB/s · " if kin is not None else "")
        self.net_lbl.setText(f"  ⇅ {live}Σ {self._gb(rin)}/{self._gb(rout)}" + (f" · {uptxt}" if uptxt else "")
                             + (" (cached)" if stale else ""))
        self.titlebar.set_diag(net=(f"⇅ ▼{kin:,.1f} ▲{kout:,.1f} KB/s" if kin is not None else f"Σ {self._gb(rin)}/{self._gb(rout)}"))
        self.net_lbl.setStyleSheet(("color:%s; font-family:'DejaVu Sans Mono',monospace; " % ("#16C784" if (kin or 0) + (kout or 0) > 0.5 else "#8aa0b4")) + CHIP)
        # evidence trail: one bandwidth sample a minute into .history (self-pruning, shreddable)
        if time.time() - self._nt_hist_ts > 60 and kin is not None:
            self._nt_hist_ts = time.time()
            from services import history_service as H
            H.append("net", inKBs=round(kin, 2), outKBs=round(kout, 2),
                     totalInB=rin, totalOutB=rout, uptimeS=up)
            spawn_fn(lambda: fetch_json("/api/netactivity?n=30"), self._hist_events)
    def _hist_events(self, d):
        """Connect/disconnect/inbound events from the node log → .history (deduped)."""
        from services import history_service as H
        seen = getattr(self, "_ev_seen", None)
        if seen is None: seen = self._ev_seen = set()
        for e in (d or {}).get("events", []):
            k = (e.get("time"), e.get("kind"), e.get("addr") or e.get("peer"))
            if k in seen or e.get("kind") not in ("connected", "disconnect", "failed", "inbound"): continue
            seen.add(k)
            H.append(e.get("kind"), addr=e.get("addr") or "", peer=e.get("peer") or "",
                     at=e.get("time") or "", text=(e.get("text") or "").strip()[:90])
        if len(seen) > 4000: self._ev_seen = set(list(seen)[-2000:])
    def poll_sys(self):
        # Console down ≠ blind: fall back to the same /sys thermal zones ICE reads, so the
        # banner's 🌡 diagnostic stays live without any Console
        def _local_temp(_e):
            t = self.ice._cpu_temp()
            if t is not None: self.titlebar.set_diag(temp=f"{t:.0f}°C")
        spawn_fn(lambda: fetch_json("/api/system"), self._sys, _local_temp)
    def _sys(self, d):
        if not d or not d.get("ok"): return
        cpu, t, mem = d.get("cpuPct"), d.get("tempC"), d.get("memUsedPct")
        # temperature severity (your calibration): comfortable working zone = ₿itcoin orange (fine at
        # 92°C); 96°C = concern; 99°C+ = DANGEROUS red. Cool/idle stays green.
        col, sev, weight = "#16C784", "", "normal"
        if t is not None:
            if t >= 99:   col, sev, weight = "#ff2b2b", "  ⚠ DANGEROUS", "bold"   # RED
            elif t >= 96: col, sev          = "#FF5E3A", "  concern"               # red-orange
            elif t >= 85: col, sev          = "#F7931A", "  HOT"                   # ₿itcoin orange (comfortable working @92)
        parts = [f"🖥 cpu {cpu}%"]
        if t is not None: parts.append(f"🌡 {t}°C{sev}")
        if mem is not None: parts.append(f"mem {mem}%")
        self.sys_lbl.setText("  " + " · ".join(parts))
        self.sys_lbl.setStyleSheet(f"color:{col}; font-weight:{weight}; " + CHIP)
        if t is not None: self.titlebar.set_diag(temp=f"{t:.0f}°C")
        # thermal protection: at/above the chosen temp, pause the pruned node to cool down
        thr = self.pausetemp.value()
        if t is not None and t >= thr and not self._thermal_paused:
            self._thermal_paused = True; self._pause_pruned(t, thr)
        elif t is not None and t < thr - 3:
            self._thermal_paused = False                  # hysteresis: re-arm once it cools 3°C
        self.ctl.update_sys(d, self._thermal_paused)      # feed the Control tab (single poller)
    def _pause_pruned(self, t, thr):
        dd = os.environ.get("BANKON_PRUNED_DATADIR", str(Path.home() / ".bitcoin-pruned"))
        cli = str(Path(BTC_BIN) / "bitcoin-cli")
        try:
            subprocess.Popen([cli, f"-datadir={dd}", "-rpcport=8342", "stop"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            msg = f"🌡 {t}°C ≥ {thr}°C — thermal protection: PAUSED the pruned node to cool down"
        except Exception as e:
            msg = f"thermal pause failed: {e}"
        self.statusBar().showMessage(msg, 15000); print(msg)


# Multi-chain accent palette (matches the web UIs): ₿itcoin orange (primary),
# Polygon purple, Ethereum blue, Cash green (success), Cardano blue (hover), Solana royal.
QSS = """
  /* typography root: DejaVu everywhere (verified installed; no Inter/JetBrains on host),
     Noto Sans CJK as the wide-glyph fallback. Monospace surfaces name their stack below. */
  * { font-family:"DejaVu Sans","Noto Sans CJK SC","FreeSans",sans-serif; }
  QMainWindow { background:#06090e; }
  /* ₿ANKON corporate blue-grey title banner (hint of blue) — now a draggable QFrame
     carrying the title text + the CORE dynamic control */
  QFrame#titlebar {
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #3b4b5d, stop:1 #28384a);
    border:1px solid #2e4a63; border-bottom:2px solid #00BFFF; border-radius:8px;
  }
  QLabel#titletext { background:transparent; border:0; color:#e8eef5;
    font-size:15px; font-weight:800; letter-spacing:2px; padding:4px; }
  /* ₿TC.oracle — enhanced ₿itcoin-orange outline */
  QFrame#oracleframe { border:2px solid #F7931A; border-radius:12px; background:#06090e; }
  /* 🔬 ₿lock science quadrant — electric-blue outline (the oracle's accuracy panel) */
  QFrame#scienceframe { border:2px solid #00BFFF; border-radius:12px; background:#06090e; }
  QLabel#oracletitle { color:#F7931A; font-size:16px; font-weight:800; letter-spacing:1px; padding:6px;
    border-bottom:1px solid #5a3a0a; }
  /* rageRPC controller */
  QFrame#ragebox { border:1px solid #2e4a63; border-radius:6px; background:#0e1620; }
  QWidget, QTabWidget::pane { background:#0b0f15; color:#eef3f8; }
  QTabWidget::pane { border:1px solid #0e3d57; border-radius:8px; }
  QLabel { color:#eef3f8; }
  QPushButton { background:qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #FFC06B, stop:1 #E6850A);
    color:#1a1200; border:2px solid #7a4806; border-radius:8px; padding:5px 13px; font-weight:700; }
  QPushButton:hover { background:qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #38b6ff, stop:1 #0072d6); color:#fff; border:2px solid #00BFFF; }
  QPushButton:pressed { background:#c77512; border:2px solid #00BFFF; }
  QPushButton:disabled { background:#1b2230; color:#5b6470; border:2px solid #2a3340; }
  QPushButton#danger { background:qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #d6464d, stop:1 #9e1f26); border:2px solid #5c1216; color:#fff; }
  QPushButton#danger:hover { background:qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #38b6ff, stop:1 #0072d6); border:2px solid #00BFFF; }
  QPushButton#good { background:qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #2bd6a6, stop:1 #07a06f); border:2px solid #064f38; color:#03120d; font-weight:700; }
  QPushButton#good:hover { background:qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #FFD37A, stop:1 #E6850A); border:2px solid #00BFFF; }
  /* WaaS button — ₿itcoin ORANGE background AND orange highlights (hover brightens, never re-hues) */
  QPushButton#waas { background:qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #FFC06B, stop:1 #F7931A); color:#1a1200; border:2px solid #7a4806; font-weight:700; }
  QPushButton#waas:hover { background:qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #FFD9A0, stop:1 #F7931A); color:#1a1200; border:2px solid #FFB74D; }
  QPushButton#waas:pressed { background:#E6850A; border:2px solid #FFD9A0; }
  /* secondary — Polygon purple (chain-accent token) */
  QPushButton#secondary { background:qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #9F6BFF, stop:1 #6C2BD9); color:#fff; border:2px solid #3a1c78; font-weight:700; }
  QPushButton#secondary:hover { background:qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #B98BFF, stop:1 #8247E5); border:2px solid #C4A2FF; }
  QTabBar::tab { background:#10161f; color:#8aa0b4; padding:7px 16px; letter-spacing:0.5px;
    border:1px solid #0e2738; border-bottom:0; border-top-left-radius:5px; border-top-right-radius:5px; margin-right:1px; }
  QTabBar::tab:selected { color:#eef3f8; font-weight:600;
    background:qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #1a2c3c, stop:1 #13202c);
    border-bottom:2px solid #00BFFF; }
  QTabBar::tab:hover { color:#7DF9FF; background:#14202d; }
  /* named monospace stack for every inline font-family:monospace site (inheritance) */
  QPlainTextEdit, QTableWidget, QTreeWidget { font-family:"DejaVu Sans Mono","Liberation Mono",monospace; }
  QProgressBar { border:1px solid #0e3d57; border-radius:6px; text-align:center; background:#070d14; color:#eef3f8; }
  QProgressBar::chunk { background:qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #F7931A, stop:1 #00BFFF); border-radius:5px; }
  QComboBox, QLineEdit { background:#0a121b; border:1px solid #14405c; border-radius:6px; padding:4px 7px; color:#eef3f8; selection-background-color:#00BFFF; selection-color:#001018; }
  QComboBox:focus, QLineEdit:focus { border:1px solid #00BFFF; }
  QPlainTextEdit, QTableWidget { background:#05080d; color:#d6e3ef; border:1px solid #14405c; alternate-background-color:#0a131c; gridline-color:#13314a; selection-background-color:#00BFFF; selection-color:#001018; }
  QTableWidget::item { padding:4px 10px; }
  QTableWidget::item:selected { background:#00BFFF; color:#001018; }
  QHeaderView::section { background:#10202e; color:#8aa0b4; border:0; border-right:2px solid #2a6a90; border-bottom:1px solid #14405c; padding:4px; }
  QHeaderView::section:last { border-right:0; }
  QToolBar { background:#0c1722; border-bottom:2px solid #00BFFF; spacing:4px; }
  QStatusBar { color:#8aa0b4; }
  QLabel a { color:#0AC18E; }
  QScrollBar:vertical { background:#0a121b; width:11px; } QScrollBar::handle:vertical { background:#14405c; border-radius:5px; }
  QSplitter::handle { background:#0e3d57; border-radius:3px; } QSplitter::handle:hover { background:#00BFFF; }
  QHeaderView::section:hover { color:#7DF9FF; border-right:2px solid #00BFFF; }   /* grip lights up = drag here to resize */
"""

def invert_qss(qss):
    """POLARITY INVERSION ('reverse video') — derive the light theme by inverting every #RRGGBB in
    the stylesheet. One palette, zero maintenance: the dark QSS is the single source of truth and
    the inverse is computed, never hand-edited. Semantic per-widget colors (severity green/orange/
    red, sync gradient) are applied at runtime outside this sheet and deliberately KEEP their
    meaning under inversion. Full write-up: docs/design.md → 'Polarity inversion'."""
    def inv(m):
        h = m.group(1)
        return "#%02x%02x%02x" % tuple(255 - int(h[i:i+2], 16) for i in (0, 2, 4))
    return re.sub(r"#([0-9a-fA-F]{6})\b", inv, qss)

QSS_INVERTED = None   # computed lazily on first toggle (startup stays instant)

# 🖤 blackICE theme — the "black ICE" look: TRANSPARENT fields, BLACK font, and a thin yet
# prominent BLACK outline on every field. Paper-light translucent ground so black text reads;
# window opacity carries the "transparent" feel under software rendering (no GL translucency).
BLACKICE_QSS = """
  * { background: transparent; color: #000000;
      font-family:"DejaVu Sans","Noto Sans CJK SC","FreeSans",sans-serif; }
  QMainWindow, QWidget, QTabWidget::pane, QScrollArea, QStackedWidget { background: #f4f4ef; }
  QTabWidget::pane { border: 1px solid #000000; border-radius: 4px; }
  /* every FIELD: transparent fill, thin prominent black outline */
  QFrame, QLineEdit, QComboBox, QAbstractSpinBox, QPlainTextEdit, QTextEdit,
  QTableWidget, QTreeWidget, QListWidget, QGroupBox, QProgressBar,
  QLabel#titlebar, QFrame#titlebar, QFrame#oracleframe, QFrame#scienceframe, QFrame#ragebox {
      background: transparent; color: #000000;
      border: 1px solid #000000; border-radius: 4px; }
  QLabel { background: transparent; color: #000000; border: 0; }
  QLabel#titletext, QLabel#oracletitle { color: #000000; font-weight: 800; }
  QPushButton { background: transparent; color: #000000; border: 1px solid #000000;
      border-radius: 6px; padding: 4px 12px; font-weight: 700; }
  QPushButton:hover { background: rgba(0,0,0,0.06); border: 2px solid #000000; }
  QPushButton:disabled { color: #6a6a6a; border: 1px solid #9a9a9a; }
  QTabBar::tab { background: transparent; color: #000000; border: 1px solid #000000;
      border-bottom: 0; padding: 6px 14px; margin-right: 1px; }
  QTabBar::tab:selected { border-bottom: 2px solid #000000; font-weight: 800; }
  QHeaderView::section { background: transparent; color: #000000; border: 1px solid #000000; }
  QProgressBar::chunk { background: rgba(0,0,0,0.18); }
  QToolBar { background: transparent; border-bottom: 2px solid #000000; }
  QCheckBox, QRadioButton { color: #000000; background: transparent; }
  QToolTip { color:#000; background:#f4f4ef; border:1px solid #000; }
"""


def apply_theme(mode):
    """One switch for the whole window: 'dark' (default) · 'invert' · 'blackice'."""
    global QSS_INVERTED
    app = QtWidgets.QApplication.instance()
    win = app.activeWindow() or (app.topLevelWidgets()[0] if app.topLevelWidgets() else None)
    if mode == "blackice":
        app.setStyleSheet(QSS + BLACKICE_QSS)
        if win: win.setWindowOpacity(0.92)          # the "transparent" feel, software-safe
    else:
        if mode == "invert":
            if QSS_INVERTED is None: QSS_INVERTED = invert_qss(QSS)
            app.setStyleSheet(QSS_INVERTED)
        else:
            app.setStyleSheet(QSS)
        if win: win.setWindowOpacity(1.0)

if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv); app.setStyle("Fusion")
    app.setFont(QtGui.QFont("DejaVu Sans", 10))          # crisp verified-installed face (no Inter/JetBrains on host)
    app.setStyleSheet(QSS)
    app.aboutToQuit.connect(shutdown_workers)   # wait for live threads → clean exit
    w = Main(); w.show(); sys.exit(app.exec())
