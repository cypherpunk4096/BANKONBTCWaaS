#!/bin/sh
# bankonOS installer — the sovereign Bitcoin workstation, provisioned cleanly.
# Rewrite of the 2024 ad-hoc scripts: POSIX sh (ash/OpenBSD-sh/bash), MULTI-OS (favours Alpine and
# OpenBSD, with Debian/Ubuntu compatibility), modular, idempotent, verified — no GUI editors, no
# hardcoded single-version downloads, no `curl|bash`, no secrets.
#
#   sh install.sh                       # detect OS, choose components interactively
#   sh install.sh --only base,bitcoin,bankon --yes
#   sh install.sh --os alpine --dry-run
#   COMPONENTS="base dev bitcoin bankon harden" sh install.sh --yes
#
# Components:  base · dev · bitcoin · bankon · ai · harden        (see component_* functions)
set -eu

VERSION="2.0.0"
DRY=0; YES=0; ONLY=""; OS_FORCE=""
BANKON_DIR="${BANKON_DIR:-$HOME/bankon-tools}"

# ── tiny UI ──────────────────────────────────────────────────────────────────
esc() { printf '\033[38;5;208m'; }; rst() { printf '\033[0m'; }
say()  { esc; printf '▸ %s\n' "$*"; rst; }
warn() { printf 'WARN: %s\n' "$*" >&2; }
die()  { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }
run()  { if [ "$DRY" = 1 ]; then printf '   [dry-run] %s\n' "$*"; else eval "$@"; fi; }

# ── args ─────────────────────────────────────────────────────────────────────
while [ $# -gt 0 ]; do
  case "$1" in
    --only) ONLY="$2"; shift 2 ;;
    --only=*) ONLY="${1#*=}"; shift ;;
    --os) OS_FORCE="$2"; shift 2 ;;
    --os=*) OS_FORCE="${1#*=}"; shift ;;
    --dry-run) DRY=1; shift ;;
    --yes|-y) YES=1; shift ;;
    --version) echo "bankonOS installer $VERSION"; exit 0 ;;
    -h|--help) sed -n '2,17p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "unknown arg: $1 (try --help)" ;;
  esac
done

# ── OS + package-manager abstraction (Alpine/OpenBSD favoured, Debian-compatible) ──
detect_os() {
  [ -n "$OS_FORCE" ] && { echo "$OS_FORCE"; return; }
  if [ -f /etc/alpine-release ]; then echo alpine
  elif [ "$(uname -s)" = OpenBSD ]; then echo openbsd
  elif [ -f /etc/debian_version ]; then echo debian
  elif have apk; then echo alpine
  elif have pkg_add; then echo openbsd
  elif have apt-get; then echo debian
  else echo unknown; fi
}
OS="$(detect_os)"

SUDO=""
[ "$(id -u)" != 0 ] && have sudo && SUDO="sudo"
[ "$(id -u)" != 0 ] && [ -z "$SUDO" ] && [ "$OS" = openbsd ] && have doas && SUDO="doas"

pkg_update() {
  case "$OS" in
    alpine)  run "$SUDO apk update" ;;
    openbsd) : ;;                                   # pkg_add resolves from PKG_PATH; no explicit update
    debian)  run "$SUDO apt-get update -y" ;;
  esac
}
pkg_install() {   # pkg_install pkg1 pkg2 …  (names are already OS-appropriate via pkgname())
  [ -z "$*" ] && return 0
  case "$OS" in
    alpine)  run "$SUDO apk add --no-cache $*" ;;
    openbsd) run "$SUDO pkg_add -I $*" ;;
    debian)  run "$SUDO DEBIAN_FRONTEND=noninteractive apt-get install -y $*" ;;
    *) die "unsupported OS '$OS' — install these manually: $*" ;;
  esac
}
# map a generic tool → the package name on this OS (only where they differ)
pkgname() {
  case "$1:$OS" in
    python:alpine) echo "python3 py3-pip" ;;
    python:openbsd) echo "python3" ;;
    python:debian) echo "python3 python3-pip python3-venv" ;;
    build:alpine) echo "build-base" ;;
    build:openbsd) echo "gmake" ;;
    build:debian) echo "build-essential" ;;
    node:alpine) echo "nodejs npm" ;;
    node:*) echo "nodejs npm" ;;
    go:openbsd) echo "go" ;;
    go:*) echo "go golang" ;;
    rust:*) echo "rust cargo" ;;
    *) echo "$1" ;;   # same name everywhere (git, curl, tmux, gnupg, ...)
  esac
}

