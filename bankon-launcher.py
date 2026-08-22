#!/usr/bin/env python3
# ₿ANKON Launcher — a one-button GTK3 window to START / stop the ₿ANKON ₿TC WaaS UI.
# No terminal, no sudo. Press the big button.
import os
import shutil
import signal
import subprocess

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib  # noqa: E402

APP = os.path.expanduser("~/bankon-tools/bankon-qt/bankon.sh")
LOG = os.path.expanduser("~/bankon-tools/bankon-qt/bankon.log")

ORANGE = "#F7931A"   # bitcoin orange — the launcher's highlight colour

CSS = b"""
decoration { border-radius: 12px; }   /* bottom corners rounded like the top */
window { background-color: #0e1116; }
label { color: #e6edf3; }
/* log-opacity slider: bitcoin-orange fill, raised round knob with depth */
scale.logfx trough { background-color: #1a2230; border: 1px solid #14405c; border-radius: 6px; min-height: 10px; }
scale.logfx highlight { background-color: #F7931A; border-radius: 6px;
  box-shadow: inset 0 1px 2px rgba(255,255,255,0.25); }
scale.logfx slider { background-color: #e8edf3; border: 2px solid #F7931A; border-radius: 999px;
  min-width: 20px; min-height: 20px;
  box-shadow: 0 2px 5px rgba(0,0,0,0.6), inset 0 1px 1px rgba(255,255,255,0.6); }
scale.logfx slider:hover { background-color: #ffffff; }
scale.logfx value { color: #F7931A; font-weight: 800; }
button.logmini { border-radius: 8px; padding: 1px 8px; font-size: 11px;
  background-image: none; background-color: #10161f; color: #8fb8d8; border: 1px solid #14405c;
  box-shadow: 0 1px 3px rgba(0,0,0,0.5); }
button.logmini:hover { background-color: #14405c; color: #ffffff; }
button.viewon { background-image: none; background-color: #F7931A; color: #1a1200; font-weight: 800;
  border: 1px solid #b96f10; box-shadow: inset 0 2px 4px rgba(0,0,0,0.35); }   /* active view = pressed orange */
button { border-radius: 12px; margin: 2px;            /* corners equal + room for the shadow */
  box-shadow: 0 3px 7px rgba(0,0,0,0.55), 0 1px 2px rgba(0,0,0,0.45); }   /* outer DEPTH */
button.bankon { color: #0a2413; font-weight: 800; border: 2px solid #0b5d34;
  background-image: linear-gradient(to bottom, #1fc05c, #0f7a38);         /* START = raised green */
  box-shadow: 0 4px 9px rgba(0,0,0,0.6), inset 0 1px 0 rgba(255,255,255,0.25); }
button.bankon:hover { background-image: linear-gradient(to bottom, #27d96b, #14934a); }
button.bankon:disabled { background-image: none; background-color: #24352a;
                         color: #5a7a64; border-color: #2f4a3a; }
button.bankon-stop { font-weight: 800; font-size: 11px; padding: 1px 10px;
  background-image: linear-gradient(to bottom, #d63a41, #9e1c22);   /* STOP = raised red pill */
  border: 2px solid #7a1216;
  box-shadow: 0 2px 5px rgba(0,0,0,0.55), inset 0 1px 0 rgba(255,255,255,0.2); }
button.bankon-stop:hover { background-image: linear-gradient(to bottom, #ee4a52, #b5262d); }
button.bankon-stop:disabled { background-image: none; background-color: #3a2426; border-color: #4a2a2c; }
button.bankon-stop label { color: #2b0405;                            /* white boundary around the text */
  text-shadow: -1px -1px 0 #ffffff, 1px -1px 0 #ffffff, -1px 1px 0 #ffffff, 1px 1px 0 #ffffff,
               -1px 0 0 #ffffff, 1px 0 0 #ffffff, 0 -1px 0 #ffffff, 0 1px 0 #ffffff; }
button.core { color: #1a1200; font-weight: 700; font-size: 10px; padding: 1px 8px;
  background-image: linear-gradient(to bottom, #ffa62e, #d97f0a);   /* Core = raised orange */
  border: 1px solid #b96a06; border-radius: 8px;
  box-shadow: 0 2px 5px rgba(0,0,0,0.55), inset 0 1px 0 rgba(255,255,255,0.25); }
button.core:hover { background-image: linear-gradient(to bottom, #ffb954, #ef9312); }
button.bankon-close { font-weight: 800; font-size: 11px; padding: 1px 10px;
  background-image: linear-gradient(to bottom, #3a4a5c, #1d2936);   /* CLOSE = raised slate */
  border: 2px solid #0e1620; color: #ffffff;
  text-shadow: 0 0 3px rgba(0,0,0,0.9), 0 1px 2px rgba(0,0,0,0.8);  /* crisp white X, dark edge */
  box-shadow: 0 2px 5px rgba(0,0,0,0.55), inset 0 1px 0 rgba(255,255,255,0.18); }
button.bankon-close:hover { background-image: linear-gradient(to bottom, #4d6078, #2a3a4c);
  border-color: #00BFFF;
  box-shadow: 0 2px 7px rgba(0,191,255,0.45), inset 0 1px 0 rgba(255,255,255,0.25); }
button.ice { color: #04222e; font-weight: 700; font-size: 10px; padding: 1px 8px;
  background-image: linear-gradient(to bottom, #6fd8ff, #1899d6);   /* ICE = raised ice-blue */
  border: 1px solid #0c5c80; border-radius: 8px;
  box-shadow: 0 2px 5px rgba(0,0,0,0.55), inset 0 1px 0 rgba(255,255,255,0.35); }
button.ice:hover { background-image: linear-gradient(to bottom, #93e2ff, #2ab0ef); }
button.uninstall { background-image: none; background-color: transparent; color: #8a5a5a;
                   font-size: 10px; padding: 1px 8px; border: 1px solid #4a2a2c;
                   box-shadow: 0 1px 3px rgba(0,0,0,0.4); }
button.uninstall:hover { background-color: #c0262d; color: #fff; }
/* pressed-in look while the thing it starts is RUNNING - sinks BELOW the surface */
button.running, button.running:hover { background-image: none;
  box-shadow: inset 0 4px 10px rgba(0,0,0,0.75), inset 0 -1px 0 rgba(255,255,255,0.08),
              0 1px 2px rgba(0,0,0,0.3);
  background-color: #0a4a24; border-color: #06371b; color: #bfe8cd; }
button.core.running, button.core.running:hover { background-image: none;
  box-shadow: inset 0 3px 8px rgba(0,0,0,0.7), 0 1px 2px rgba(0,0,0,0.3);
  background-color: #8a500c; border-color: #5f3806; color: #ffd9a0; }
"""


