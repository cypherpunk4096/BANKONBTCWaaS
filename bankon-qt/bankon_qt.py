#!/usr/bin/env python3
"""
BANKON Qt — native diagnostics & node-control UI for Bitcoin Core (PySide6).

Parity with the web Console: live tabs (Overview / Node / Network / Mempool /
Blocks / Indexes / RPC Console), a variable refresh rate (1-min default), node
recognition + Start/Stop, a live debug.log bootup stream, and last-known caching
so tabs keep showing data while the node is lock-bound during IBD.

Launch via bankon-qt.sh (installs PySide6, forces software rendering for HD 3000).
"""
import json, math, os, subprocess, sys, urllib.request, webbrowser
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
from services.geodesy import great_circle_points
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


class OverviewTab(QtWidgets.QWidget):
    def __init__(self):
        super().__init__(); v = QtWidgets.QVBoxLayout(self)
        self.bar = QtWidgets.QProgressBar(); self.bar.setMaximum(100000)
        v.addWidget(QtWidgets.QLabel("<b>Sync</b>")); v.addWidget(self.bar)
        box, self.f = cardgrid(["chain", "height", "headers", "verify %", "peers", "mempool txs", "size on disk", "IBD"])
        v.addWidget(box); v.addStretch()
        # near-realtime sync: /api/synctip is a cheap debug.log tail (no node RPC), so poll it
        # every 3s while this tab is visible — the % ticks up as the node validates blocks.
        self._synctimer = QtCore.QTimer(self); self._synctimer.timeout.connect(self._tick_sync); self._synctimer.start(3000)
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
        hh = self.t.horizontalHeader(); hh.setSectionResizeMode(QtWidgets.QHeaderView.Interactive); hh.setStretchLastSection(True)
        hh.setSectionsMovable(True); hh.setMinimumSectionSize(70)
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
            cells = [f"{b.get('height',0):,}", (h[:18] + "…" if h else "—"), tm, (f"{nt:,}" if isinstance(nt, int) else "—")]
            for c, val in enumerate(cells):
                it = QtWidgets.QTableWidgetItem(val)
                if c == 0: it.setData(QtCore.Qt.UserRole, h)        # stash full hash for the detail view
                self.t.setItem(r, c, it)
        self.t.resizeColumnsToContents(); self.t.setSortingEnabled(True)
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
        self.t = QtWidgets.QTableWidget(); self.t.setColumnCount(7)
        self.t.setHorizontalHeaderLabels(["index", "height", "behind", "% indexed", "rate (blk/min)", "ETA", "status"])
        hh = self.t.horizontalHeader(); hh.setSectionResizeMode(QtWidgets.QHeaderView.Interactive); hh.setStretchLastSection(True)
        hh.setMinimumSectionSize(70); self.t.verticalHeader().setVisible(False)
        self.t.verticalHeader().setDefaultSectionSize(28); self.t.setShowGrid(False)
        self.t.setAlternatingRowColors(True); self.t.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        v.addWidget(self.t)
        self.activity = QtWidgets.QLabel("activity: —"); self.activity.setStyleSheet("color:#16C784;font-weight:600"); v.addWidget(self.activity)
        self.detail = QtWidgets.QLabel("sync: —"); self.detail.setStyleSheet("color:#c9d4e0;font-family:monospace;font-size:12px")
        self.detail.setWordWrap(True); v.addWidget(self.detail)
        self.note = QtWidgets.QLabel("Indexes build in the background during IBD and catch up to the chain tip.")
        self.note.setStyleSheet("color:#8B949E"); self.note.setWordWrap(True); v.addWidget(self.note); v.addStretch()
        self._tip = 0; self._idx = {}
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
        if indexing:
            self._si = (self._si + 1) % len(self._spin)
            self.crunch.setText(f"{self._spin[self._si]}  crunching  ·  {self._crunch_text}")
        else:
            self.crunch.setText(f"⏸  idle  ·  {self._crunch_text}")
    def refresh(self):
        spawn_fn(lambda: fetch_json("/api/indexinfo").get("indexes", {}), self._setidx)  # FRESH, realtime
        spawn_fn(synctip, self._settip)                    # cheap live chain tip (debug.log)
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
            self._crunch_text = f"block #{h:,}  ·  +{dtx:,} txs  ·  cache {st.get('cache', '—')}"
            sess = h - self._first_tip; ago = int(now - self._last_tt) if self._last_tt else 0
            self.activity.setText(f"activity:  ▲ {self._rate:.1f} blk/min  ·  +{sess:,} indexed since open  ·  last advance {ago}s ago")
            prog = st.get("progress"); bd = (st.get("blockDate") or "—").replace("T", " ").replace("Z", "")
            lt = (st.get("logTime") or ""); lt = lt[11:19] if len(lt) >= 19 else "—"
            head = f"tip {h:,}  ·  {prog*100:.4f}%  " if prog is not None else f"tip {h:,}  "
            self.detail.setText(head + f"·  block date {bd}  ·  tx {st.get('tx') or 0:,}  ·  UTXO cache {st.get('cache','—')}  ·  last UpdateTip {lt}")
        self._render()
    def _setidx(self, idx): self._idx = idx or {}; self._render()
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
                cells = ["txindex", f"~{tip:,}", "0", "tracking", f"{self._rate:.1f}", "≈ tip", "indexing… (≈ tip)"]
                for c, val in enumerate(cells): self.t.setItem(0, c, QtWidgets.QTableWidgetItem(val))
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
                     "synced ✓" if synced else "indexing…"]
            for c, val in enumerate(cells):
                it = QtWidgets.QTableWidgetItem(val)
                if c == 6: it.setForeground(QtGui.QColor("#16C784" if synced else "#F7931A"))
                self.t.setItem(r, c, it)
        self.t.resizeColumnsToContents()
        self.bar.setValue(int(primary * 1000)); self.bar.setFormat(f"{primary:.3f}% — tip {tip:,}")
        allsync = all((idx[n] or {}).get("synced") for n in names)
        missing = [k for k in KNOWN if k not in names]
        miss = f"  ·  not enabled: {', '.join(missing)}" if missing else ""
        self.lbl.setText(f"<b>Index quality</b> — live · {len(names)} index(es) · {'all synced' if allsync else 'building'} (tip {tip:,}){miss}")


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
        try: self.out.setPlainText(json.dumps(rpc(m, params), indent=2))
        except Exception as e: self.out.setPlainText("ERROR: " + str(e))


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
        self.view.viewport().installEventFilter(self)        # click → select a peer node
        split.addWidget(self.view)
        split.addWidget(self._build_diag())
        split.setStretchFactor(0, 1); split.setStretchFactor(1, 0); split.setSizes([680, 300])
        v.addWidget(split, 1)
        self._ni, self._known, self._peers, self._act, self._pstale = {}, [], [], [], False
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
        br.addWidget(self.btn_promote); br.addWidget(self.btn_boot); d.addLayout(br)
        self.diag_status = QtWidgets.QLabel(""); self.diag_status.setStyleSheet("color:#8aa0b4"); self.diag_status.setWordWrap(True)
        d.addWidget(self.diag_status)
        self.btn_promote.setEnabled(False); self.btn_boot.setEnabled(False)
        return w
    def refresh(self):
        spawn("getnetworkinfo", self._setni)
        n = self.maxnodes.value()
        spawn_fn(lambda: known_nodes(max(1, n)) if n else [], self._setknown)
        spawn("getpeerinfo", self._setpeers, timeout=10)
        spawn_fn(lambda: fetch_json("/api/netactivity?n=60"), self._setact)   # log-based fallback (works during the RPC choke)
    def _setni(self, ni, stale): self._ni = ni or {}; self._redraw()
    def _setknown(self, nodes): self._known = nodes or []; self._redraw()
    def _setpeers(self, peers, stale):
        self._peers = peers or []; self._pstale = bool(stale)
        if self._sel:                                          # keep the open diagnostics panel live
            fresh = next((p for p in self._peers if p.get("addr") == self._sel.get("addr")), None)
            if fresh: self._sel = fresh; self._fill_diag(fresh)
            else: self.diag_status.setText("(this peer is no longer connected)")
        self._redraw()
    def _setact(self, d): self._act = (d or {}).get("events", []); self._redraw()
    # ---- click-to-select a peer node ----
    def eventFilter(self, obj, ev):
        if obj is self.view.viewport() and ev.type() == QtCore.QEvent.MouseButtonPress:
            sp = self.view.mapToScene(ev.position().toPoint()) if hasattr(ev, "position") else self.view.mapToScene(ev.pos())
            best, bestd = None, 1e9
            for (x, y, r, p) in self._hits:
                dd = (sp.x() - x) ** 2 + (sp.y() - y) ** 2
                if dd <= (r + 10) ** 2 and dd < bestd: best, bestd = p, dd
            if best: self._select(best)
            else: self._sel = None; self.diag_title.setText("◎ click a peer node for diagnostics"); \
                self._clear_form(); self.btn_promote.setEnabled(False); self.btn_boot.setEnabled(False); self.diag_status.setText("")
            return False
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
        self.btn_promote.setEnabled(True); self.btn_boot.setEnabled(True)
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
            if kind == "boot": self._sel = None; self.btn_promote.setEnabled(False); self.btn_boot.setEnabled(False)
            QtCore.QTimer.singleShot(700, self.refresh)
        else:
            self.diag_status.setText(f"✗ {(d or {}).get('error', 'failed')}")
    def _pulse(self):
        # DYNAMIC overlay: traffic 'packets' flow centre→peer along each link, + a halo on the
        # selected node. Cheap — only the overlay is rebuilt each tick; the base scene is static.
        for it in self._anim:
            try: self.scene.removeItem(it)
            except Exception: pass
        self._anim = []
        if not self._links and not self._sel: return
        self._phase = (self._phase + 0.05) % 1.0
        import math
        for k, (x, y, frac) in enumerate(self._links):
            if frac < 0.015: continue
            col = self._traffic_color(frac)
            for j in range(2):                                     # two packets per link, offset
                t = (self._phase + j * 0.5 + k * 0.13) % 1.0
                px, py = x * t, y * t; rad = 2.0 + 3.0 * frac
                dot = self.scene.addEllipse(px - rad, py - rad, 2 * rad, 2 * rad, QtGui.QPen(QtCore.Qt.NoPen), QtGui.QBrush(col))
                self._anim.append(dot)
        if self._sel:                                              # pulsing selection halo
            sx = sy = None
            for (x, y, r, p) in self._hits:
                if p.get("addr") == self._sel.get("addr"): sx, sy, sr = x, y, r; break
            if sx is not None:
                hr = sr + 6 + 4 * math.sin(self._phase * 2 * math.pi)
                ring = self.scene.addEllipse(sx - hr, sy - hr, 2 * hr, 2 * hr, QtGui.QPen(QtGui.QColor("#FFD37A"), 2.5))
                self._anim.append(ring)
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
        for i, p in enumerate(peers):
            ang = 2 * math.pi * i / n
            x, y = R * math.cos(ang), R * math.sin(ang)
            traf = p.get('bytessent', 0) + p.get('bytesrecv', 0); frac = traf / maxt
            col = self._traffic_color(frac)
            inbound = p.get('inbound')
            self.scene.addLine(0, 0, x, y, QtGui.QPen(col, 1 + 6 * frac))
            r = 8 + 10 * frac
            selected = bool(self._sel) and p.get("addr") == self._sel.get("addr")
            promoted = bool(p.get("addnode"))
            if promoted: pen = QtGui.QPen(QtGui.QColor("#FFD37A"), 3)          # ★ favourite = gold ring
            elif inbound: pen = QtGui.QPen(QtGui.QColor("#F7931A"), 2)         # inbound = orange ring
            else: pen = QtGui.QPen(QtGui.QColor("#14405c"), 1)
            self.scene.addEllipse(x - r, y - r, 2 * r, 2 * r, pen, QtGui.QBrush(col))
            self._hits.append((x, y, r, p)); self._links.append((x, y, frac))
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
        self.view.fitInView(self.scene.itemsBoundingRect().adjusted(-60, -40, 60, 40), QtCore.Qt.KeepAspectRatio)
        if peers:
            self.info.setText(f"Network map — {len(peers)} peers · ~{len(self._known):,} known · flowing dots = traffic · "
                              f"gold = ★favourite · orange = inbound · click a node for diagnostics{' · cached' if stale else ''}")
        elif acts:
            nc = sum(1 for e in acts if e['kind'] == 'connected'); nf = sum(1 for e in acts if e['kind'] == 'failed')
            self.info.setText(f"Network map — peer RPC busy (IBD); showing live connection activity from the log: "
                              f"{nc} connected · {nf} failed (centre = your node)")
        else:
            self.info.setText("Network map — waiting for the node (RPC busy during verification/IBD)")
    def resizeEvent(self, e):
        if self.scene.items(): self.view.fitInView(self.scene.itemsBoundingRect().adjusted(-60, -40, 60, 40), QtCore.Qt.KeepAspectRatio)
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
        v.addLayout(top)
        self.scene = QtWidgets.QGraphicsScene(); self.view = QtWidgets.QGraphicsView(self.scene)
        self.view.setRenderHint(QtGui.QPainter.Antialiasing)
        self.view.setStyleSheet("background:#05080d;border:2px solid #00BFFF;border-radius:8px")
        self.stack = QtWidgets.QStackedWidget(); self.stack.addWidget(self.globe); self.stack.addWidget(self.view)
        v.addWidget(self.stack, 1)
        self.legend = QtWidgets.QLabel(""); self.legend.setStyleSheet("color:#d6e3ef"); self.legend.setWordWrap(True); v.addWidget(self.legend)
        self._peers, self._ni, self._net, self._act = [], {}, [], []
        self._bg = None; self._bg_n = -1     # cached background pixmap + the node count it was built for
    def _toggle(self):
        i = 1 - self.stack.currentIndex(); self.stack.setCurrentIndex(i)
        self.toggle.setText("🗺 Flat map" if i == 0 else "🌐 Globe")
    def proj(self, lon, lat): return ((lon + 180) / 360 * self.W, (90 - lat) / 180 * self.H)
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
            qp.drawPolygon(QtGui.QPolygonF([QtCore.QPointF(*self.proj(lo, la)) for lo, la in poly]))
        qp.setPen(QtGui.QPen(QtGui.QColor("#0e2a3d")))
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
            mk.setToolTip("bankon: this node")
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
            d.setToolTip(f"{p.get('addr')}  ·  {flag(g['iso'])} {g['country']}  ·  AS{an.get('asn','?')} {an.get('org','')}  ·  {(traf/1048576):.1f} MiB  ·  {'in' if inbound else 'out'}")
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
        self.setMinimumHeight(300)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        self._phase = 0.0; self._series = []; self._headline = "—"; self._sub = ""
        self._t = QtCore.QTimer(self); self._t.timeout.connect(self._tick); self._t.start(45)  # shimmer ~22 fps
    def _tick(self): self._phase = (self._phase + 0.010) % 1.0; self.update()
    def set_series(self, vals): self._series = vals or []; self.update()
    def set_headline(self, h, sub=""): self._headline = h; self._sub = sub; self.update()
    def paintEvent(self, e):
        qp = QtGui.QPainter(self); qp.setRenderHint(QtGui.QPainter.Antialiasing)
        w, h = self.width(), self.height()
        qp.fillRect(0, 0, w, h, QtGui.QColor("#04070c"))
        step = 16
        qp.setPen(QtGui.QPen(QtGui.QColor(0, 191, 255, 36), 1))            # fine electric-blue mesh
        x = 0
        while x <= w: qp.drawLine(x, 0, x, h); x += step
        y = 0
        while y <= h: qp.drawLine(0, y, w, y); y += step
        cx = self._phase * (w + 240) - 120                                 # shimmer band sweeping across
        gx = 0
        while gx <= w:
            d = abs(gx - cx)
            if d < 110:
                qp.setPen(QtGui.QPen(QtGui.QColor(150, 228, 255, int(150 * (1 - d / 110))), 1.4)); qp.drawLine(gx, 0, gx, h)
            gx += step
        if len(self._series) > 1:                                          # block-interval sparkline
            mx = max(self._series) or 1; n = len(self._series)
            path = QtGui.QPainterPath()
            for i, vlt in enumerate(self._series):
                px = 14 + i / (n - 1) * (w - 28)
                py = h - 22 - (vlt / mx) * (h - 110)
                path.moveTo(px, py) if i == 0 else path.lineTo(px, py)
            qp.setPen(QtGui.QPen(QtGui.QColor("#F7931A"), 2)); qp.drawPath(path)
        # THROB: the headline pulses forward/back (depth) — a glow halo + size pulse from a sine
        throb = 0.5 + 0.5 * math.sin(self._phase * 2 * math.pi * 2)
        gr = 80 + 55 * throb
        gg = QtGui.QRadialGradient(w / 2, h / 2 - 8, gr)
        gg.setColorAt(0, QtGui.QColor(0, 191, 255, int(55 + 70 * throb))); gg.setColorAt(1, QtGui.QColor(0, 191, 255, 0))
        qp.setPen(QtCore.Qt.NoPen); qp.setBrush(QtGui.QBrush(gg))
        qp.drawEllipse(QtCore.QPointF(w / 2, h / 2 - 8), gr, gr * 0.5)
        qp.setPen(QtGui.QColor("#eef3f8"))                                  # headline avg block time
        f = qp.font(); f.setPointSize(26 + int(6 * throb)); f.setBold(True); qp.setFont(f)
        qp.drawText(QtCore.QRectF(0, h / 2 - 38, w, 54), QtCore.Qt.AlignCenter, self._headline)
        f.setPointSize(10); f.setBold(False); qp.setFont(f); qp.setPen(QtGui.QColor("#8aa0b4"))
        qp.drawText(QtCore.QRectF(0, h / 2 + 18, w, 20), QtCore.Qt.AlignCenter, self._sub)
        qp.end()


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


