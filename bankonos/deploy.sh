#!/bin/sh
# deploy.sh — the bankonOS orchestrator. One entry point for the whole family: pick an OS foundation
# (bankonAlpine / bankonDebian / bankonBSD) and a role (vault / node), and it dispatches to the right
# base + crypto scripts. Separates concerns (OS layer vs crypto layer) but drives them together.
#
#   sh deploy.sh <alpine|debian|bsd|auto> <vault|node> [--dry-run] [--yes] [COINS=...]
#
#   sh deploy.sh alpine vault            # build the Alpine amnesic signing enclave (ISO)
#   sh deploy.sh debian node             # provision a persistent Bitcoin node on Debian
#   sh deploy.sh bsd    vault            # provision cryptoBSD vault on a booted FuguIta
#   sh deploy.sh auto   node --yes       # detect this OS, run the node setup
#
# Roles:
#   vault → amnesic air-gapped signing enclave (build an image, or provision a live system)
#   node  → persistent Bitcoin-Core + other-coin daemons on the OS
set -eu
HERE="$(cd "$(dirname "$0")" && pwd)"
OS="${1:-}"; ROLE="${2:-}"; if [ $# -ge 2 ]; then shift 2; else set --; fi
DRY=0; YES=0; PASS=""
for a in "$@"; do
  case "$a" in
    --dry-run) DRY=1 ;;
    --yes|-y)  YES=1 ;;
    COINS=*)   PASS="$PASS $a" ;;
    *) echo "unknown arg: $a" >&2; exit 1 ;;
  esac
done
say()  { printf '\033[38;5;208m▸ %s\033[0m\n' "$*"; }
warn() { printf 'WARN: %s\n' "$*" >&2; }
die()  { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
run()  { if [ "$DRY" = 1 ]; then printf '   [dry-run] %s\n' "$*"; else eval "$@"; fi; }

usage() {
  sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
  exit "${1:-0}"
}
if [ -z "$OS" ] || [ -z "$ROLE" ]; then usage 1; fi
case "$OS" in alpine|debian|bsd|auto) ;; *) die "OS must be alpine|debian|bsd|auto (got '$OS')" ;; esac
case "$ROLE" in vault|node) ;; *) die "role must be vault|node (got '$ROLE')" ;; esac

# auto → detect the OS we're standing on
if [ "$OS" = auto ]; then
  if   [ -f /etc/alpine-release ] || command -v apk >/dev/null 2>&1; then OS=alpine
  elif [ "$(uname -s)" = OpenBSD ]; then OS=bsd
  elif [ -f /etc/debian_version ] || command -v apt-get >/dev/null 2>&1; then OS=debian
  else die "could not auto-detect OS — pass alpine|debian|bsd explicitly"; fi
  say "auto-detected OS: $OS"
fi

label() { case "$1" in alpine) echo bankonAlpine/cryptoAlpine;; debian) echo bankonDebian/cryptoDebian;; bsd) echo bankonBSD/cryptoBSD;; esac; }
say "bankonOS deploy · $(label "$OS") · role=$ROLE${PASS:+ ·$PASS}${DRY:+ · dry-run}"

# ── VAULT role: build an amnesic signing-enclave IMAGE (or provision a live system for bsd) ──
deploy_vault() {
  case "$OS" in
    alpine)
      say "vault → build the Alpine signing-enclave ISO (rootless podman)"
      if command -v podman >/dev/null 2>&1; then run "sh \"$HERE/enclave/podman-build.sh\""
      else warn "podman not found — on an Alpine host run: sh $HERE/enclave/build.sh"; fi ;;
    debian)
      say "vault → build the Debian toram live-ISO enclave"
      run "sh \"$HERE/enclave/debian/build.sh\"" ;;
    bsd)
      say "vault → provision cryptoBSD vault on a booted FuguIta (mode 2, amnesic)"
      [ "$(uname -s)" = OpenBSD ] || { warn "run this INSIDE a booted FuguIta; see cryptobsd/GUIDE.md"; return 0; }
      run "doas sh \"$HERE/cryptobsd/cryptobsd.sh\" vault" ;;
  esac
  say "  enclave is air-gapped + amnesic: import a seed offline, sign USB PSBTs, power-off = amnesia."
}

# ── NODE role: provision the persistent Bitcoin-Core + coins foundation on this OS ──
deploy_node() {
  case "$OS" in
    alpine) SCRIPT="$HERE/cryptoalpine/node-setup.sh"; SU="doas" ;;
    debian) SCRIPT="$HERE/cryptodebian/node-setup.sh"; SU="sudo" ;;
    bsd)    SCRIPT="$HERE/cryptobsd/node-setup.sh";     SU="doas" ;;
  esac
  # node provisioning must run ON the target OS
  case "$OS" in
    alpine) command -v apk    >/dev/null 2>&1 || die "run the node role ON bankonAlpine (apk missing)" ;;
    debian) command -v apt-get>/dev/null 2>&1 || die "run the node role ON bankonDebian (apt missing)" ;;
    bsd)    [ "$(uname -s)" = OpenBSD ] || die "run the node role ON cryptoBSD/FuguIta (not OpenBSD here)" ;;
  esac
  say "node → $SCRIPT"
  run "$( [ "$(id -u)" = 0 ] && echo '' || echo "$SU" ) env${PASS:+ $(echo "$PASS" | sed 's/^ //')} sh \"$SCRIPT\""
  say "  loopback-RPC node up; attach BANKON read-only Console/WaaS to :8332."
}

if [ "$ROLE" = vault ]; then deploy_vault; else deploy_node; fi
say "deploy complete."
