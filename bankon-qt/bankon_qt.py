#!/usr/bin/env python3
"""
BANKON Qt — native diagnostics & node-control UI for Bitcoin Core (PySide6).

Parity with the web Console: live tabs (Overview / Node / Network / Mempool /
Blocks / Indexes / RPC Console), a variable refresh rate (1-min default), node
recognition + Start/Stop, a live debug.log bootup stream, and last-known caching
so tabs keep showing data while the node is lock-bound during IBD.

Launch via bankon-qt.sh (installs PySide6, forces software rendering for HD 3000).
"""
import json, math, os, re, socket, subprocess, sys, time, urllib.request, webbrowser
from datetime import datetime, timezone
from pathlib import Path

try:
    from PySide6 import QtWidgets, QtCore, QtGui
except ImportError:
    sys.exit("PySide6 not installed. Run: pip install --user pyside6  (or use bankon-qt.sh)")

# Data plumbing lives in the service layer (shared with adapters/, no circular import).
from services.rpc_service import (RPC_URL, COOKIE, CONSOLE_URL, rpc, rpc_cached,
                                  synctip, fetch_json, post_json, flag)
from services.zmq_service import ZmqService

BTC_BIN  = os.environ.get("BANKON_BTC_BIN", str(Path.home() / "bitcoin-31.0" / "bin"))
DATADIR  = os.environ.get("BANKON_BTC_DATADIR", str(Path.home() / ".bitcoin"))
DEBUG_LOG = Path(DATADIR) / "debug.log"
WAAS_URL = os.environ.get("BANKON_WAAS_URL", "http://127.0.0.1:8088")

# GeoIP + map geometry + node-native network view live in the service layer.
from services.geoip_service import geolocate, asn as asn_lookup, WORLD, HAVE_GEOIP, HAVE_ASN
from services.geodesy import (great_circle_points, azimuthal_equidistant, nearest_city,
                              densify_latlon, azimuthal_equidistant_hp, format_hp)
from services.network_view import known_nodes, network_asof
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


def anim_on(w):
    """True only when animating `w` can actually be seen. THERMAL: every animation tick must gate
    on this — under software rendering (HD 3000) a hidden/minimized 20-25 fps repaint is pure CPU
    heat. isVisible() alone is not enough: Qt keeps it True while the window is minimized."""
    try:
        return w.isVisible() and not (w.window().windowState() & QtCore.Qt.WindowMinimized)
    except Exception:
        return w.isVisible()


CANDLE = "#16C784"
def sync_color(p):
    # <51% dark; 51%→99% dark green → lighter; ≥99% candle green (held to 100%)
    if p >= 99: return CANDLE
    if p < 51: return "#0a3d27"
    t = (p - 51) / 48.0
    L = lambda a, b: int(a + (b - a) * t)
    return f"rgb({L(11,22)},{L(93,199)},{L(52,132)})"

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


class OverviewTab(QtWidgets.QWidget):
    def __init__(self):
        super().__init__(); v = QtWidgets.QVBoxLayout(self)
        self.bar = QtWidgets.QProgressBar(); self.bar.setMaximum(100000)
        v.addWidget(QtWidgets.QLabel("<b>Sync</b>")); v.addWidget(self.bar)
        box, self.f = cardgrid(["chain", "height", "headers", "verify %", "peers", "mempool txs",
                                "size on disk", "IBD", "CPU %", "memory %", "load / temp"])
        v.addWidget(box)
        # --- Datadir diagnostic: the disk BANKON is attached to (works even when the node is down) ---
        fs = QtWidgets.QFrame(); fs.setStyleSheet("QFrame{border:1px solid #0e3d57;border-radius:6px}")
        fl = QtWidgets.QVBoxLayout(fs)
        hh = QtWidgets.QHBoxLayout()
        fh = QtWidgets.QLabel("💾 Datadir — the disk BANKON is attached to"); fh.setStyleSheet("color:#F7931A;font-weight:700;border:0")
        hh.addWidget(fh, 1)
        self.fsopen = QtWidgets.QPushButton("Open folder"); self.fsopen.setObjectName("secondary")
        self.fsopen.setToolTip("Open the datadir in the file manager"); self.fsopen.clicked.connect(self._open_datadir); hh.addWidget(self.fsopen)
        fl.addLayout(hh)
        self.fspath = QtWidgets.QLabel("path: …"); self.fspath.setStyleSheet("color:#8aa0b4;font-family:monospace;font-size:10px;border:0")
        self.fspath.setWordWrap(True); self.fspath.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse); fl.addWidget(self.fspath)
        self.fsbar = QtWidgets.QProgressBar(); self.fsbar.setMaximum(1000); self.fsbar.setFormat("disk …"); fl.addWidget(self.fsbar)
        self.fscomp = QtWidgets.QLabel("measuring…"); self.fscomp.setStyleSheet("color:#c9d4e0;font-family:monospace;font-size:11px;border:0")
        self.fscomp.setWordWrap(True); fl.addWidget(self.fscomp)
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
        self.launchbtn.setToolTip("Open the BANKON launcher (start/stop Core + BANKON, live logs, ICE)")
        self.launchbtn.clicked.connect(self._open_launcher); foot.addWidget(self.launchbtn)
        foot.addStretch(1)
        cpr = QtWidgets.QLabel("© 2026 BANKON — all rights preserved")
        cpr.setStyleSheet("color:#5a6b7b;font-size:10px"); foot.addWidget(cpr)
        v.addLayout(foot); v.addStretch()
        # near-realtime sync: /api/synctip is a cheap debug.log tail (no node RPC), so poll it
        # every 3s while this tab is visible — the % ticks up as the node validates blocks.
        self._synctimer = QtCore.QTimer(self); self._synctimer.timeout.connect(self._tick_sync); self._synctimer.start(3000)
        self._fstimer = QtCore.QTimer(self); self._fstimer.timeout.connect(self._tick_fs); self._fstimer.start(10000)
        QtCore.QTimer.singleShot(500, self._tick_fs)
    def _tick_sync(self):
        if self.isVisible():
            spawn_fn(synctip, self._sync)
    def refresh(self):
        spawn("getblockchaininfo", self._c)
        spawn("getnetworkinfo", self._n)
        spawn("getmempoolinfo", self._m)
        spawn_fn(synctip, self._sync)            # live sync from debug.log (accurate)
    def _sync(self, st):
        p = st.get("progress")
        if p is None: return
        pct = p * 100
        self.bar.setMaximum(100000000); self.bar.setValue(int(pct * 1000000)); self.bar.setFormat(f"{pct:.6f}%")
        self.bar.setStyleSheet("QProgressBar{border:1px solid #0e3d57;border-radius:6px;text-align:center;"
                               "background:#070d14;color:#eef3f8;} QProgressBar::chunk{background:%s;border-radius:5px;}" % sync_color(pct))
        self.f["verify %"].setText(f"{pct:.8f}")
        if st.get("height"): self.f["height"].setText(f"{st['height']:,}")
    def _c(self, c, stale):
        pct = (c.get("verificationprogress", 0) or 0) * 100
        self.bar.setMaximum(100000000)                       # high-precision gauge
        self.bar.setValue(int(pct * 1000000))
        self.bar.setFormat(f"{pct:.6f}%{' (cached)' if stale else ''}")
        col = sync_color(pct)
        self.bar.setStyleSheet("QProgressBar{border:1px solid #0e3d57;border-radius:6px;text-align:center;"
                               "background:#070d14;color:#eef3f8;} QProgressBar::chunk{background:%s;border-radius:5px;}" % col)
        synced = (not c.get("initialblockdownload")) and pct >= 99.99
        self.f["chain"].setText(str(c.get("chain"))); self.f["height"].setText(f"{c.get('blocks',0):,}")
        self.f["headers"].setText(f"{c.get('headers',0):,}"); self.f["verify %"].setText(f"{pct:.8f}")
        self.f["size on disk"].setText(f"{c.get('size_on_disk',0)/1073741824:.1f} GB")
        self.f["IBD"].setText("● FULL NODE" if synced else "IBD (syncing)")
    def _n(self, n, stale): self.f["peers"].setText(str(n.get("connections", "—")))
    def _m(self, m, stale): self.f["mempool txs"].setText(f"{m.get('size',0):,}")
    # ---- datadir diagnostic (external disk BANKON is attached to; works with node down) ----
    def _tick_fs(self):
        if self.isVisible():
            spawn_fn(lambda: fetch_json("/api/filesystem?files=1"), self._fill_fs)
            spawn_fn(lambda: fetch_json("/api/system"), self._fill_sys)
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
                                 + ("  ⚠ FULL — Bitcoin Core can't write" if full else (" — low" if low else "")))
        c = d.get("components") or {}
        self.fscomp.setText(f"blocks {self._gib(c.get('blocks'))}  ·  indexes {self._gib(c.get('indexes'))}  ·  "
                            f"chainstate {self._gib(c.get('chainstate'))}  ·  total on device {self._gib(c.get('total'))}")
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
        if QtWidgets.QMessageBox.question(self, "Stop", "Stop Bitcoin Core?") != QtWidgets.QMessageBox.Yes: return
        try:
            r = subprocess.run([str(Path(BTC_BIN)/"bitcoin-cli"), f"-datadir={DATADIR}", "stop"],
                               capture_output=True, text=True, timeout=15)
            self.msg.setText(r.stdout.strip() or r.stderr.strip() or "stopping")
        except Exception as e: self.msg.setText(f"stop failed: {e}")


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
        # stay current to Bitcoin Core: poll the accumulating feed every 5s while visible
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
        d = QtWidgets.QDialog(self); d.setWindowTitle(f"Block {b.get('height','')}"); d.resize(640, 470)
        dl = QtWidgets.QVBoxLayout(d)
        te = QtWidgets.QPlainTextEdit(); te.setReadOnly(True); te.setPlainText("\n".join(lines))
        te.setStyleSheet("font-family:monospace;font-size:12px;background:#070d14;color:#d6e3ef")
        dl.addWidget(te)
        cl = QtWidgets.QPushButton("Close"); cl.clicked.connect(d.accept); dl.addWidget(cl)
        self.lbl.setText("Latest blocks — double-click a block for full detail")
        d.exec()


