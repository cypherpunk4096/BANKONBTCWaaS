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
. "$HERE/lib/log.sh"                         # 3-level logging (BANKON_LOG 0/1/2) + file log
say() { log_info "$@"; }; warn() { log_warn "$@"; }; run() { log_run "$@"; }
DRY=0; YES=0; PASS=""; VERIFY=0

# first two positionals are OS + ROLE (unless it's a bare flag like --verify/--help)
OS=""; ROLE=""
_want_log=0
for a in "$@"; do
  if [ "$_want_log" = 1 ]; then log_setfile "$a"; _want_log=0; continue; fi
  if log_parse_flag "$a"; then continue; elif [ $? = 2 ]; then _want_log=1; continue; fi
  case "$a" in
    --dry-run) DRY=1 ;;
    --yes|-y)  YES=1 ;;
    --verify)  VERIFY=1 ;;
    -h|--help) OS=""; ROLE=""; break ;;
    COINS=*)   PASS="$PASS $a" ;;
    -*)        die "unknown flag: $a" ;;
    *) if [ -z "$OS" ]; then OS="$a"; elif [ -z "$ROLE" ]; then ROLE="$a"; else die "unexpected arg: $a"; fi ;;
  esac
done

usage() {
  sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
  echo "  --verify              self-test: every os×role path (script exists, POSIX-valid, dispatches)"
  echo "  --quiet|--verbose|--debug   logging level (0/1/2)   --log FILE   append a timestamped audit log"
  exit "${1:-0}"
}

# ── --verify: dry-run/validate every OS×role dispatch without touching the system ──
verify_all() {
  say "bankonOS deploy --verify · checking every os×role path"
  fails=0; checks=0
  for os in alpine debian bsd; do
    # vault targets (image builders — safe to introspect anywhere)
    case "$os" in
      alpine) vs="$HERE/enclave/podman-build.sh $HERE/enclave/build.sh $HERE/enclave/genapkovl-bankon-enclave.sh" ;;
      debian) vs="$HERE/enclave/debian/build.sh" ;;
      bsd)    vs="$HERE/cryptobsd/cryptobsd.sh" ;;
    esac
    for s in $vs; do
      checks=$((checks+1))
      if [ ! -f "$s" ]; then log_error "MISSING vault script: $s"; fails=$((fails+1))
      elif ! sh -n "$s" 2>/dev/null; then log_error "SYNTAX FAIL: $s"; fails=$((fails+1))
      else log_debug "ok vault[$os]: $s"; fi
    done
    # node target
    case "$os" in alpine) ns="$HERE/cryptoalpine/node-setup.sh";; debian) ns="$HERE/cryptodebian/node-setup.sh";; bsd) ns="$HERE/cryptobsd/node-setup.sh";; esac
    checks=$((checks+1))
    if [ ! -f "$ns" ]; then log_error "MISSING node script: $ns"; fails=$((fails+1))
    elif ! sh -n "$ns" 2>/dev/null; then log_error "SYNTAX FAIL: $ns"; fails=$((fails+1))
    else log_debug "ok node[$os]: $ns"; fi
    log_info "  $os: vault + node scripts present & POSIX-valid"
  done
  # dry-run the dispatcher itself for the buildable (vault) paths
  for os in alpine debian; do
    checks=$((checks+1))
    if DRY=1 YES=1 BANKON_LOG=0 sh "$HERE/deploy.sh" "$os" vault --dry-run --yes >/dev/null 2>&1; then log_debug "dispatch ok: $os vault"
    else log_error "DISPATCH FAIL: $os vault"; fails=$((fails+1)); fi
  done
  if [ "$fails" = 0 ]; then say "✓ verify PASSED — $checks checks, 0 failures"; exit 0
  else log_error "✗ verify FAILED — $fails/$checks checks failed"; exit 1; fi
}
[ "$VERIFY" = 1 ] && verify_all

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