confirm() {
  [ "$YES" = 1 ] && return 0
  printf '   proceed with [%s]? [y/N] ' "$1"; read -r a; [ "$a" = y ] || [ "$a" = Y ]
}

# ── components (each idempotent; skips what's already present) ─────────────────
component_base() {
  say "base — core utilities (git, curl, gnupg, tmux, jq)"
  pkg_update
  # shellcheck disable=SC2046
  pkg_install $(pkgname git) $(pkgname curl) $(pkgname gnupg) tmux jq ca-certificates
  pkg_install $(pkgname python)
}

component_dev() {
  say "dev — Go, Node, Rust, build toolchain (the sovereign dev workstation)"
  # shellcheck disable=SC2046
  pkg_install $(pkgname build)
  have go   || pkg_install $(pkgname go)
  have node || pkg_install $(pkgname node)
  have cargo|| pkg_install $(pkgname rust)
}

component_bitcoin() {
  say "bitcoin — Bitcoin Core (via BANKON's verified installer if present, else package)"
  if [ -x "$BANKON_DIR/bankon" ]; then
    run "\"$BANKON_DIR/bankon\" install-core"       # SHA256SUMS-verified official tarball
  elif [ "$OS" = openbsd ]; then
    pkg_install bitcoin
  elif [ "$OS" = alpine ]; then
    pkg_install bitcoin || warn "bitcoin not in apk main — use BANKON's install-core or build from source"
  else
    warn "clone github.com/cypherpunk2048/bankon-tools and run: ./bankon install-core"
  fi
}

component_bankon() {
  say "bankon — the sovereign stack (bankon-tools + bankon-vault + ICE)"
  if [ ! -d "$BANKON_DIR" ]; then
    have git || pkg_install $(pkgname git)
    run "git clone https://github.com/cypherpunk2048/bankon-tools \"$BANKON_DIR\""
  else
    say "  bankon-tools present at $BANKON_DIR"
  fi
  [ -f "$BANKON_DIR/bankon-vault/install.sh" ] && run "sh \"$BANKON_DIR/bankon-vault/install.sh\"" || true
  say "  ICE (thermal/airgap/firewall + BANKON_VAULT frozen storage): $BANKON_DIR/../ICE or ~/ICE"
}

component_ai() {
  say "ai — local LLM inference (ollama)"
  if have ollama; then say "  ollama present"; return; fi
  if [ "$OS" = openbsd ]; then warn "ollama has no OpenBSD build — skip (use a Linux box for inference)"; return; fi
  run "curl -fsSL https://ollama.com/install.sh -o /tmp/ollama.install"
  say "  fetched official ollama installer to /tmp/ollama.install — review, then: sh /tmp/ollama.install"
}

component_harden() {
  say "harden — sovereign posture (firewall + no swap-to-disk for key ops)"
  case "$OS" in
    alpine)  pkg_install iptables awall || true ;;
    openbsd) say "  OpenBSD pf is built-in — enable /etc/pf.conf rules manually" ;;
    debian)  pkg_install ufw || true; run "$SUDO ufw --force enable" || true ;;
  esac
  say "  for key generation/signing: run air-gapped (ICE AIRGAP) and 'swapoff -a' — see bankon-vault SECURITY.md"
}

# ── run selected components ────────────────────────────────────────────────────
ALL="base dev bitcoin bankon ai harden"
SEL="${ONLY:-${COMPONENTS:-}}"
if [ -z "$SEL" ]; then
  if [ "$YES" = 1 ]; then SEL="base bitcoin bankon harden"       # sane default set
  else
    say "components: $ALL"
    printf '   which to install? [base bitcoin bankon harden] : '; read -r SEL
    [ -z "$SEL" ] && SEL="base bitcoin bankon harden"
  fi
fi
SEL="$(echo "$SEL" | tr ',' ' ')"

say "bankonOS $VERSION · OS=$OS · pkgmgr=$( [ "$OS" = alpine ] && echo apk || { [ "$OS" = openbsd ] && echo pkg_add || echo apt; } ) · components: $SEL"
[ "$OS" = unknown ] && die "unsupported OS — bankonOS favours Alpine/OpenBSD, works on Debian/Ubuntu"
confirm "$SEL" || die "aborted"

for c in $SEL; do
  case " $ALL " in *" $c "*) "component_$c" ;; *) warn "unknown component '$c' — skipped" ;; esac
done

esc; printf '\n✓ bankonOS provisioning complete (%s). Sovereign, verifiable, yours.\n' "$SEL"; rst
[ "$DRY" = 1 ] && say "(dry-run — nothing was changed)"