class OracleTab(QtWidgets.QWidget):
    """BTC.oracle — the clock kept on a Bitcoin block. Bitcoin-orange framed, with an electric-blue
    mesh graphical area (block-interval sparkline + headline) beside the statistical readout, plus a
    block-measurement history accordion for per-block scientific analysis (getblockstats)."""
    def __init__(self):
        super().__init__(); outer = QtWidgets.QVBoxLayout(self); outer.setContentsMargins(6, 6, 6, 6)
        frame = QtWidgets.QFrame(); frame.setObjectName("oracleframe")
        v = QtWidgets.QVBoxLayout(frame)
        t = QtWidgets.QLabel("₿  BTC.oracle — the clock kept on a Bitcoin block"); t.setObjectName("oracletitle")
        t.setAlignment(QtCore.Qt.AlignCenter); v.addWidget(t)
        mid = QtWidgets.QHBoxLayout()
        self.mesh = MeshPanel(); mid.addWidget(self.mesh, 3)               # graphical area
        box, self.f = cardgrid(["chain height", "tip block date", "time since last block",
            "avg block time — all-time (from genesis)", "avg block time — recent (~2016 blk)",
            "protocol target", "basis used", "recommended poll", "genesis", "time since last update"])
        box.setMaximumWidth(440); mid.addWidget(box, 2)                    # statistical area
        v.addLayout(mid, 1)
        outer.addWidget(frame)
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
        self.mlog = QtWidgets.QPlainTextEdit(); self.mlog.setReadOnly(True); self.mlog.setMaximumHeight(130)
        self.mlog.setStyleSheet("font-family:monospace;font-size:11px;background:#05080d;color:#c9d4e0"); outer.addWidget(self.mlog)
        clr.clicked.connect(self.mlog.clear)
        self._hist_heights = set(); self._measurements = []
        self._logdir = Path.home() / "bankon-tools" / "oracle-logs"   # default: auto-persist as JSONL
        try: self._logdir.mkdir(parents=True, exist_ok=True)
        except Exception: pass
        self._auto_jsonl = self._logdir / "measurements.jsonl"; self._primed = False
        self._blk_logtime = 0.0; self._lastupd = 0.0
        self._clk = QtCore.QTimer(self); self._clk.timeout.connect(self._clock); self._clk.start(1000)
        # auto-measure: poll for new blocks every 8s (log-based, runs even when this tab isn't shown)
        self._mtimer = QtCore.QTimer(self); self._mtimer.timeout.connect(self._tick_measure); self._mtimer.start(8000)
    def _tick_measure(self):
        if self.automeasure.isChecked():
            spawn_fn(lambda: fetch_json("/api/recentblocks?n=30").get("blocks", []), self._fill_blocks)
    def refresh(self):
        spawn_fn(lambda: fetch_json("/api/oracle").get("oracle", {}), self._fill)
        spawn_fn(synctip, self._fill_sync)
        spawn_fn(lambda: fetch_json("/api/recentblocks?n=60").get("blocks", []), self._fill_blocks)
    def _fill_blocks(self, rb):
        blocks = [b for b in (rb or []) if b.get("time") and b.get("height") is not None]
        srt = sorted(blocks, key=lambda b: b["height"])
        series = []
        for i in range(1, len(srt)):
            dt = (srt[i]["time"] - srt[i - 1]["time"]) / 60.0             # interval, minutes
            if dt >= 0: series.append(min(dt, 120))
        self.mesh.set_series(series)
        # accordion history — add any new blocks (ascending → insert at top so newest is on top)
        tmap = {b["height"]: b["time"] for b in srt}
        for b in srt:
            hgt = b["height"]
            if hgt in self._hist_heights: continue
            self._hist_heights.add(hgt)
            prev = tmap.get(hgt - 1); iv = f"{(b['time']-prev)/60.0:.1f} min" if prev else "—"
            when = datetime.fromtimestamp(b["time"], timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            title = f"#{hgt:,}   ·   {when} UTC   ·   Δ {iv}   ·   {b.get('nTx','?')} txs"
            row = Collapsible(title, on_expand=lambda lay, lbl, H=hgt: self._block_detail(H, lay, lbl))
            self.hist_lay.insertWidget(0, row)
            if self._primed and self.automeasure.isChecked():             # only NEW arrivals, not the backlog
                self._log_basic(b, tmap.get(hgt - 1))
        self._primed = True                                               # first fill = backlog (no auto-log spam)
        while self.hist_lay.count() > 80:                                 # cap memory
            w = self.hist_lay.takeAt(self.hist_lay.count() - 1).widget()
            if w: w.setParent(None)
    def _log_basic(self, b, prevtime):
        # lightweight per-block measurement (log-based — works during the RPC choke). The live stream
        # in the log is the visual confirmation the node is connected and feeding blocks.
        h = b["height"]; iv = ((b["time"] - prevtime) / 60.0) if prevtime else None
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        line = f"[{ts}] ⬢ NEW block #{h:,}  ·  {b.get('nTx','?')} txs"
        if iv is not None: line += f"  ·  Δ {iv:.1f} min"
        self.mlog.appendPlainText(line)
        rec = {"ts": datetime.now(timezone.utc).isoformat(), "height": h, "time": b.get("time"),
               "nTx": b.get("nTx"), "interval_min": round(iv, 3) if iv is not None else None,
               "hash": b.get("hash"), "source": "auto"}
        self._measurements.append(rec)
        try:
            with open(self._auto_jsonl, "a") as fh: fh.write(json.dumps(rec) + "\n")
        except Exception: pass
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
        spawn("getpeerinfo", self._fill, timeout=10)
        spawn("getnetworkinfo", self._setni, timeout=8)                       # our local node address
        spawn_fn(lambda: fetch_json("/api/netactivity?n=50"), self._setact)   # log-based connection activity
    def _setni(self, ni, stale):
        ni = ni or {}
        la = ni.get("localaddresses") or []
        sub = (ni.get("subversion", "") or "").strip("/")
        if la:
            addrs = " · ".join(f"{a.get('address')}:{a.get('port')}" for a in la[:3])
            self.local_lbl.setText(f"our node:  {addrs}   ·   {sub}   ·   protocol {ni.get('protocolversion','?')}")
        else:
            self.local_lbl.setText(f"our node:  (no public address advertised — likely behind NAT)   ·   {sub or '—'}")
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
        # Honesty: an empty + stale result means the node's RPC is busy (IBD/breaker open) and we
        # couldn't read peers — NOT that there are zero. Don't show a misleading "0".
        if stale and not peers:
            self.conns.setText("connections: — (node RPC busy — can't read peers during heavy IBD)")
            self.conns.setStyleSheet("color:#F7931A;font-weight:700")
            return
        n = len(peers); tgt = int(os.environ.get("BANKON_PEER_TARGET", "12"))
        inb = sum(1 for p in peers if p.get("inbound")); out = n - inb
        # The target is a FLOOR (minimum desired for healthy sync), not a cap — exceeding it is good.
        meet = "✓ above target" if n >= tgt else "building toward target…"
        self.conns.setText(f"connections: {n} ({out} out · {inb} in)   ·   target ≥ {tgt}  {meet}" + ("   (cached)" if stale else ""))
        self.conns.setStyleSheet("color:%s;font-weight:700" % ("#16C784" if n >= tgt else "#F7931A" if n >= 3 else "#f85149"))
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


class Main(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__(); self.setWindowTitle("BANKON BITCOIN Wallet as a Service"); self.resize(940, 680)
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
        self.oracle = OracleTab()
        self.con = ConsoleTab()
        for w, name in [(self.ov,"Overview"),(self.node,"Node"),(self.net,"Network"),(self.map,"Net Map"),
                        (self.mp,"Mempool"),(self.blk,"Blocks"),(self.oracle,"BTC.oracle"),(self.idx,"Indexes"),(self.con,"RPC Console")]:
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
  QPushButton#waas { background:qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #FFC06B, stop:1 #E6850A); color:#1a1200; border:2px solid #7a4806; font-weight:700; }
  QPushButton#waas:hover { background:qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #2bd6a6, stop:1 #07a06f); color:#03120d; border:2px solid #00BFFF; }
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

if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv); app.setStyle("Fusion"); app.setStyleSheet(QSS)
    app.aboutToQuit.connect(shutdown_workers)   # wait for live threads → clean exit
    w = Main(); w.show(); sys.exit(app.exec())