class IndexesTab(QtWidgets.QWidget):
    """Live index view — every index (txindex, coinstatsindex, blockfilter…) advancing toward the
    chain tip, updated in near-realtime like the Blocks feed. Tip comes from the cheap log-based
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
        "txindex": "Look up ANY transaction by txid (getrawtransaction) — required for BANKON wallet/tx lookups.",
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
            self.activity.setText(f"activity:  ▲ {self._rate:.1f} blk/min  ·  +{sess:,} indexed since open  ·  last advance {ago}s ago")
            prog = st.get("progress"); bd = (st.get("blockDate") or "—").replace("T", " ").replace("Z", "")
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
        "getblock": "Block by hash.  params: [hash, verbosity 0|1|2]",
        "getblockstats": "Per-block stats (fees, size, txs).  params: [height | hash]",
        "getblockhash": "Block hash at a height.  params: [height]",
        "getchaintxstats": "Tx count & rate over a window.  params: [nblocks]",
        "getmempoolinfo": "Mempool size, bytes, usage, min relay fee.",
        "getrawmempool": "Mempool txids.  params: [verbose true|false]",
        "getpeerinfo": "Connected peers — addr, subver, ping, height, direction.",
        "getnetworkinfo": "Version, connection count, relay fee, reachable networks.",
        "getindexinfo": "Index status (txindex, coinstatsindex, blockfilter…).",
        "getmininginfo": "Difficulty, network hashrate, mempool size.",
        "estimatesmartfee": "Fee estimate (BTC/kvB).  params: [conf_target]",
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
        ("starting ht",     p.get("startingheight", "—")),
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
    f["min relay fee"].setText(f"{d.get('minrelaytxfee','—')} BTC/kvB")
    f["mempool min fee"].setText(f"{d.get('mempoolminfee','—')} BTC/kvB")
    tf = d.get('total_fee'); f["total fee"].setText(f"{tf} BTC" if tf is not None else "—")
    f["unbroadcast"].setText(f"{d.get('unbroadcastcount',0):,}")
    f["RBF / loaded"].setText(f"fullrbf={d.get('fullrbf','?')} · loaded={d.get('loaded','?')}")

def idx_fill(f, m, d, stale):
    if "txindex" in (d or {}):
        e = d["txindex"]; f["txindex synced"].setText(str(e.get("synced")))
        f["txindex height"].setText(f"{e.get('best_block_height',0):,}")
    else:
        f["txindex synced"].setText("— (pruned/none)")


class NetworkMapTab(QtWidgets.QWidget):
    """EtherApe-style live topology from ONE Bitcoin Core node: our node at centre,
    connected peers radial (link width + node size + colour = traffic, inbound vs
    outbound tinted), and a faint outer cloud of ALL nodes our addrman knows.

    DYNAMIC: a pulse timer animates traffic 'packets' flowing along each link and a
    selection halo, so the map is visibly live. CLICK any peer node → a diagnostics
    panel shows that peer's full getpeerinfo + Promote (favourite) / Boot actions."""
    def __init__(self):
        super().__init__(); v = QtWidgets.QVBoxLayout(self)
        top = QtWidgets.QHBoxLayout()
        self.info = QtWidgets.QLabel("Network map — your node (centre), connected peers, and the known-node cloud")
        self.info.setStyleSheet("color:#8aa0b4"); top.addWidget(self.info, 1)
        self.speed = QtWidgets.QLabel(""); self.speed.setStyleSheet("font-family:monospace;font-weight:700")
        self.speed.setToolTip("Live node throughput (getnettotals) — orange = data in, green = data out")
        top.addWidget(self.speed)
        top.addWidget(QtWidgets.QLabel("max nodes"))
        self.maxnodes = QtWidgets.QSpinBox(); self.maxnodes.setRange(0, 50000); self.maxnodes.setSingleStep(500)
        self.maxnodes.setValue(5000); self.maxnodes.setToolTip("Max known nodes to fetch/draw (+/- or type)")
        self.maxnodes.valueChanged.connect(lambda _: self.refresh()); top.addWidget(self.maxnodes)
        v.addLayout(top)
        split = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        self.scene = QtWidgets.QGraphicsScene()
        self.view = QtWidgets.QGraphicsView(self.scene)
        self.view.setRenderHint(QtGui.QPainter.Antialiasing)
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
        # map into a call-to-action: start Core + read the BANKON FAQ/docs. Shown over the view; hidden
        # the moment peers/activity appear.
        self.downpanel = QtWidgets.QFrame(self.view)
        self.downpanel.setStyleSheet("QFrame{background:rgba(8,16,26,0.95);border:2px solid #F7931A;border-radius:12px}")
        dp = QtWidgets.QVBoxLayout(self.downpanel); dp.setSpacing(8); dp.setContentsMargins(24, 18, 24, 18)
        _h = QtWidgets.QLabel("₿  the wallet you can BANKON"); _h.setAlignment(QtCore.Qt.AlignCenter)
        _h.setStyleSheet("color:#F7931A;font-weight:800;font-size:16px;border:0"); dp.addWidget(_h)
        self.downmsg = QtWidgets.QLabel("Bitcoin Core is not running — BANKON attaches to your node.\n"
                                        "Start Core to map the network.")
        self.downmsg.setAlignment(QtCore.Qt.AlignCenter); self.downmsg.setWordWrap(True)
        self.downmsg.setStyleSheet("color:#d6e3ef;border:0"); dp.addWidget(self.downmsg)
        _sb = QtWidgets.QPushButton("▶  Start Bitcoin Core")
        _sb.setStyleSheet("QPushButton{background:#17a24b;color:#eafff0;font-weight:800;border:2px solid #0b5d34;"
                          "border-radius:8px;padding:8px}QPushButton:hover{background:#1fc75e}")
        _sb.clicked.connect(self._start_core); dp.addWidget(_sb)
        _lk = QtWidgets.QHBoxLayout()
        for _txt, _key in [("❓ FAQ", "faq"), ("📖 Docs", "docs")]:
            _b = QtWidgets.QPushButton(_txt); _b.setObjectName("secondary")
            _b.clicked.connect(lambda _c, k=_key: self._open_doc(k)); _lk.addWidget(_b)
        dp.addLayout(_lk)
        _lk2 = QtWidgets.QHBoxLayout()
        for _txt, _key in [("₿ BTC Standard ↗", "cypherpunk"), ("🎛 gnuGUI ↗", "gnugui")]:
            _b = QtWidgets.QPushButton(_txt); _b.setObjectName("secondary")
            _b.clicked.connect(lambda _c, k=_key: self._open_doc(k)); _lk2.addWidget(_b)
        dp.addLayout(_lk2)
        _rd = QtWidgets.QLabel("New here? Read the BANKON FAQ & docs while Core starts.")
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
        self.btn_boot = QtWidgets.QPushButton("⏏ Boot"); self.btn_boot.setObjectName("danger")
        self.btn_boot.setToolTip("Disconnect this peer now (disconnectnode)")
        self.btn_boot.clicked.connect(lambda: self._act_peer("boot"))
        self.btn_ban = QtWidgets.QPushButton("🚫 Ban"); self.btn_ban.setObjectName("danger")
        self.btn_ban.setToolTip("Blacklist as unreliable — setban 7 days + disconnect")
        self.btn_ban.clicked.connect(lambda: self._list_ban(self._sel.get("addr") if self._sel else "", True))
        br.addWidget(self.btn_promote); br.addWidget(self.btn_boot); br.addWidget(self.btn_ban); d.addLayout(br)
        self.diag_status = QtWidgets.QLabel(""); self.diag_status.setStyleSheet("color:#8aa0b4"); self.diag_status.setWordWrap(True)
        d.addWidget(self.diag_status)
        self.btn_promote.setEnabled(False); self.btn_boot.setEnabled(False); self.btn_ban.setEnabled(False)
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
                             ("🚫", lambda: self._list_ban(self._edit_addr(), True), "Blacklist: ban as unreliable (7 days)"),
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
                self._clear_form(); self.btn_promote.setEnabled(False); self.btn_boot.setEnabled(False); self.btn_ban.setEnabled(False); self.diag_status.setText("")
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
    def _select(self, p):
        self._sel = p
        self.diag_title.setText("◎ " + (p.get("addr", "—")))
        self._fill_diag(p)
        self.btn_promote.setEnabled(True); self.btn_boot.setEnabled(True); self.btn_ban.setEnabled(True)
        self.btn_promote.setText("★ Promoted" if p.get("addnode") else "★ Promote")
        self.diag_status.setText("Promote = favourite + persistent · Boot = disconnect now")
        self._redraw()
    def _act_peer(self, kind):
        if not self._sel: return
        addr = self._sel.get("addr", "")
        self.diag_status.setText(f"{kind} {addr} …")
        peer_action(kind, addr, on_done=lambda d: self._acted(kind, d), subver=self._sel.get("subver", ""))
    def _acted(self, kind, d):
        if d and d.get("ok"):
            self.diag_status.setText(f"✓ {kind} done — {d.get('addr', '')}")
            if kind == "boot": self._sel = None; self.btn_promote.setEnabled(False); self.btn_boot.setEnabled(False); self.btn_ban.setEnabled(False)
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
                self._cloud_rot = getattr(self, "_cloud_rot", 0.0) + 0.010
                self._layout_cloud()
            return
        if not self._links and not self._sel: return
        self._phase += 0.05                                        # unbounded so per-link speeds stay smooth
        import math
        ORANGE = QtGui.QColor("#F7931A"); GREEN = QtGui.QColor("#16C784")
        # Packets = ACTUAL traffic measured between the last two peer polls (B/s deltas).
        # A direction with zero live traffic shows no dots — the map only flows when data flows.
        for k, (x, y, pin, pout) in enumerate(self._links):
            if pin > 0:    # real incoming data — bitcoin orange, external peer → BANKON node
                npk = 1 + int(round(2 * pin)); spd = 0.6 + 2.4 * pin; rad = 2.0 + 3.0 * pin
                for j in range(npk):
                    t = (self._phase * spd + j / npk + k * 0.13) % 1.0
                    px, py = x * (1.0 - t), y * (1.0 - t)
                    self._anim.append(self.scene.addEllipse(px - rad, py - rad, 2 * rad, 2 * rad,
                                                            QtGui.QPen(QtCore.Qt.NoPen), QtGui.QBrush(ORANGE)))
            if pout > 0:   # real outgoing data — candle green, BANKON node → external peer
                npk = 1 + int(round(2 * pout)); spd = 0.6 + 2.4 * pout; rad = 2.0 + 3.0 * pout
                for j in range(npk):
                    t = (self._phase * spd + j / npk + k * 0.17) % 1.0
                    px, py = x * t, y * t
                    self._anim.append(self.scene.addEllipse(px - rad, py - rad, 2 * rad, 2 * rad,
                                                            QtGui.QPen(QtCore.Qt.NoPen), QtGui.QBrush(GREEN)))
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
        self.downmsg.setText("starting Bitcoin Core…")
        spawn_fn(lambda: post_json("/api/node/start", {}, timeout=12),
                 lambda d: self.downmsg.setText("Bitcoin Core is starting — the map fills in as peers connect."
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
            self.downmsg.setText("Bitcoin Core is not running — BANKON attaches to your node.\n"
                                 "Start Core to map the network.")
            self.downpanel.show(); self.downpanel.raise_(); self._place_down()
        elif not off and self.downpanel.isVisible():
            self.downpanel.hide()
    def _bankon_name(self):
        la = (self._ni or {}).get("localaddresses") or []
        if la:
            addr = f"{la[0].get('address','')}:{la[0].get('port','')}"
        else:
            addr = ((self._ni or {}).get("subversion", "") or "").strip("/") or "node"
        return "bankon:" + addr
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
        # faint outer cloud = every node our addrman knows (the "all nodes" backdrop)
        if self._known:
            step = max(1, len(self._known) // 240)
            CR = 360
            for i, _nd in enumerate(self._known[::step]):
                ang = 2 * math.pi * (i * 0.61803398875 % 1.0)   # golden-angle scatter
                rr = CR + (i % 7) * 6
                cx, cy = rr * math.cos(ang), rr * math.sin(ang)
                self.scene.addEllipse(cx - 1.3, cy - 1.3, 2.6, 2.6, QtGui.QPen(QtCore.Qt.NoPen), QtGui.QBrush(QtGui.QColor(90, 150, 180, 90)))
        # centre = our node
        self.scene.addEllipse(-30, -30, 60, 60, QtGui.QPen(QtGui.QColor("#F7931A"), 3), QtGui.QBrush(QtGui.QColor("#1a1200")))
        t = self.scene.addText(self._bankon_name()); t.setDefaultTextColor(QtGui.QColor("#F7931A"))
        t.setScale(0.95); t.setPos(-t.boundingRect().width() * 0.95 / 2, 32)
        maxt = max([(p.get('bytessent', 0) + p.get('bytesrecv', 0)) for p in peers] or [1]) or 1
        maxr = max([p.get('bytesrecv', 0) for p in peers] or [1]) or 1
        maxs = max([p.get('bytessent', 0) for p in peers] or [1]) or 1
        maxri = max([r[0] for r in self._rates.values()] or [0]) or 1
        maxro = max([r[1] for r in self._rates.values()] or [0]) or 1
        for i, p in enumerate(peers):
            ang = 2 * math.pi * i / n
            x, y = R * math.cos(ang), R * math.sin(ang)
            traf = p.get('bytessent', 0) + p.get('bytesrecv', 0); frac = traf / maxt
            col = self._traffic_color(frac)
            inbound = p.get('inbound')
            # directional lanes: IN data = bitcoin orange, OUT data = candle green (width ∝ share)
            fin = p.get('bytesrecv', 0) / maxr; fout = p.get('bytessent', 0) / maxs
            ox, oy = -math.sin(ang) * 2.2, math.cos(ang) * 2.2
            self.scene.addLine(ox, oy, x + ox, y + oy, QtGui.QPen(QtGui.QColor(247, 147, 26, 190), 1 + 5 * fin))
            self.scene.addLine(-ox, -oy, x - ox, y - oy, QtGui.QPen(QtGui.QColor(22, 199, 132, 190), 1 + 5 * fout))
            # packets carry only ACTUAL live traffic: per-direction B/s measured between polls
            ri, ro = self._rates.get(p.get("addr"), (0.0, 0.0))
            pin = (ri / maxri) if ri > 0 else 0.0    # incoming from this external node right now
            pout = (ro / maxro) if ro > 0 else 0.0   # outgoing from BANKON node right now
            r = 8 + 10 * frac
            selected = bool(self._sel) and p.get("addr") == self._sel.get("addr")
            promoted = bool(p.get("addnode"))
            if promoted: pen = QtGui.QPen(QtGui.QColor("#FFD37A"), 3)          # ★ favourite = gold ring
            elif inbound: pen = QtGui.QPen(QtGui.QColor("#F7931A"), 2)         # inbound = orange ring
            else: pen = QtGui.QPen(QtGui.QColor("#14405c"), 1)
            self.scene.addEllipse(x - r, y - r, 2 * r, 2 * r, pen, QtGui.QBrush(col))
            self._hits.append((x, y, r, p)); self._links.append((x, y, pin, pout))
            lbl = self.scene.addText(p.get('addr', '')[:24] + "\n" + p.get('subver', '').replace('/', ''))
            lbl.setDefaultTextColor(QtGui.QColor("#FFD37A") if selected else QtGui.QColor("#d6e3ef")); lbl.setScale(0.75)
            lbl.setPos(x + (12 if math.cos(ang) >= 0 else -110), y - 8)
        # Log-based connection ACTIVITY ring — shows the node dialing peers even when getpeerinfo is
        # RPC-choked, so the map is never empty during IBD. connected=green · failed=red · inbound=blue.
        acts = [e for e in (self._act or []) if e.get("kind") in ("connected", "failed", "inbound", "disconnect")]
        if acts and len(peers) < 4:
            AC = {"connected": "#16C784", "failed": "#f85149", "inbound": "#00BFFF", "disconnect": "#F7931A"}
            for i, e in enumerate(acts[-40:]):
                ang = 2 * math.pi * (i * 0.61803398875 % 1.0); rr = 150 + (i % 6) * 10
                x, y = rr * math.cos(ang), rr * math.sin(ang); col = QtGui.QColor(AC.get(e.get("kind"), "#8aa0b4"))
                self.scene.addLine(0, 0, x, y, QtGui.QPen(QtGui.QColor(col.red(), col.green(), col.blue(), 70), 1))
                self.scene.addEllipse(x - 4, y - 4, 8, 8, QtGui.QPen(QtCore.Qt.NoPen), QtGui.QBrush(col))
                lab = e.get("addr") or (("peer=" + e["peer"]) if e.get("peer") else "")
                if lab:
                    t = self.scene.addText(lab); t.setDefaultTextColor(col); t.setScale(0.6); t.setPos(x + 6, y - 8)
        if not self._user_zoom: self._fitview()
        if peers:
            self.info.setText(f"Network map — {len(peers)} peers · ~{len(self._known):,} known · "
                              f"orange dots = live data IN (peer→node) · green dots = live data OUT (node→peer) · "
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
        self.update()
    # --- interaction (learned from QGlobe / Qt_Globe_Engine / Marble): grab to rotate,
    #     wheel to zoom, inertial fling on release; pure-QPainter so it works software-rendered ---
    def mousePressEvent(self, e):
        self._drag = e.position(); self._vel = 0.0; self.setCursor(QtCore.Qt.ClosedHandCursor)
    def mouseMoveEvent(self, e):
        if self._drag is None: return
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
        self._nodes = nodes[:700]              # subsample the cloud for smooth spin
        self._peers = peers
        self._my = my
        self._arcs = [great_circle_points(my[0], my[1], la, lo, 36) for (la, lo, _c, _r) in peers] if my else []
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
        qp.setPen(QtCore.Qt.NoPen); qp.setBrush(QtGui.QBrush(QtGui.QColor(255, 255, 255, 130)))
        for (la, lo) in self._nodes:                                    # known-node cloud
            x, y, v = self._proj(la, lo, cx, cy, R)
            if v: qp.drawEllipse(QtCore.QPointF(x, y), 1.4, 1.4)
        qp.setPen(QtGui.QPen(QtGui.QColor(247, 147, 26, 180), 1.2))     # great-circle arcs
        for arc in self._arcs:
            self._polyline(qp, [self._proj(la, lo, cx, cy, R) for (la, lo) in arc])
        for (la, lo, col, r) in self._peers:                           # connected peers
            x, y, v = self._proj(la, lo, cx, cy, R)
            if v:
                qp.setPen(QtGui.QPen(QtGui.QColor("#0b0f15"), 1)); qp.setBrush(QtGui.QBrush(col))
                qp.drawEllipse(QtCore.QPointF(x, y), r, r)
        if self._my:                                                    # our node
            x, y, v = self._proj(self._my[0], self._my[1], cx, cy, R)
            if v:
                qp.setPen(QtGui.QPen(QtGui.QColor("#F7931A"), 2)); qp.setBrush(QtGui.QBrush(QtGui.QColor("#1a1200")))
                qp.drawEllipse(QtCore.QPointF(x, y), 6, 6)
        qp.end()


class AdvancedGeoWidget(QtWidgets.QWidget):
    """🔬 Advanced geoearth: NASA actual imagery + scientific WGS84 shape/size, with
    strictly OPT-IN external integrations (Google Earth, SpaceNet/satellite). Nothing
    here contacts the network by default and NO wallet data ever leaves the app —
    external features are off until the participant explicitly enables them."""
    ASSET = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "earth_bm.jpg")

    def __init__(self, node_latlon_fn=None):
        super().__init__()
        self._node_latlon = node_latlon_fn or (lambda: None)
        lay = QtWidgets.QVBoxLayout(self)
        priv = QtWidgets.QLabel("🔒 Privacy: no external service is contacted by default; "
                                "no wallet data is ever sent. Google Earth & SpaceNet are opt-in.")
        priv.setStyleSheet("color:#16C784;font-weight:600"); priv.setWordWrap(True)
        lay.addWidget(priv)
        tabs = QtWidgets.QTabWidget(); lay.addWidget(tabs, 1)
        tabs.addTab(self._actual_tab(), "🌍 Actual (NASA)")
        tabs.addTab(self._scientific_tab(), "🔬 Scientific (WGS84)")
        tabs.addTab(self._satellite_tab(), "🛰 Satellite / SpaceNet")

    # ── Actual: bundled NASA Blue Marble (public domain, local — no ping) ──
    def _actual_tab(self):
        w = QtWidgets.QWidget(); v = QtWidgets.QVBoxLayout(w)
        img = QtWidgets.QLabel(); img.setAlignment(QtCore.Qt.AlignCenter)
        pm = QtGui.QPixmap(self.ASSET)
        if not pm.isNull():
            img.setPixmap(pm.scaled(720, 360, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation))
        else:
            img.setText("NASA Blue Marble imagery not found in assets/")
        v.addWidget(img, 1)
        v.addWidget(QtWidgets.QLabel("NASA Blue Marble — public domain, bundled locally (equirectangular). No network access."))
        v.addWidget(self._google_earth_row())
        return w

    # ── Scientific: WGS84 size & shape, high precision ──
    def _scientific_tab(self):
        import math as _m
        a = 6378137.0; f = 1.0 / 298.257223563; b = a * (1 - f); e2 = 2 * f - f * f
        R1 = (2 * a + b) / 3.0
        eq_circ = 2 * _m.pi * a
        # Authalic-ish surface area and ellipsoid volume
        vol = 4.0 / 3.0 * _m.pi * a * a * b
        try:
            import mpmath as mp; mp.mp.dps = 24
            e = mp.sqrt(mp.mpf(str(e2)))
            area = 2 * mp.pi * mp.mpf(str(a))**2 * (1 + (1 - e2) / e * mp.atanh(e))
            area_s = mp.nstr(area, 20)
        except Exception:
            area = 4 * _m.pi * ((a**1.6 * 2 + b**1.6) / 3) ** (1 / 1.6); area_s = f"{area:.6e}"
        rows = [
            ("Datum", "WGS84 (EPSG:4326 / NGA TR8350.2)"),
            ("Semi-major axis a", f"{a:.6f} m"),
            ("Semi-minor axis b", f"{b:.9f} m"),
            ("Flattening f", f"1 / {1/f:.9f}"),
            ("First eccentricity² e²", f"{e2:.18f}"),
            ("Mean radius R1=(2a+b)/3", f"{R1:.9f} m"),
            ("Equatorial circumference", f"{eq_circ:.6f} m"),
            ("Surface area", f"{area_s} m²"),
            ("Volume", f"{vol:.6e} m³"),
            ("Shape", "oblate spheroid (equatorial bulge; not a perfect sphere, not flat)"),
        ]
        w = QtWidgets.QWidget(); grid = QtWidgets.QGridLayout(w)
        for i, (k, val) in enumerate(rows):
            kl = QtWidgets.QLabel(k + ":"); kl.setStyleSheet("color:#8aa0b4")
            vl = QtWidgets.QLabel(val); vl.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
            vl.setStyleSheet("font-family:monospace")
            grid.addWidget(kl, i, 0); grid.addWidget(vl, i, 1)
        note = QtWidgets.QLabel("Projection precision: rendering is float64 (~15–16 sig digits, "
                                "visually exact); point measurement is available to 18 decimals (mpmath).")
        note.setWordWrap(True); note.setStyleSheet("color:#6a808f")
        grid.addWidget(note, len(rows), 0, 1, 2)
        grid.addWidget(self._google_earth_row(), len(rows) + 1, 0, 1, 2)
        return w

    # ── Satellite / SpaceNet: placeholder, opt-in only, never pings ──
    def _satellite_tab(self):
        w = QtWidgets.QWidget(); v = QtWidgets.QVBoxLayout(w)
        self.sat_optin = QtWidgets.QCheckBox("Join as participant to enable satellite / SpaceNet mapping")
        self.sat_optin.setToolTip("Off by default. No SpaceNet/satellite endpoint is contacted unless you opt in.")
        self.sat_optin.toggled.connect(self._on_sat_optin)
        v.addWidget(self.sat_optin)
        self.sat_body = QtWidgets.QLabel(
            "🛰 Satellite / SpaceNet mapping — placeholder.\n\n"
            "Not connected. This tab makes NO network request by default; SpaceNet is a "
            "participant opt-in. Enable the checkbox above to activate (integration TBD).")
        self.sat_body.setWordWrap(True); self.sat_body.setStyleSheet("color:#8aa0b4")
        v.addWidget(self.sat_body, 1)
        return w

    def _on_sat_optin(self, on):
        self.sat_body.setText(
            "🛰 Satellite / SpaceNet — participant mode ENABLED (opt-in).\n\n"
            "Placeholder: a satellite/SpaceNet tile source would attach here. Still no data "
            "is sent automatically; any request would be an explicit action."
            if on else
            "🛰 Satellite / SpaceNet mapping — placeholder.\n\nNot connected. No network request "
            "is made by default; SpaceNet is a participant opt-in.")

    # ── Google Earth: opt-in, location-only, no gmail stored, no wallet data ──
    def _google_earth_row(self):
        box = QtWidgets.QWidget(); h = QtWidgets.QHBoxLayout(box); h.setContentsMargins(0, 6, 0, 0)
        self.ge_optin = QtWidgets.QCheckBox("Enable Google Earth (opt-in)")
        self.ge_optin.setToolTip("Off by default. When on, opens a map LOCATION only — no wallet, "
                                 "account, or gmail is sent or stored.")
        self.ge_btn = QtWidgets.QPushButton("Open node location in Google Earth")
        self.ge_btn.setEnabled(False)
        self.ge_optin.toggled.connect(self.ge_btn.setEnabled)
        self.ge_btn.clicked.connect(self._open_google_earth)
        h.addWidget(self.ge_optin); h.addWidget(self.ge_btn); h.addStretch(1)
        return box

    def _open_google_earth(self):
        if not self.ge_optin.isChecked():
            return
        ll = self._node_latlon() or (0.0, 0.0)   # approx node location only; never wallet data
        lat, lon = ll
        url = f"https://earth.google.com/web/@{lat:.6f},{lon:.6f},1000a,10000000d"
        QtGui.QDesktopServices.openUrl(QtCore.QUrl(url))


class GeoMapTab(QtWidgets.QWidget):
    """Geo map (EPSG:4326 plate carrée). The WHOLE known network from this node's addrman
    (getnodeaddresses — Bitnodes-style, self-sourced, no external API) as a density layer,
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
        self.allnodes = QtWidgets.QCheckBox("all nodes"); self.allnodes.setToolTip("Off = connected peers only · On = whole addrman network")
        self.allnodes.toggled.connect(lambda _: self.refresh()); top.addWidget(self.allnodes)
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
        self.advbtn.setToolTip("NASA actual · scientific WGS84 · satellite/SpaceNet (all opt-in, no default network)")
        self.advbtn.clicked.connect(self._show_advanced); top.addWidget(self.advbtn)
        v.addLayout(top)
        self.scene = QtWidgets.QGraphicsScene(); self.view = QtWidgets.QGraphicsView(self.scene)
        self.view.setRenderHint(QtGui.QPainter.Antialiasing)
        self.view.setStyleSheet("background:#05080d;border:2px solid #00BFFF;border-radius:8px")
        self.advanced = AdvancedGeoWidget(self._my_latlon)
        self.stack = QtWidgets.QStackedWidget(); self.stack.addWidget(self.globe); self.stack.addWidget(self.view); self.stack.addWidget(self.advanced)
        v.addWidget(self.stack, 1)
        self.legend = QtWidgets.QLabel(""); self.legend.setStyleSheet("color:#d6e3ef"); self.legend.setWordWrap(True); v.addWidget(self.legend)
        self._peers, self._ni, self._net, self._act = [], {}, [], []
        self._bg = None; self._bg_n = -1     # cached background pixmap + the node count it was built for
    def _toggle(self):
        # Globe (0) ⇄ Flat (1); from Advanced (2) go back to Globe.
        i = 1 if self.stack.currentIndex() == 0 else 0
        self.stack.setCurrentIndex(i)
        self.toggle.setText("🗺 Flat map" if i == 0 else "🌐 Globe")
    def _show_advanced(self):
        self.stack.setCurrentIndex(2)
    def _on_proj(self, i):
        self.projmode = "flatearth" if i == 1 else "plate"
        self._bg = None                        # projection changed → rebuild background
        self.stack.setCurrentIndex(1)          # show the flat map so the change is visible
        self.toggle.setText("🌐 Globe")
        self._redraw()
    # Flat-map point projection → scene (x, y). Dispatches on the selected mode.
    _AE_R = 350.0
    def proj(self, lon, lat):
        if self.projmode == "flatearth":
            return azimuthal_equidistant(lat, lon, self.W / 2, self.H / 2, self._AE_R)
        return ((lon + 180) / 360 * self.W, (90 - lat) / 180 * self.H)
    def refresh(self):
        spawn("getpeerinfo", self._on_peers, timeout=10)
        spawn("getnetworkinfo", self._on_ni, timeout=8)
        if self.allnodes.isChecked():                        # off = peers only; on = whole addrman
            spawn_fn(lambda: known_nodes(5000), self._on_net)
        else:
            self._net = []; self._redraw()
        spawn_fn(lambda: fetch_json("/api/netactivity?n=60"), self._on_act)   # log-based geo fallback (works during choke)
    def _on_peers(self, peers, stale): self._peers = peers or []; self._redraw()
    def _on_ni(self, ni, stale): self._ni = ni or {}; self._redraw()
    def _on_net(self, nodes): self._net = nodes or []; self._redraw()
    def _on_act(self, d): self._act = (d or {}).get("events", []); self._redraw()
    def _my_latlon(self):
        la = (self._ni or {}).get("localaddresses") or []
        for a in la:
            g = geolocate(a.get("address", ""))
            if g: return g["lat"], g["lon"]
        return None
    def _build_bg(self):
        """World + graticule + the whole known network as a dim density layer (one pixmap)."""
        pm = QtGui.QPixmap(self.W, self.H); pm.fill(QtGui.QColor("#071019"))
        qp = QtGui.QPainter(pm); qp.setRenderHint(QtGui.QPainter.Antialiasing)
        qp.setPen(QtGui.QPen(QtGui.QColor("#16324a"))); qp.setBrush(QtGui.QBrush(QtGui.QColor("#0c2236")))
        for poly in WORLD:
            # Flat-earth (AE): great-circle-densify edges so continents curve correctly.
            ring = densify_latlon(poly) if self.projmode == "flatearth" else poly
            qp.drawPolygon(QtGui.QPolygonF([QtCore.QPointF(*self.proj(lo, la)) for lo, la in ring]))
        qp.setPen(QtGui.QPen(QtGui.QColor("#0e2a3d")))
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
        self.scene.clear()
        if self._bg is None or self._bg_n != len(self._net):
            self._build_bg()
        self.scene.addPixmap(self._bg)
        my = self._my_latlon()
        # great-circle arcs from our node to each connected peer (inferred edges)
        if my:
            mx, my_y = self.proj(my[1], my[0])
            arc_pen = QtGui.QPen(QtGui.QColor(247, 147, 26, 120), 1.0)
            for p in self._peers:
                ip = p.get("addr", "").rsplit(":", 1)[0].strip("[]")
                g = geolocate(ip)
                if not g: continue
                path = QtGui.QPainterPath(); started = False; px = None
                for la, lo in great_circle_points(my[0], my[1], g["lat"], g["lon"], 40):
                    x, y = self.proj(lo, la)
                    if px is not None and abs(x - px) > self.W / 2:   # antimeridian wrap
                        started = False
                    if not started: path.moveTo(x, y); started = True
                    else: path.lineTo(x, y)
                    px = x
                self.scene.addPath(path, arc_pen)
            mk = self.scene.addEllipse(mx - 6, my_y - 6, 12, 12, QtGui.QPen(QtGui.QColor("#F7931A"), 2), QtGui.QBrush(QtGui.QColor("#1a1200")))
            _nc = nearest_city(my[0], my[1])
            mk.setToolTip(f"bankon: this node · nearest city: {_nc[0]}, {_nc[1]} (~{_nc[2]:.0f} km)")
        # connected peers on top, coloured by traffic/direction, ASN in tooltip
        cc, asncc, located, gpeers = Counter(), Counter(), 0, []
        for p in self._peers:
            ip = p.get("addr", "").rsplit(":", 1)[0].strip("[]")
            g = geolocate(ip)
            if not g: continue
            located += 1; cc[g["iso"]] += 1
            an = asn_lookup(ip) or {}
            if an.get("org"): asncc[an["org"][:22]] += 1
            x, y = self.proj(g["lon"], g["lat"])
            traf = p.get("bytessent", 0) + p.get("bytesrecv", 0); inbound = p.get("inbound")
            col = QtGui.QColor("#16C784") if traf > (1 << 20) else (QtGui.QColor("#F7931A") if inbound else QtGui.QColor("#00BFFF"))
            r = 5 + min(6, traf / (1 << 21))
            gpeers.append((g["lat"], g["lon"], col, max(4.0, r)))
            self.scene.addEllipse(x - r - 3, y - r - 3, 2 * (r + 3), 2 * (r + 3), QtGui.QPen(QtCore.Qt.NoPen), QtGui.QBrush(QtGui.QColor(col.red(), col.green(), col.blue(), 60)))
            d = self.scene.addEllipse(x - r, y - r, 2 * r, 2 * r, QtGui.QPen(QtGui.QColor("#eef3f8"), 1), QtGui.QBrush(col))
            _pc = nearest_city(g["lat"], g["lon"])
            d.setToolTip(f"{p.get('addr')}  ·  {flag(g['iso'])} {g['country']}  ·  near {_pc[0]} (~{_pc[2]:.0f} km)  ·  AS{an.get('asn','?')} {an.get('org','')}  ·  {(traf/1048576):.1f} MiB  ·  {'in' if inbound else 'out'}")
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
                dd.setToolTip(f"{e.get('kind')} {e.get('addr')} · {flag(g['iso'])} {g['country']}")
                gpeers.append((g["lat"], g["lon"], col, 5.0)); act_plotted += 1
        self.view.fitInView(self.scene.sceneRect(), QtCore.Qt.KeepAspectRatio)
        net_age = network_asof()
        net_when = datetime.fromtimestamp(net_age).strftime("%H:%M") if net_age else "—"
        if located == 0 and act_plotted:
            self.info.setText(f"Geo map — peer RPC busy (IBD); plotting {act_plotted} geolocated connection events from the log · "
                              "geo approximate (EPSG:4326)" + ("" if HAVE_GEOIP else "  (GeoIP DB missing)"))
        else:
            self.info.setText(
                f"Network ~{len(self._net):,} known nodes (addrman @ {net_when}) · "
                f"{located}/{len(self._peers)} connected peers · {len(cc)} countries · "
                "arcs inferred · geo approximate (EPSG:4326)" + ("" if HAVE_GEOIP else "  (GeoIP DB missing)"))
        top_c = "  ".join(f"{flag(iso)} {iso} {n}" for iso, n in cc.most_common(12))
        top_a = "  ·  ".join(f"{o} {n}" for o, n in asncc.most_common(4))
        self.legend.setText(f"peers by country: {top_c or '—'}" + (f"     top ASNs: {top_a}" if top_a else ""))
        # feed the spinning globe (same data, projected onto the sphere)
        self.globe.set_data([(n["lat"], n["lon"]) for n in self._net], gpeers, my)
    def resizeEvent(self, e):
        if self.scene.sceneRect().width(): self.view.fitInView(self.scene.sceneRect(), QtCore.Qt.KeepAspectRatio)
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
    """BTC.oracle graphical area — a fine electric-blue mesh with an animated shimmer sweep, the
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
        ① identity → ② proof-of-work → ③ structure → ④ economics
    DeFi meets sci-fi, and BANKON.oracle is accuracy: every figure below is measured, none estimated."""
    def __init__(self):
        super().__init__(); self.setObjectName("scienceframe")
        v = QtWidgets.QVBoxLayout(self)
        top = QtWidgets.QHBoxLayout()
        tt = QtWidgets.QLabel("🔬 Block science — visual workflow from the actual block")
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
        for col, (key, title) in enumerate([("pow", "② proof-of-work"), ("struct", "③ structure"), ("econ", "④ economics")]):
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
        hashes = d * 4294967296                                   # expected hashes = difficulty × 2³²
        self.q["pow"].setText(
            f"difficulty {d:.3e}\n"
            f"exp. hashes {hashes:.2e}\n"
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
        sub = S.get("subsidy", 0) / 1e8; fees = S.get("totalfee", 0) / 1e8
        self.q["econ"].setText(
            f"subsidy {sub:g} BTC\n"
            f"fees {fees:.8f} BTC\n"
            f"reward {sub+fees:.8f} BTC\n"
            f"avg feerate {S.get('avgfeerate',0)} sat/vB\n"
            f"avg fee {S.get('avgfee',0):,} sat\n"
            f"total out {S.get('total_out',0)/1e8:,.2f} BTC")
        self.fullbar.setValue(min(4_000_000, int(w)))
        pct = S.get("feerate_percentiles") or []
        if len(pct) == 5:
            self.feebar.setText("fee percentiles (sat/vB):  p10 %s · p25 %s · p50 %s · p75 %s · p90 %s" % tuple(pct))


class OracleTab(QtWidgets.QWidget):
    """BTC.oracle — the clock kept on a Bitcoin block. Bitcoin-orange framed, with an electric-blue
    mesh graphical area (block-interval sparkline + headline) beside the statistical readout, plus a
    block-measurement history accordion for per-block scientific analysis (getblockstats)."""
    def __init__(self):
        super().__init__(); outer = QtWidgets.QVBoxLayout(self); outer.setContentsMargins(5, 5, 5, 5); outer.setSpacing(5)
        frame = QtWidgets.QFrame(); frame.setObjectName("oracleframe")
        v = QtWidgets.QVBoxLayout(frame); v.setContentsMargins(7, 5, 7, 7); v.setSpacing(5)
        t = QtWidgets.QLabel("₿  BTC.oracle — the clock kept on a Bitcoin block"); t.setObjectName("oracletitle")
        t.setAlignment(QtCore.Qt.AlignCenter); v.addWidget(t)
        mid = QtWidgets.QHBoxLayout()
        self.mesh = MeshPanel(); mid.addWidget(self.mesh, 3)               # graphical area
        box, self.f = cardgrid(["chain height", "tip block date", "time since last block",
            "avg block time — all-time (from genesis)", "avg block time — recent (~2016 blk)",
            "protocol target", "basis used", "recommended poll",
            "avg peer ping", "network ↓ / ↑ rate", "network total ↓ / ↑",
            "genesis", "time since last update"])
        box.setMaximumWidth(440); mid.addWidget(box, 2)                    # statistical area
        v.addLayout(mid, 1)
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
        # Block-measurement history — accordion + a logging-verbosity control for scientific monitoring.
        hrow = QtWidgets.QHBoxLayout()
        hh = QtWidgets.QLabel("📜 Block measurement history — expand a block for scientific analysis")
        hh.setStyleSheet("color:#F7931A;font-weight:700"); hrow.addWidget(hh, 1)
        self.automeasure = QtWidgets.QCheckBox("⚡ auto-measure"); self.automeasure.setChecked(True)
        self.automeasure.setToolTip("Measure every new block as it arrives → live activity stream + JSONL.\n"
                                    "The running log is your visual confirmation the node is connected and receiving blocks.")
        hrow.addWidget(self.automeasure)
        hrow.addWidget(QtWidgets.QLabel("logging"))
        self.verb = QtWidgets.QComboBox(); self.verb.addItems(["Quiet", "Normal", "Verbose", "Scientific"])
        self.verb.setCurrentText("Normal")
        self.verb.setToolTip("Detail level for block monitoring / BTC.oracle diagnostics:\n"
                             "Quiet = one-line · Normal = full metric grid · Verbose = + raw getblockstats · "
                             "Scientific = + header + derived measures")
        self.verb.currentTextChanged.connect(self._verb_changed)
        hrow.addWidget(self.verb); outer.addLayout(hrow)
        sc = QtWidgets.QScrollArea(); sc.setWidgetResizable(True); sc.setStyleSheet("border:1px solid #2e4a63;border-radius:6px")
        hold = QtWidgets.QWidget(); self.hist_lay = QtWidgets.QVBoxLayout(hold); self.hist_lay.setAlignment(QtCore.Qt.AlignTop)
        self.hist_lay.setSpacing(2); sc.setWidget(hold); outer.addWidget(sc, 2)
        ml = QtWidgets.QHBoxLayout()
        ml.addWidget(QtWidgets.QLabel("🔬 measurement log")); ml.addStretch()
        ml.addWidget(QtWidgets.QLabel("export"))
        ej = QtWidgets.QPushButton("JSON"); ej.clicked.connect(lambda: self._export("json")); ml.addWidget(ej)
        el = QtWidgets.QPushButton("JSONL"); el.clicked.connect(lambda: self._export("jsonl")); ml.addWidget(el)
        ec = QtWidgets.QPushButton("CSV"); ec.setObjectName("secondary"); ec.clicked.connect(lambda: self._export("csv")); ml.addWidget(ec)
        clr = QtWidgets.QPushButton("clear"); clr.setObjectName("danger"); ml.addWidget(clr); outer.addLayout(ml)
        self.mlog = QtWidgets.QPlainTextEdit(); self.mlog.setReadOnly(True); self.mlog.setMaximumHeight(96)
        self.mlog.setStyleSheet("font-family:monospace;font-size:11px;background:#05080d;color:#c9d4e0"); outer.addWidget(self.mlog)
        clr.clicked.connect(self.mlog.clear)
        qsplit.addWidget(histcol); qsplit.setSizes([460, 500])
        page.addWidget(qsplit, 2)   # complete the 2×2: Q3 science | Q4 history now under Q1|Q2
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
    def _tick_measure(self):
        if self.isVisible() and self.automeasure.isChecked():
            spawn_fn(lambda: fetch_json(f"/api/recentblocks?n={self.SERIES_N}").get("blocks", []), self._fill_blocks)
    def refresh(self):
        spawn_fn(lambda: fetch_json("/api/oracle").get("oracle", {}), self._fill)
        spawn_fn(synctip, self._fill_sync)
        spawn_fn(lambda: fetch_json(f"/api/recentblocks?n={self.SERIES_N}").get("blocks", []), self._fill_blocks)
        spawn_fn(lambda: fetch_json("/api/nethealth"), self._fill_net)
        self.science.refresh()                                     # Q3: re-measure the running block
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
                self.mlog.appendPlainText(f"— BTC.oracle log · level: {self.verb.currentText()} · "
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
                line += f"  ·  {b['nTx'] / (iv * 60):.2f} tx/s"    # derived: tx throughput this interval
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
        if fee is not None: parts.append(f"fees {fee/1e8:.4f}₿")
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
            df["bytes / tx"].setText(f"{s.get('total_size',0)/txs:.1f} B")
            df["vbytes (weight/4)"].setText(f"{tw/4:,.0f} vB")
            df["effective sat/vByte"].setText(f"{fee/(tw/4):.3f}")
            df["fees % of subsidy"].setText(f"{fee/sub*100:.4f}%")
            df["fees % of reward"].setText(f"{fee/rew*100:.4f}%" if rew else "—")
            df["inputs / tx"].setText(f"{s.get('ins',0)/txs:.2f}"); df["outputs / tx"].setText(f"{s.get('outs',0)/txs:.2f}")
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
        f["chainwork"].setText((hd.get("chainwork", "") or "").lstrip("0")[:16] + "…")
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
        mn = lambda s: f"{s/60:.3f} min" if s else "—"
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
        self.mesh.set_headline(mn(a), "average block time · all-time, from genesis")
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
        # ₿ network intelligence strip — self-sourced Bitcoin facts: OUR addrman census (total nodes
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
        self.t.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)              # right-click → Promote / Boot
        self.t.customContextMenuRequested.connect(self._peer_menu)
        self.t.setToolTip("Right-click a peer for actions: ★ Promote (favourite) · ⏏ Boot (disconnect)")
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
                      f"subsidy {50/2**era:g} BTC",
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
        s = (f"connection activity — ✓ {ty.get('connected',0)} connected · ✗ {ty.get('failed',0)} failed · "
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
        m.addAction("⏏ Boot (disconnect now)").triggered.connect(lambda: self._do_peer("boot", addr, p))
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
      🔌 Localhost checks — raw socket probes of every BANKON port from 127.0.0.1's OWN perspective.
                            Deliberately not routed through the Console: a diagnostics panel must
                            not depend on one of the things it diagnoses.
      ⚙ Admin            — node ▶/■ + the AIRGAP switch (setnetworkactive) so the WaaS can generate
                            wallet keys with the machine's Bitcoin network dark, then re-enable.
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
            ("Bitcoin Core RPC (full)",  8332),
            ("Bitcoin P2P",              8333),
            ("Pruned node RPC",          8342),
            ("BANKON Console",           self._port_of(CONSOLE_URL, 8090)),
            ("BANKON WaaS",              self._port_of(WAAS_URL, 8088)),
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
        # BANKON table formula: ODD column counts (1·3·5·7·9·11·13) — port folds into the service
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
        st = QtWidgets.QPushButton("▶ Start node"); st.clicked.connect(self._start); ar.addWidget(st)
        sp = QtWidgets.QPushButton("■ Stop node"); sp.setObjectName("danger"); sp.clicked.connect(self._stop); ar.addWidget(sp)
        ar.addSpacing(24)
        self.airgap = QtWidgets.QPushButton("…"); self.airgap.setEnabled(False)   # armed once state is known
        self.airgap.setToolTip("setnetworkactive — take the Bitcoin network dark, generate wallet keys in the "
                               "WaaS with zero P2P traffic, then switch back ON")
        self.airgap.clicked.connect(self._toggle_net); ar.addWidget(self.airgap)
        waas = QtWidgets.QPushButton("Open WaaS"); waas.clicked.connect(lambda: webbrowser.open(WAAS_URL)); ar.addWidget(waas)
        ar.addStretch(); v.addLayout(ar)
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
            self.f["datadir disk"].setText(f"{used:,.0f} / {size:,.0f} GB ({df.get('pcent','?')}) on {df.get('source','?')}")
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
        if QtWidgets.QMessageBox.question(self, "Stop", "Stop Bitcoin Core?") != QtWidgets.QMessageBox.Yes: return
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
            self.status.setText("network state: 🔒 AIRGAPPED — Bitcoin P2P dark; safe to generate wallet keys in the WaaS")
            self.status.setStyleSheet("color:#F7931A;font-weight:700")
        self.airgap.style().unpolish(self.airgap); self.airgap.style().polish(self.airgap)   # re-apply QSS after objectName change
    def _toggle_net(self):
        want = not self._netactive
        if not want:      # going dark is a state change worth confirming, like Stop
            if QtWidgets.QMessageBox.question(
                self, "Airgap", "Take the Bitcoin network DARK (setnetworkactive false)?\n"
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
    from the BANKON BTC WaaS node (via /api/netactivity), parsed from debug.log so it
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
        hh = self.t.horizontalHeader(); hh.setSectionResizeMode(QtWidgets.QHeaderView.ResizeToContents)
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
        for e in sorted(d.get("events", []), key=lambda x: str(x.get("time") or "")):
            key = (e.get("time"), e.get("addr"), e.get("kind"), e.get("peer"))
            if key in self._seen:
                continue
            self._seen.add(key); self._events.append(e)
        self._events = self._events[-8000:]
        self._render()
    def _render(self):
        want = self.filter.currentText()
        rows = [e for e in self._events if want == "all" or e.get("kind") == want]
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
                     client or DASH, e.get("blocks", "") or DASH, e.get("net", "") or DASH,
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
        self.t.scrollToBottom()   # header is ResizeToContents — columns auto-hug their content
        self._render_summary()
    def _render_summary(self):
        d = self._latest; tally = d.get("tally", {}) or {}
        tr = d.get("transports", {}) or {}; nets = d.get("nets", {}) or {}
        ct = d.get("conntypes", {}) or {}; local = d.get("local", []) or []
        def seg(label, m):
            parts = [f"{k} {v}" for k, v in sorted(m.items(), key=lambda x: -x[1]) if v]
            return f"{label}: " + (" · ".join(parts) if parts else "—")
        counts = (f"<b>{len(self._events)}</b> events shown  ·  "
                  f"<span style='color:#16C784'>connected {tally.get('connected',0)}</span> · "
                  f"<span style='color:#00BFFF'>inbound {tally.get('inbound',0)}</span> · "
                  f"<span style='color:#F7931A'>disconnect {tally.get('disconnect',0)}</span> · "
                  f"<span style='color:#f85149'>failed {tally.get('failed',0)}</span>")
        line2 = (seg("transport", {"v2 (encrypted)": tr.get("v2", 0), "v1 (legacy)": tr.get("v1", 0)})
                 + "   |   " + seg("net", nets) + "   |   " + seg("roles", ct))
        line3 = ("local: " + (" · ".join(local) if local else "—"))
        self.summary.setText(f"{counts}<br>{line2}<br>{line3}")
        self.info.setText(f"Network activity — {len(self._events)} events (BANKON BTC WaaS) · "
                          f"parsed live from debug.log")


class OrdinalsTab(QtWidgets.QWidget):
    """🜚 Ordinals — OPTIONAL read-only panel over the bankon-ord module (which wraps the `ord`
    CLI). Honors the Qt read-only contract: preflight, wallet balance, inscriptions and outputs
    only — every mutating action (inscribe/send/etch/mint) stays in the GATED bankon-ord CLI.
    Degrades honestly: no module → says so; no `ord` binary → the preflight report says so."""
    def __init__(self):
        super().__init__(); v = QtWidgets.QVBoxLayout(self)
        h = QtWidgets.QLabel("🜚 Ordinals — read-only (inscriptions · runes · sat hunting)")
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
        note = QtWidgets.QLabel("Read-only by contract — inscribe/send/etch/mint run only through the gated "
                                "bankon-ord CLI (ordinal-wallet isolation · material-funds guard · human approval).")
        note.setWordWrap(True); note.setStyleSheet("color:#8aa0b4"); v.addWidget(note)
        self.wname.textChanged.connect(self._iso_badge)

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


class IceTab(QtWidgets.QWidget):
    """🧊 ICE — the wall between the network and the wallet. CPU temperature plus the
    radio (RF) kill switch. AIRGAP severs every RF path (Bluetooth/Wi-Fi/WWAN/NFC).
    Radio changes need root → via pkexec; the full ICE controller opens separately."""
    ICE_APP = os.path.expanduser("~/ICE/ice.py")
    RADIOS = [("bluetooth", "Bluetooth"), ("wifi", "Wi-Fi"), ("wwan", "Cellular"), ("nfc", "NFC")]
    def __init__(self):
        super().__init__(); v = QtWidgets.QVBoxLayout(self)
        h = QtWidgets.QLabel("🧊 ICE — the wall between the network and the wallet")
        h.setStyleSheet("font-weight:700;font-size:15px;color:#00BFFF"); v.addWidget(h)
        self.temp = QtWidgets.QLabel("CPU: — °C"); self.temp.setStyleSheet("font-size:22px;font-weight:700"); v.addWidget(self.temp)
        rl = QtWidgets.QHBoxLayout()
        ag = QtWidgets.QPushButton("🛑 AIRGAP (cut all radios)"); ag.clicked.connect(lambda: self._rfk("block")); rl.addWidget(ag)
        rs = QtWidgets.QPushButton("📡 Restore radios"); rs.clicked.connect(lambda: self._rfk("unblock")); rl.addWidget(rs)
        rl.addStretch(1); v.addLayout(rl)
        self.rlabel = QtWidgets.QLabel("radios — …"); self.rlabel.setStyleSheet("font-family:monospace"); v.addWidget(self.rlabel)
        launch = QtWidgets.QPushButton("Open full ICE controller (scaling · auto-cool · persistence · radios)")
        launch.clicked.connect(self._launch); v.addWidget(launch)
        note = QtWidgets.QLabel("ICE gates CPU heat and the machine's radios. AIRGAP severs every RF path between "
                                "the network and the wallet. No wallet data is involved.")
        note.setWordWrap(True); note.setStyleSheet("color:#8aa0b4"); v.addWidget(note); v.addStretch(1)
        self._t = QtCore.QTimer(self); self._t.timeout.connect(self.refresh); self._t.start(2000)
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


class Main(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__(); self.setWindowTitle("BANKON BITCOIN Wallet as a Service")
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
        self.titlebar = QtWidgets.QLabel("₿  the wallet you can BANKON")
        self.titlebar.setObjectName("titlebar"); self.titlebar.setAlignment(QtCore.Qt.AlignCenter)
        cl.addWidget(self.titlebar); cl.addWidget(self.tabs); self.setCentralWidget(central)
        self._glow = QtWidgets.QGraphicsDropShadowEffect(self)
        self._glow.setColor(QtGui.QColor("#00BFFF")); self._glow.setOffset(0, 0); self._glow.setBlurRadius(22)
        self.tabs.setGraphicsEffect(self._glow)
        self._glowAnim = QtCore.QPropertyAnimation(self._glow, b"blurRadius")   # shimmer = pulsing blur
        self._glowAnim.setDuration(2600); self._glowAnim.setLoopCount(-1)
        self._glowAnim.setKeyValueAt(0.0, 12); self._glowAnim.setKeyValueAt(0.5, 38); self._glowAnim.setKeyValueAt(1.0, 12)
        self._glowAnim.start()
        self.ov = OverviewTab(); self.node = NodeTab()
        self.net = NetworkTab()
        self.mp = CardsTab(["txs", "virtual size", "memory / max", "min relay fee", "mempool min fee",
                            "total fee", "unbroadcast", "RBF / loaded"], mp_fill, ["getmempoolinfo"])
        self.blk = BlocksTab()
        self.idx = IndexesTab()
        self.map = NetworkMapTab()
        self.geo = None          # Geo Map is OPTIONAL (toolbar toggle, default OFF) — built lazily so
                                 # its GeoIP lookups + globe spin-timer cost nothing unless enabled.
        self.ords = None         # Ordinals is OPTIONAL too (same lazy pattern) — read-only bankon-ord panel.
        self.oracle = OracleTab()
        self.con = ConsoleTab()
        self.ctl = ControlTab()          # localhost / local-machine client control center
        self.netlog = NetLogTab()        # live network activity log (BANKON BTC WaaS)
        self.ice = IceTab()              # 🧊 ICE — network↔wallet wall (CPU + radios)
        for w, name in [(self.ov,"Overview"),(self.node,"Node"),(self.net,"Network"),(self.map,"Net Map"),
                        (self.netlog,"📡 Net Log"),(self.mp,"Mempool"),(self.blk,"Blocks"),(self.oracle,"BTC.oracle"),
                        (self.idx,"Indexes"),(self.ctl,"🖥 Control"),(self.ice,"🧊 ICE"),(self.con,"RPC Console")]:
            self.tabs.addTab(w, name)
        self.tabs.currentChanged.connect(self.do_refresh)

        bar = self.addToolBar("main")
        ref = QtWidgets.QPushButton("↻ Refresh"); ref.clicked.connect(self.do_refresh); bar.addWidget(ref)
        bar.addWidget(QtWidgets.QLabel("  refresh "))
        self.rate = QtWidgets.QComboBox()
        for label, ms in [("off",0),("10s",10000),("30s",30000),("1 min",60000),("5 min",300000)]:
            self.rate.addItem(label, ms)
        self.rate.setCurrentText("1 min"); self.rate.currentIndexChanged.connect(self.apply_rate); bar.addWidget(self.rate)
        self.geo_chk = QtWidgets.QCheckBox(" 🌍 Geo Map")          # optional GeoIP map tab — default OFF
        self.geo_chk.setToolTip("Show the Geo Map tab (needs geoip/*.mmdb). Off by default.")
        self.geo_chk.toggled.connect(self._toggle_geo); bar.addWidget(self.geo_chk)
        self.ord_chk = QtWidgets.QCheckBox(" 🜚 Ordinals")         # optional read-only ordinals tab — default OFF
        self.ord_chk.setToolTip("Show the Ordinals tab (read-only; needs the bankon-ord module — mutations "
                                "stay in its gated CLI). Off by default.")
        self.ord_chk.toggled.connect(self._toggle_ords); bar.addWidget(self.ord_chk)
        self.inv_chk = QtWidgets.QCheckBox(" ◐ invert")           # polarity inversion — whole window
        self.inv_chk.setToolTip("Polarity inversion ('reverse video'): invert the entire window's theme.\n"
                                "Computed from the dark palette — see docs/design.md → Polarity inversion.")
        self.inv_chk.toggled.connect(self._toggle_invert); bar.addWidget(self.inv_chk)
        self.status_lbl = QtWidgets.QLabel("  ● checking…"); bar.addWidget(self.status_lbl)
        self.core_lbl = QtWidgets.QLabel(" ● CORE"); bar.addWidget(self.core_lbl)
        self.core_lbl.setToolTip("Bitcoin Core monitor — orange ON · red OFF · green ring = connecting/feeding")
        self._core_base = "padding:1px 7px; border-radius:7px; font-weight:800; border:2px solid transparent;"
        self.core_lbl.setStyleSheet("color:#f85149; " + self._core_base)
        self.refresh_lbl = QtWidgets.QLabel("  ↻ —"); self.refresh_lbl.setStyleSheet("color:#0AC18E"); bar.addWidget(self.refresh_lbl)
        spacer = QtWidgets.QWidget(); spacer.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred); bar.addWidget(spacer)
        waas_btn = QtWidgets.QPushButton("+ Create Wallet (WaaS)"); waas_btn.setObjectName("waas")
        waas_btn.setToolTip(WAAS_URL); waas_btn.clicked.connect(lambda: webbrowser.open(WAAS_URL)); bar.addWidget(waas_btn)

        self.zmq_lbl = QtWidgets.QLabel("  ⚡ zmq —"); self.zmq_lbl.setStyleSheet("color:#5a6b7b")
        self.zmq_lbl.setToolTip("ZMQ push — real-time block events from bitcoind (no polling)")
        bar.addWidget(self.zmq_lbl)
        self.sys_lbl = QtWidgets.QLabel("  🖥 —"); self.sys_lbl.setStyleSheet("color:#8aa0b4")
        self.sys_lbl.setToolTip("Host CPU usage · temperature · memory"); bar.addWidget(self.sys_lbl)
        bar.addWidget(QtWidgets.QLabel(" ⏸@"))
        self.pausetemp = QtWidgets.QSpinBox(); self.pausetemp.setRange(80, 110); self.pausetemp.setValue(99); self.pausetemp.setSuffix("°C")
        self.pausetemp.setToolTip("Thermal protection: auto-pause the pruned node at/above this temperature")
        bar.addWidget(self.pausetemp); self._thermal_paused = False
        # Two-way sync toolbar ↔ Control tab threshold (valueChanged doesn't fire on same-value
        # set, so this can't loop). ONE protection engine (_sys below); two views of its dial.
        self.pausetemp.valueChanged.connect(self.ctl.pausetemp.setValue)
        self.ctl.pausetemp.valueChanged.connect(self.pausetemp.setValue)

        self.timer = QtCore.QTimer(); self.timer.timeout.connect(self.do_refresh)
        self.health = QtCore.QTimer(); self.health.timeout.connect(self.poll_health); self.health.start(12000)  # gentle
        self.systimer = QtCore.QTimer(); self.systimer.timeout.connect(self.poll_sys); self.systimer.start(5000); self.poll_sys()
        self.coretimer = QtCore.QTimer(); self.coretimer.timeout.connect(self.poll_coremon); self.coretimer.start(5000); self.poll_coremon()
        self.logt = QtCore.QTimer(); self.logt.timeout.connect(lambda: self.node.load_log() if self.current() is self.node else None); self.logt.start(6000)
        # ZMQ push: refresh on each new block (event-driven) — the rate timer is now a fallback heartbeat.
        self.zmq = ZmqService(self)
        self.zmq.block.connect(self.on_zmq_block)
        self.zmq.status.connect(self.on_zmq_status)
        self.zmq.start()
        self.apply_rate(); self.poll_health(); self.do_refresh()
    def current(self): return self.tabs.currentWidget()
    def _toggle_invert(self, on):
        # Whole-window polarity flip in one call — the app stylesheet is the single styling root,
        # so swapping it inverts every tab at once (that's why this is cheap and total).
        global QSS_INVERTED
        if on and QSS_INVERTED is None: QSS_INVERTED = invert_qss(QSS)
        QtWidgets.QApplication.instance().setStyleSheet(QSS_INVERTED if on else QSS)
    def _toggle_geo(self, on):
        # Build the Geo Map on enable (insert right after Net Map); destroy on disable so its
        # globe spin-timer + GeoIP work fully stop — "default off = nothing running".
        if on:
            if self.geo is None: self.geo = GeoMapTab()
            i = self.tabs.indexOf(self.map) + 1
            self.tabs.insertTab(i, self.geo, "🌍 Geo Map")
            self.tabs.setCurrentWidget(self.geo)                  # currentChanged → refresh
        elif self.geo is not None:
            i = self.tabs.indexOf(self.geo)
            if i != -1: self.tabs.removeTab(i)
            self.geo.deleteLater(); self.geo = None
    def _toggle_ords(self, on):
        # Same lazy build/destroy as Geo Map — "default off = nothing running". Inserted
        # right before RPC Console so diagnostics stay grouped.
        if on:
            if self.ords is None: self.ords = OrdinalsTab()
            i = self.tabs.indexOf(self.con)
            self.tabs.insertTab(i if i != -1 else self.tabs.count(), self.ords, "🜚 Ordinals")
            self.tabs.setCurrentWidget(self.ords)
        elif self.ords is not None:
            i = self.tabs.indexOf(self.ords)
            if i != -1: self.tabs.removeTab(i)
            self.ords.deleteLater(); self.ords = None
    def on_zmq_block(self, block_hash, seq):
        # push-driven refresh — a new block connected; update the active tab + stamp.
        self.zmq_lbl.setText(f"  ⚡ zmq ● block {block_hash[:10]}…"); self.zmq_lbl.setStyleSheet("color:#16C784")
        self.do_refresh()
    def on_zmq_status(self, ok, msg):
        self.zmq_lbl.setText(f"  ⚡ zmq {'●' if ok else '○'} {msg}")
        self.zmq_lbl.setStyleSheet("color:%s" % ("#16C784" if ok else "#5a6b7b"))
    def closeEvent(self, e):
        # Clean kill (X button): stop every timer → no new work; stop the ZMQ thread; join live
        # worker threads. Deterministic teardown — nothing left running, nothing to clean up later.
        for name in ("timer", "health", "systimer", "coretimer", "logt"):
            try: getattr(self, name).stop()
            except Exception: pass
        for t in self.findChildren(QtCore.QTimer):    # child-widget timers (map pulse, globe spin, oracle throb)
            try: t.stop()
            except Exception: pass
        try: self.zmq.stop()                 # joins the subscriber thread (≤2.5s)
        except Exception: pass
        try: shutdown_workers()              # wait out any in-flight RPC workers
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
            self.status_lbl.setText(f"  ● node :8332 · block {b:,}" + (" (cached)" if s else "")); self.status_lbl.setStyleSheet("color:#0AC18E")
        def bad(e):
            self._hb = False
            if "refus" in e.lower() or "connect" in e.lower():
                self.status_lbl.setText("  ● node stopped"); self.status_lbl.setStyleSheet("color:#f85149")
            elif "-28" in e or "warm" in e.lower() or "load" in e.lower():
                self.status_lbl.setText("  ● node booting…"); self.status_lbl.setStyleSheet("color:#F7931A")
            else:
                self.status_lbl.setText("  ● node validating…"); self.status_lbl.setStyleSheet("color:#F7931A")
        spawn("getblockcount", ok, bad, timeout=6)
    def poll_coremon(self):
        spawn_fn(lambda: fetch_json("/api/coremon"), self._coremon)
    def _coremon(self, d):
        d = d or {}
        if not d.get("up"):
            self.core_lbl.setText(" ● CORE OFF"); self.core_lbl.setStyleSheet("color:#f85149; " + self._core_base)
            self.core_lbl.setToolTip("Bitcoin Core not reachable on :" + str(d.get("port", "8332")))
        elif d.get("feeding"):
            self.core_lbl.setText(" ● CORE ON")   # orange ON + green surround = feeding from connect
            self.core_lbl.setStyleSheet("color:#F7931A; padding:1px 7px; border-radius:7px; font-weight:800; border:2px solid #16C784;")
            self.core_lbl.setToolTip(f"Bitcoin Core ON · feeding from connect (block {d.get('height')}, tip {d.get('logAgeSec')}s ago)")
        else:
            self.core_lbl.setText(" ● CORE ON"); self.core_lbl.setStyleSheet("color:#F7931A; " + self._core_base)
            self.core_lbl.setToolTip(f"Bitcoin Core ON (block {d.get('height') or '?'})")
    def poll_sys(self):
        spawn_fn(lambda: fetch_json("/api/system"), self._sys)
    def _sys(self, d):
        if not d or not d.get("ok"): return
        cpu, t, mem = d.get("cpuPct"), d.get("tempC"), d.get("memUsedPct")
        # temperature severity (your calibration): comfortable working zone = Bitcoin orange (fine at
        # 92°C); 96°C = concern; 99°C+ = DANGEROUS red. Cool/idle stays green.
        col, sev, weight = "#16C784", "", "normal"
        if t is not None:
            if t >= 99:   col, sev, weight = "#ff2b2b", "  ⚠ DANGEROUS", "bold"   # RED
            elif t >= 96: col, sev          = "#FF5E3A", "  concern"               # red-orange
            elif t >= 85: col, sev          = "#F7931A", "  HOT"                   # Bitcoin orange (comfortable working @92)
        parts = [f"🖥 cpu {cpu}%"]
        if t is not None: parts.append(f"🌡 {t}°C{sev}")
        if mem is not None: parts.append(f"mem {mem}%")
        self.sys_lbl.setText("  " + " · ".join(parts))
        self.sys_lbl.setStyleSheet(f"color:{col}; font-weight:{weight}")
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


# Multi-chain accent palette (matches the web UIs): Bitcoin orange (primary),
# Polygon purple, Ethereum blue, Cash green (success), Cardano blue (hover), Solana royal.
QSS = """
  QMainWindow { background:#06090e; }
  /* BANKON corporate blue-grey title banner (hint of blue) */
  QLabel#titlebar {
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #3b4b5d, stop:1 #28384a);
    color:#e8eef5; font-size:15px; font-weight:800; letter-spacing:2px;
    border:1px solid #2e4a63; border-bottom:2px solid #00BFFF; border-radius:8px; padding:9px;
  }
  /* BTC.oracle — enhanced Bitcoin-orange outline */
  QFrame#oracleframe { border:2px solid #F7931A; border-radius:12px; background:#06090e; }
  /* 🔬 Block science quadrant — electric-blue outline (the oracle's accuracy panel) */
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
  /* WaaS button — Bitcoin ORANGE background AND orange highlights (hover brightens, never re-hues) */
  QPushButton#waas { background:qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #FFC06B, stop:1 #F7931A); color:#1a1200; border:2px solid #7a4806; font-weight:700; }
  QPushButton#waas:hover { background:qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #FFD9A0, stop:1 #F7931A); color:#1a1200; border:2px solid #FFB74D; }
  QPushButton#waas:pressed { background:#E6850A; border:2px solid #FFD9A0; }
  /* secondary — Polygon purple (chain-accent token) */
  QPushButton#secondary { background:qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #9F6BFF, stop:1 #6C2BD9); color:#fff; border:2px solid #3a1c78; font-weight:700; }
  QPushButton#secondary:hover { background:qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #B98BFF, stop:1 #8247E5); border:2px solid #C4A2FF; }
  QTabBar::tab { background:#10161f; color:#8aa0b4; padding:6px 14px; border:1px solid #0e2738; border-bottom:0; margin-right:1px; }
  QTabBar::tab:selected { color:#eef3f8; background:#13202c; border-bottom:2px solid #00BFFF; }
  QTabBar::tab:hover { color:#7DF9FF; }
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

if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv); app.setStyle("Fusion"); app.setStyleSheet(QSS)
    app.aboutToQuit.connect(shutdown_workers)   # wait for live threads → clean exit
    w = Main(); w.show(); sys.exit(app.exec())
