#!/usr/bin/env bash
# bankon-qt.sh — launch the BANKON Qt diagnostics UI.
# Handles the one-time PySide6 install and forces SOFTWARE rendering, because
# this machine's Intel HD 3000 + Mesa black-screens Qt's GL path (the reason we
# moved the node itself off bitcoin-qt to headless bitcoind).
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PATH="$HOME/bitcoin-31.0/bin:$PATH"
# Operator config (rage handoff target, BANKON_CONSOLE_URL, …) — auto-exported.
[ -f "$HERE/bankon.env" ] && { set -a; . "$HERE/bankon.env"; set +a; }

if ! python3 -c "import PySide6" >/dev/null 2>&1; then
  echo "PySide6 not found — installing (one-time, ~150 MB)…"
  python3 -m pip install --user pyside6 || { echo "pip install failed. Install manually: pip install --user pyside6"; exit 1; }
fi

# Software rendering — the fix for the HD 3000 black-screen.
# IMPORTANT: QT_OPENGL=software ALONE works and loads fast. Do NOT add
# LIBGL_ALWAYS_SOFTWARE=1 or QT_XCB_FORCE_SOFTWARE_OPENGL=1 — on this HD 3000 they
# force a GL path that hangs and the window never appears (verified June 2026).
export QT_OPENGL=software
exec python3 "$HERE/bankon-qt/bankon_qt.py" "$@"