def apply_style():
    prov = Gtk.CssProvider()
    prov.load_from_data(CSS)
    Gtk.StyleContext.add_provider_for_screen(
        Gdk.Screen.get_default(), prov, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)


BITCOIND = os.path.expanduser("~/bitcoin-31.0/bin/bitcoind")
BITCOIN_CLI = os.path.expanduser("~/bitcoin-31.0/bin/bitcoin-cli")


def _pids(pattern, exact=False):
    try:
        out = subprocess.run(["pgrep", "-x" if exact else "-f", pattern],
                             capture_output=True, text=True).stdout
        return [int(x) for x in out.split()]
    except Exception:
        return []


def bankon_pids():
    return _pids("bankon_qt.py")


def core_pids():
    return _pids("bitcoind", exact=True)


class Launcher:
    def __init__(self):
        apply_style()
        w = Gtk.Window(title="₿ANKON Launcher")
        w.set_border_width(18)
        w.set_default_size(340, 260)
        w.connect("destroy", Gtk.main_quit)

        self.win = w
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.vbox = box
        w.add(box)

        title = Gtk.Label()
        title.set_markup(f"<b><span foreground='{ORANGE}'>₿ANKON ₿TC</span></b> — "
                         f"the wallet you can <span foreground='{ORANGE}'>₿ANKON</span>")
        box.pack_start(title, False, False, 0)

        # START button with ✖ close INSIDE it; the red STOP sits OUTSIDE, right-adjacent,
        # at 1/7 of START's width. Both appear when ₿ANKON starts and persist while it runs.
        hrow = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.start = Gtk.Button()
        self.start.get_style_context().add_class("bankon")
        srow = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.start_lbl = Gtk.Label()
        self.start_lbl.set_markup(f"▶   START <span foreground='{ORANGE}' weight='800'>₿ANKON</span>")
        srow.pack_start(self.start_lbl, True, True, 0)
        self.close_btn = Gtk.Button(label="✖ close")  # stop ₿ANKON *and* close this launcher
        self.close_btn.get_style_context().add_class("bankon-close")
        self.close_btn.set_no_show_all(True)
        self.close_btn.set_tooltip_text("Stop ₿ANKON and close the launcher")
        self.close_btn.connect("clicked", self.on_close)
        srow.pack_end(self.close_btn, False, False, 0)
        self.start.add(srow)
        self.start.set_size_request(-1, 56)
        self.start.connect("clicked", self.on_start)
        hrow.pack_start(self.start, True, True, 0)
        self.stop = Gtk.Button(label="■\nstop")
        self.stop.get_style_context().add_class("bankon-stop")
        self.stop.set_no_show_all(True)               # hidden until ₿ANKON is running
        self.stop.set_tooltip_text("Stop ₿ANKON (launcher stays open)")
        self.stop.connect("clicked", self.on_stop)
        # keep STOP at 1/7 of START's actual width, same height
        self.start.connect("size-allocate",
                           lambda _w, a: self.stop.set_size_request(max(44, a.width // 7), 56))
        hrow.pack_start(self.stop, False, False, 0)
        box.pack_start(hrow, False, False, 0)

        # ₿itcoin Core — deliberately small (~10% the size of START), under the main button
        crow = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.core = Gtk.Button(label="⛓ core")
        self.core.get_style_context().add_class("core")
        self.core.set_size_request(90, 22)
        self.core.connect("clicked", self.on_core)
        crow.pack_start(self.core, False, False, 0)
        self.ice = Gtk.Button(label="🧊 ICE")
        self.ice.get_style_context().add_class("ice")
        self.ice.set_size_request(70, 22)
        self.ice.set_tooltip_text("Open the ICE controller — thermal + radio kill switch (AIRGAP)")
        self.ice.connect("clicked", self.on_ice)
        crow.pack_start(self.ice, False, False, 0)
        crow.pack_start(Gtk.Label(), True, True, 0)   # spacer keeps it left + small
        self.uninst = Gtk.Button(label="🗑 uninstall ₿ANKON")
        self.uninst.get_style_context().add_class("uninstall")
        self.uninst.connect("clicked", self.on_uninstall)
        crow.pack_end(self.uninst, False, False, 0)
        box.pack_start(crow, False, False, 0)

        self.status = Gtk.Label(label="…")
        box.pack_start(self.status, False, False, 0)

        # live log accordions — the actual logs, not pointers to them.
        # Packed WITHOUT expand: they claim window space only while open (see _on_expand).
        self.logx, self.logview = self._log_expander("📜 ₿ANKON logs (bankon.log)")
        box.pack_start(self.logx, False, False, 0)
        self.corelogx, self.corelogview = self._log_expander("⛓ ₿itcoin Core logs (debug.log)")
        box.pack_start(self.corelogx, False, False, 0)
        # transparency / brightness slider for BOTH log displays (orange fill + raised knob)
        self.fxrow = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.fxrow.pack_start(Gtk.Label(label="🔅"), False, False, 0)
        self.logfx = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 20, 100, 5)
        self.logfx.set_value(100); self.logfx.set_draw_value(True); self.logfx.set_value_pos(Gtk.PositionType.RIGHT)
        self.logfx.set_tooltip_text("Log opacity + brightness — slide left to fade the log panels")
        self.logfx.get_style_context().add_class("logfx")
        self.logfx.connect("value-changed", self._apply_logfx)
        self.fxrow.pack_start(self.logfx, True, True, 0)
        self.fxrow.pack_start(Gtk.Label(label="🔆"), False, False, 0)
        box.pack_start(self.fxrow, False, False, 0)

        # DOCK / CALL — window choreography between the ₿UTTON and the Overview
        self.wmrow = Gtk.Box(spacing=6)
        dk = Gtk.Button(label="⚓ DOCK")
        dk.set_tooltip_text("CLICK: dock the ₿UTTON onto the console's BOTTOM FOOTER "
                            "(centred on the © / banner strip).\n"
                            "PRESS AND HOLD: summon the console window to appear BEHIND the ₿UTTON.")
        dk.get_style_context().add_class("logmini")
        # two-mode gesture: short click vs press-and-hold (≥550 ms) — handled on press/release
        dk.connect("pressed", self._dock_pressed)
        dk.connect("released", self._dock_released)
        self.wmrow.pack_start(dk, True, True, 0)
        cl = Gtk.Button(label="📞 CALL")
        cl.set_tooltip_text("Bring the Overview + Console here: moves the ₿ANKON window to the ₿UTTON, "
                            "opens the web Console, then docks the ₿UTTON beside the Overview")
        cl.get_style_context().add_class("logmini"); cl.connect("clicked", self.on_call)
        self.wmrow.pack_start(cl, True, True, 0)
        box.pack_start(self.wmrow, False, False, 0)

        # VIEW switcher — visible in EVERY view (fullscreen always has a way back)
        vrow = Gtk.Box(spacing=4)
        vrow.pack_start(Gtk.Label(label="view"), False, False, 0)
        self._viewbtns = {}
        for txt, mode, tip in (("▁ small", "s", "Minimal choices only — the essential buttons"),
                               ("▢ regular", "r", "Buttons + collapsed log accordions"),
                               ("▤ medium", "m", "Extra medium — logs shown and expanding"),
                               ("⛶ full", "x", "Fullscreen — maximized with large logs")):
            b = Gtk.Button(label=txt)
            b.set_tooltip_text(tip)
            b.get_style_context().add_class("logmini")
            b.connect("clicked", lambda _b, mo=mode: self._set_view(mo))
            self._viewbtns[mode] = b
            vrow.pack_start(b, True, True, 0)
        box.pack_start(vrow, False, False, 0)

        cpr = Gtk.Label()
        cpr.set_markup("<span foreground='#5a6b7b' size='8000'>© 2026 ₿ANKON — all rights preserved</span>")
        box.pack_end(cpr, False, False, 0)

        w.show_all()
        self._apply_logfx(self.logfx)          # pin the dark log background from launch
        self._set_view("r")                    # regular is the launch view
        self.refresh()
        GLib.timeout_add(1500, self.refresh)

    def on_core(self, _b):
        if core_pids():
            # Core is running — this press means STOP (graceful bitcoin-cli stop)
            try:
                subprocess.Popen([BITCOIN_CLI, "stop"],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                self.status.set_text("Stopping ₿itcoin Core (graceful)…")
                self.corelogx.set_expanded(True)      # watch the shutdown in debug.log
            except Exception as e:
                self.status.set_text(f"Core stop failed: {e}")
            return
        try:
            subprocess.Popen([BITCOIND], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
            self.status.set_text("Starting ₿itcoin Core…")
            self.corelogx.set_expanded(True)          # watch the boot in debug.log
        except Exception as e:
            self.status.set_text(f"Core start failed: {e}")

    def on_start(self, _b):
        if bankon_pids():
            self.status.set_text("₿ANKON is already running.")
            return
        env = dict(os.environ)
        env.setdefault("QT_OPENGL", "software")
        try:
            with open(LOG, "w") as log:
                subprocess.Popen(["bash", APP], stdout=log, stderr=subprocess.STDOUT,
                                 env=env, start_new_session=True)
            self.status.set_text("Starting ₿ANKON…")
            self.logx.set_expanded(True)          # show the boot log as it happens
        except Exception as e:
            self.status.set_text(f"Failed: {e}")

    def on_stop(self, _b):
        pids = bankon_pids()
        for p in pids:
            try:
                os.kill(p, signal.SIGTERM)
            except Exception:
                pass
        self.status.set_text("Stopping…" if pids else "Not running.")

    def _apply_logfx(self, scale):
        # TRANSPARENCY is the effect; brightness is only a minor augment. The text colour stays in
        # a readable mid-grey band and the background is PINNED dark — never a white-out.
        frac = scale.get_value() / 100.0
        for v in (getattr(self, "logview", None), getattr(self, "corelogview", None)):
            if v is None:
                continue
            v.set_opacity(frac)                                   # primary: fade the whole panel
            lo, hi = 0x8a, 0xc8                                   # minor: dim grey → soft light grey
            g = int(lo + (hi - lo) * frac)
            css = ("textview, textview text {{ background-color: #0a0f16; }} "
                   "textview text {{ color: #{0:02x}{1:02x}{2:02x}; }}"
                   .format(g, min(255, g + 6), min(255, g + 12))).encode()
            prov = getattr(v, "_fxprov", None)
            if prov is None:
                prov = Gtk.CssProvider(); v.get_style_context().add_provider(prov, Gtk.STYLE_PROVIDER_PRIORITY_USER + 10)
                v._fxprov = prov
            try: prov.load_from_data(css)
            except Exception: pass
    def _log_expander(self, label):
        x = Gtk.Expander()
        x.set_label(label)
        view = Gtk.TextView(editable=False, cursor_visible=False, monospace=True)
        view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        sw = Gtk.ScrolledWindow()
        sw.set_min_content_height(160)
        sw.add(view)
        # toolbar inside the accordion: copy the log + three display sizes (S / M / ⛶ max)
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        bar = Gtk.Box(spacing=4)
        for txt, tip, cb in (("⛶", "Maximize the log display (and the window)", lambda _b, s=sw: self._log_size(s, "x")),
                             ("M", "Medium log display", lambda _b, s=sw: self._log_size(s, "m")),
                             ("S", "Regular small log display", lambda _b, s=sw: self._log_size(s, "s")),
                             ("⧉ Copy", "Copy the visible log to the clipboard", lambda _b, v=view: self._copy_log(v))):
            b = Gtk.Button(label=txt)
            b.set_tooltip_text(tip)
            b.get_style_context().add_class("logmini")
            b.connect("clicked", cb)
            bar.pack_end(b, False, False, 0)
        inner.pack_start(bar, False, False, 0)
        inner.pack_start(sw, True, True, 0)
        x.add(inner)
        x._sw = sw                      # view modes size the log panes through this
        x.connect("notify::expanded", self._on_expand)
        return x, view

    # ── window VIEW modes: small (minimal) · regular · extra medium (logs) · fullscreen ──
    def _set_view(self, mode):
        self._view = mode
        logs = (self.logx, self.corelogx)
        extras = (self.fxrow, self.wmrow)
        for m, b in self._viewbtns.items():
            ctx = b.get_style_context()
            (ctx.add_class if m == mode else ctx.remove_class)("viewon")
        if mode == "s":                 # minimal: the essential choices only
            for x in logs:
                x.set_expanded(False); x.hide()
            for w in extras:
                w.hide()
            self.win.unmaximize()
            GLib.idle_add(lambda: self.win.resize(1, 1) and False)
            self.status.set_text("view ▁ small — minimal choices.")
        elif mode == "r":               # regular: collapsed accordions + tools
            for x in logs:
                x.show(); x.set_expanded(False)
            for w in extras:
                w.show()
            self.win.unmaximize()
            GLib.idle_add(lambda: self.win.resize(1, 1) and False)
            self.status.set_text("view ▢ regular.")
        elif mode == "m":               # extra medium: the logs, live and expanding
            for x in logs:
                x.show(); self._log_size(x._sw, "m"); x.set_expanded(True)
            for w in extras:
                w.show()
            self.status.set_text("view ▤ extra medium — logs live; accordions expand.")
        else:                           # fullscreen — the view row stays: the way back
            for x in logs:
                x.show(); self._log_size(x._sw, "x"); x.set_expanded(True)
            for w in extras:
                w.show()
            self.status.set_text("view ⛶ fullscreen — ▤ / ▢ / ▁ bring it back down.")
        self.load_log()

    def _copy_log(self, view):
        buf = view.get_buffer()
        text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
        cb = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        cb.set_text(text, -1); cb.store()
        self.status.set_text(f"⧉ copied {len(text.splitlines())} log lines to the clipboard.")

    def _log_size(self, sw, mode):
        # S = regular small · M = extra medium · ⛶ = maximize (window grows too)
        if mode == "x":
            sw.set_min_content_height(560); self.win.maximize()
        else:
            sw.set_min_content_height(340 if mode == "m" else 160)
            self.win.unmaximize()
            GLib.idle_add(lambda: self.win.resize(1, 1) and False)   # shrink-to-fit

    # ── DOCK / CALL: window choreography with the ₿ANKON (Overview) window via wmctrl ──
    def _wm_list(self):
        if not shutil.which("wmctrl"):
            return []
        try:
            out = subprocess.run(["wmctrl", "-lG"], capture_output=True, text=True, timeout=5).stdout
        except Exception:
            return []
        wins = []
        for ln in out.splitlines():
            p = ln.split(None, 7)
            if len(p) >= 8:
                try:
                    wins.append({"id": p[0], "x": int(p[2]), "y": int(p[3]),
                                 "w": int(p[4]), "h": int(p[5]), "title": p[7]})
                except ValueError:
                    pass
        return wins

    def _find_overview(self):
        me = (self.win.get_title() or "").lower()
        best = None
        for w in self._wm_list():
            t = w["title"].lower()
            if "launcher" in t or t == me or ("bankon" not in t and "₿ankon" not in t):
                continue
            if "wallet as a service" in t:      # the Qt console's exact title — always preferred
                return w
            best = best or w                    # fallback: any other ₿ANKON window (browser tab etc.)
        return best

    def _dock_pos(self, qt):
        # DOCK position = the console's BOTTOM FOOTER: flush against the window's bottom
        # edge (the © status-bar / banner strip), horizontally centred on it — the ₿UTTON
        # parks ON the footer like a tug against a hull.
        bw, bh = self.win.get_size()
        return (max(0, qt["x"] + (qt["w"] - bw) // 2),
                max(0, qt["y"] + qt["h"] - bh - 4))

    # ── DOCK gesture: click = dock onto the banner · press-and-hold = summon console behind ──
    _DOCK_HOLD_MS = 550

    def _dock_pressed(self, _b):
        self._dock_held = False
        self._dock_timer = GLib.timeout_add(self._DOCK_HOLD_MS, self._dock_hold_fire)

    def _dock_hold_fire(self):
        self._dock_timer = None
        self._dock_held = True                 # swallow the click that follows this release
        self._summon_console_behind()
        return False

    def _dock_released(self, _b):
        if getattr(self, "_dock_timer", None):
            GLib.source_remove(self._dock_timer)
            self._dock_timer = None
        if not getattr(self, "_dock_held", False):
            self.on_dock(None)                 # short click → dock onto the banner field

    def _summon_console_behind(self):
        """Press-and-hold DOCK: bring the console window up BEHIND the ₿UTTON (deploying it
        first if it isn't running), keeping the ₿UTTON on top."""
        qt = self._find_overview()
        if qt is None:
            self.on_start(None)                # deploy; _await_console raises it behind + docks
            self.status.set_text("⚓ hold — deploying the console behind the ₿UTTON…")
            self._await_console()
            return
        subprocess.run(["wmctrl", "-i", "-a", qt["id"]], check=False)   # raise the console…
        self.win.present()                                              # …₿UTTON back on top
        self.status.set_text("⚓ hold — console summoned behind the ₿UTTON. Click DOCK to park on its banner.")

    def on_dock(self, _b):
        qt = self._find_overview()
        if qt:
            x, y = self._dock_pos(qt)
            self.win.move(x, y)
            self.win.present()                             # ₿UTTON floats above the console
            self.status.set_text("⚓ ₿UTTON docked — on the console's bottom footer.")
        else:
            scr = Gdk.Screen.get_default()
            w, h = self.win.get_size()
            self.win.move(max(0, (scr.get_width() - w) // 2), max(0, scr.get_height() - h - 48))
            self.status.set_text("⚓ Overview not found — docked to the screen's bottom footer instead.")

    def on_call(self, _b):
        bx, by = self.win.get_position()
        bw, _bh = self.win.get_size()
        qt = self._find_overview()
        if qt is None:
            # DEPLOY from the ₿UTTON: start the console; when its window appears it opens BEHIND
            # the ₿UTTON, and the ₿UTTON snaps to the DOCK position on top of it.
            self.on_start(None)
            self.status.set_text("📞 CALL — deploying the console behind the ₿UTTON…")
            self._await_console()
            return
        # bring the console TO the ₿UTTON: position it so the ₿UTTON sits at its right-hand side
        # (the DOCK relation), raise it, then put the ₿UTTON back on top.
        qx = max(0, bx + bw + 18 - qt["w"])
        qy = max(0, by - 42)
        subprocess.run(["wmctrl", "-i", "-r", qt["id"], "-e", f"0,{qx},{qy},-1,-1"], check=False)
        subprocess.run(["wmctrl", "-i", "-a", qt["id"]], check=False)
        self.win.present()                                 # console behind, ₿UTTON in front
        self.status.set_text("📞 console called to the ₿UTTON — ₿UTTON docked on its right-hand side.")

    def _await_console(self, tries=30):
        # poll for the console window after a CALL-deploy; when it appears: console behind,
        # ₿UTTON moved to the DOCK position and presented on top.
        self._await_left = tries

        def _poll():
            qt = self._find_overview()
            if qt:
                subprocess.run(["wmctrl", "-i", "-a", qt["id"]], check=False)   # raise the console…
                x, y = self._dock_pos(qt)
                self.win.move(x, y)
                self.win.present()                                             # …₿UTTON on top, docked
                self.status.set_text("📞 console deployed behind the ₿UTTON — ₿UTTON in the DOCK position.")
                return False
            self._await_left -= 1
            if self._await_left <= 0:
                self.status.set_text("console window did not appear — press DOCK once it is up.")
                return False
            return True
        GLib.timeout_add(1000, _poll)

    def _on_expand(self, x, *_):
        # give an OPEN accordion the window's spare space; on close, fully compress
        # and hand the reclaimed space back to the display (shrink-to-fit)
        self.vbox.set_child_packing(x, x.get_expanded(), x.get_expanded(),
                                    0, Gtk.PackType.START)
        if not x.get_expanded():
            GLib.idle_add(lambda: self.win.resize(1, 1) and False)   # snaps to natural size
        self.load_log()

    @staticmethod
    def _tail(path, max_bytes=32768, lines=150):
        # read only the end of the file — debug.log can be tens of MB
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - max_bytes))
            data = f.read().decode("utf-8", errors="replace")
        return "\n".join(data.splitlines()[-lines:])

    def _fill(self, view, path, empty_hint):
        try:
            text = self._tail(path) or "(log is empty)"
        except Exception:
            text = empty_hint
        buf = view.get_buffer()
        if buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False) != text:
            buf.set_text(text)
            GLib.idle_add(lambda: view.scroll_to_iter(               # keep tailing the end
                buf.get_end_iter(), 0.0, False, 0.0, 1.0) and False)

    def load_log(self):
        if self.logx.get_expanded():
            self._fill(self.logview, LOG, "(no log yet — press START)")
        if self.corelogx.get_expanded():
            self._fill(self.corelogview, os.path.expanduser("~/.bitcoin/debug.log"),
                       "(no debug.log — has ₿itcoin Core ever run here?)")

    @staticmethod
    def _pressed(btn, on):
        ctx = btn.get_style_context()
        (ctx.add_class if on else ctx.remove_class)("running")

    def on_close(self, _b):
        self.on_stop(_b)                  # stop ₿ANKON first…
        GLib.timeout_add(300, Gtk.main_quit)   # …then close the launcher (let SIGTERM land)

    ICE_APP = os.path.expanduser("~/ICE/ice.py")

    def on_ice(self, _b):
        import shutil
        if not os.path.exists(self.ICE_APP):
            self.status.set_text("ICE app not found at ~/ICE/ice.py")
            return
        term = (shutil.which("x-terminal-emulator") or shutil.which("gnome-terminal")
                or shutil.which("xterm"))
        try:
            subprocess.Popen([term, "-e", self.ICE_APP] if term else [self.ICE_APP],
                             start_new_session=True)
            self.status.set_text("Opening ICE controller…")
        except Exception as e:
            self.status.set_text(f"ICE launch failed: {e}")

    def on_uninstall(self, _b):
        d = Gtk.MessageDialog(message_type=Gtk.MessageType.WARNING,
                              buttons=Gtk.ButtonsType.OK_CANCEL,
                              text="Completely uninstall ₿ANKON?")
        d.format_secondary_text(
            "This stops every ₿ANKON process and deletes ~/bankon-tools\n"
            "(code, services, node_modules, logs, desktop entries).\n\n"
            "₿itcoin Core, the blockchain, bitcoin.conf and ALL wallets\n"
            "are NOT touched — the node keeps running.\n\n"
            "This cannot be undone.")
        resp = d.run(); d.destroy()
        if resp == Gtk.ResponseType.OK:
            subprocess.Popen(
                ["bash", os.path.expanduser("~/bankon-tools/bankon-uninstall.sh")],
                start_new_session=True)
            Gtk.main_quit()

    def refresh(self, *_):
        pids = bankon_pids()
        cpids = core_pids()
        # buttons LOOK PRESSED while the thing they started is running
        self._pressed(self.start, bool(pids))
        self._pressed(self.core, bool(cpids))
        self.core.get_child().set_text("⛓ core ● stop" if cpids else "⛓ core ▶ start")
        self.core.set_tooltip_text("₿itcoin Core is running — press for graceful stop" if cpids
                                   else "Start bitcoind")
        if pids:                                          # START shows pressed RUNNING state
            self.start_lbl.set_markup(
                f"●   <span foreground='{ORANGE}' weight='800'>₿ANKON</span> RUNNING")
            self.stop.show()                              # the inner stop appears and persists
            self.close_btn.show()                         # …accompanied by ✕ close
        else:
            self.start_lbl.set_markup(
                f"▶   START <span foreground='{ORANGE}' weight='800'>₿ANKON</span>")
            self.stop.hide()
            self.close_btn.hide()
        parts = []
        parts.append(f"<span foreground='{ORANGE}'>● Core running</span> (pid {cpids[0]})" if cpids
                     else "<span foreground='#888'>○ Core stopped</span>")
        parts.append(f"<span foreground='{ORANGE}'>● ₿ANKON running</span> (pid {pids[0]})" if pids
                     else "<span foreground='#888'>○ ₿ANKON stopped</span>")
        self.status.set_markup("   ·   ".join(parts))
        self.stop.set_sensitive(bool(pids))
        self.load_log()                                   # live-tail while the accordion is open
        return True


if __name__ == "__main__":
    Launcher()
    Gtk.main()
