#!/usr/bin/env bash
#
# bankon.sh — the BANKON INSTALLER. One command to make a clean machine ready to run BANKON:
# prerequisites → Bitcoin Core (verified) → WaaS/Console node deps → Qt (PySide6) → QR + optional
# systemd units → a doctor check. Idempotent (skips what's already present), honest (prints every
# action; --dry-run shows the plan), and non-destructive.
#
# It ORCHESTRATES the existing pieces rather than duplicating them: Core install reuses
# `./bankon install-core` (SHA256-verified); node deps reuse `npm install`; the launcher stays
# `bankon-qt/bankon.sh`. This is the "install" half; `./bankon up` is the "run" half.
#
# Usage:
#   ./bankon.sh                      # full install (all components), interactive-safe
#   ./bankon.sh --only core,waas     # just those components
#   ./bankon.sh --dry-run            # print the plan, change nothing
#   ./bankon.sh --yes                # assume yes (no prompts) — for scripts/CI
#   ./bankon.sh --help
#
# Components:  prereqs · core · waas · console · qt · qr · units   (default: all except units)
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

# ---- pretty ----
c(){ [ -t 1 ] && printf '\033[%sm%s\033[0m' "$1" "$2" || printf '%s' "$2"; }
ok(){   printf '  %s %s\n' "$(c '1;32' '✓')" "$*"; }
warn(){ printf '  %s %s\n' "$(c '1;33' '!')" "$*"; }
err(){  printf '  %s %s\n' "$(c '1;31' '✗')" "$*" >&2; }
step(){ printf '\n%s %s\n' "$(c '1;36' '▶')" "$*"; }

DRY=0; YES=0; ONLY=""
while [ $# -gt 0 ]; do case "$1" in
  --dry-run) DRY=1; shift ;;
  --yes|-y)  YES=1; shift ;;
  --only)    ONLY="$2"; shift 2 ;;
  -h|--help) sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
  *) err "unknown arg: $1  (see --help)"; exit 1 ;;
esac; done

# default component set (units are opt-in)
COMPONENTS="${ONLY:-prereqs,core,waas,console,qt,qr,dexy}"
want(){ case ",$COMPONENTS," in *",$1,"*) return 0 ;; *) return 1 ;; esac; }
run(){ if [ "$DRY" = 1 ]; then printf '    %s %s\n' "$(c '2' 'would run:')" "$*"; else eval "$@"; fi; }

# OS package manager (Debian/Alpine/OpenBSD — the bankonOS seed targets) + macOS brew
PKG=""; PKG_INSTALL=""
if   command -v apt-get >/dev/null; then PKG=apt;     PKG_INSTALL="sudo apt-get install -y";
elif command -v apk     >/dev/null; then PKG=apk;     PKG_INSTALL="doas apk add";
elif command -v pkg_add >/dev/null; then PKG=pkg_add; PKG_INSTALL="doas pkg_add";
elif command -v brew    >/dev/null; then PKG=brew;    PKG_INSTALL="brew install";
fi
pkgname(){ # per-OS package name map
  case "$1:$PKG" in
    node:apk) echo nodejs ;; node:*) echo nodejs ;;
    npm:apk) echo npm ;; npm:pkg_add) echo "" ;; npm:*) echo npm ;;
    python:apk) echo python3 ;; python:pkg_add) echo python3 ;; python:*) echo python3 ;;
    pip:apk) echo py3-pip ;; pip:*) echo python3-pip ;;
    qrencode:*) echo qrencode ;;
    curl:*) echo curl ;;
    *) echo "$1" ;;
  esac
}
need_pkg(){ local bin="$1" pk; pk="$(pkgname "$2")"; command -v "$bin" >/dev/null && { ok "$bin present"; return 0; }
  [ -z "$PKG" ] && { warn "$bin missing and no known package manager — install it manually"; return 1; }
  [ -z "$pk" ] && { ok "$bin comes with node"; return 0; }
  warn "$bin missing → installing $pk via $PKG"; run "$PKG_INSTALL $pk"; }

printf '%s\n' "$(c '1;36' '╔══ BANKON installer ══╗')"
printf '  target OS pkg: %s   components: %s   %s\n' "${PKG:-none}" "$COMPONENTS" "$([ "$DRY" = 1 ] && c '1;33' '(dry-run)')"

# ── prereqs ───────────────────────────────────────────────────────────────────
if want prereqs; then
  step "Prerequisites (node · npm · python3 · pip · curl)"
  need_pkg curl curl
  need_pkg node node
  need_pkg npm npm
  need_pkg python3 python
  command -v pip3 >/dev/null || need_pkg pip3 pip || true
fi

# ── Bitcoin Core (verified) ────────────────────────────────────────────────────
if want core; then
  step "Bitcoin Core v31 (SHA256-verified via ./bankon install-core)"
  if command -v bitcoind >/dev/null && bitcoind --version 2>/dev/null | grep -q "31.0"; then
    ok "Bitcoin Core v31 already installed"
  else
    run "./bankon install-core"
  fi
fi

# ── WaaS + Console node deps ───────────────────────────────────────────────────
for comp in waas console dexy; do
  want "$comp" || continue
  dir="bankon-$comp"; [ "$comp" = dexy ] && dir="dexy"   # DEXY lives at repo-root dexy/
  step "Node deps — $dir"
  if [ -d "$dir/node_modules" ]; then ok "$dir/node_modules present"
  elif [ -f "$dir/package.json" ]; then run "( cd '$dir' && npm install --no-audit --no-fund )"
  else warn "$dir has no package.json — skipping"; fi
done

# ── Qt UI (PySide6) ────────────────────────────────────────────────────────────
if want qt; then
  step "Qt UI — PySide6 (software-rendered; the bankon-qt.sh launcher also self-installs it)"
  if python3 -c "import PySide6" >/dev/null 2>&1; then ok "PySide6 present"
  else run "python3 -m pip install --user pyside6"; fi
fi

# ── QR (receive-from-QR) ───────────────────────────────────────────────────────
if want qr; then
  step "QR — qrencode (for /api/wallet/:name/qr receive)"
  need_pkg qrencode qrencode || warn "receive-from-QR degrades to the BIP21 URI without qrencode"
fi

# ── systemd units (opt-in) ─────────────────────────────────────────────────────
if want units; then
  step "systemd user units"
  if [ -x "systemd/install-units.sh" ]; then run "./systemd/install-units.sh"
  else warn "systemd/install-units.sh not found/executable — skipping"; fi
fi

# ── doctor ─────────────────────────────────────────────────────────────────────
if [ "$DRY" = 1 ]; then
  printf '\n%s dry-run complete — re-run without --dry-run to install.\n' "$(c '1;33' '●')"
  exit 0
fi
step "Environment check (./bankon doctor)"
./bankon doctor || true

printf '\n%s\n' "$(c '1;32' 'BANKON installed.')"
echo "  next:  ./bankon up          # start Core (if needed) + WaaS + Console"
echo "         ./bankon-qt.sh       # the native Qt diagnostics UI"
echo "         ./bankon doctor      # re-check anytime"
