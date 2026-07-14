#!/bin/sh
# cryptobsd.sh — provision a booted FuguIta into cryptoBSD (the bankonOS OpenBSD build). One image,
# two roles, mapped onto FuguIta's own operating modes:
#
#   vault  → FuguIta mode 2 (RAM-only, AMNESIC) — the air-gapped signing enclave / vault builder
#   node   → FuguIta mode 3 (encrypted persistent) — the durable Bitcoin-Core + multi-crypto node
#
# Run INSIDE a booted FuguIta:  doas sh cryptobsd.sh vault   (or)   doas sh cryptobsd.sh node
# See GUIDE.md for the from-scratch walkthrough (build the FuguIta USB, apply noasks, first boot).
set -eu
# 3-level logging (shared lib if reachable; inline fallback keeps the script standalone)
_ld="$(cd "$(dirname "$0")" 2>/dev/null && pwd || echo .)"
for _p in "$_ld/lib/log.sh" "$_ld/../lib/log.sh" "$_ld/../../lib/log.sh"; do [ -r "$_p" ] && { . "$_p"; break; }; done
if ! command -v log_info >/dev/null 2>&1; then
  : "${BANKON_LOG:=1}"
  log_info(){ [ "${BANKON_LOG:-1}" -ge 1 ] && printf "\033[38;5;208m▸ %s\033[0m\n" "$*"; return 0; }
  log_warn(){ printf "WARN: %s\n" "$*" >&2; }
  die(){ printf "ERROR: %s\n" "$*" >&2; exit 1; }
  log_debug(){ [ "${BANKON_LOG:-1}" -ge 2 ] && printf "  · %s\n" "$*" >&2; return 0; }
  log_run(){ [ "${DRY:-0}" = 1 ] && { printf "   [dry-run] %s\n" "$*"; return 0; }; eval "$@"; }
fi
say(){ log_info "$@"; }; warn(){ log_warn "$@"; }; run(){ log_run "$@"; }
ROLE="${1:-}"
HERE="$(cd "$(dirname "$0")" && pwd)"
[ "$(uname -s)" = OpenBSD ] || { echo "cryptoBSD runs on FuguIta (live OpenBSD) — boot that first"; exit 1; }
SUDO=""; [ "$(id -u)" != 0 ] && { command -v doas >/dev/null && SUDO=doas; }
[ -z "$SUDO" ] && [ "$(id -u)" != 0 ] && { echo "run as root or via doas"; exit 1; }
case "$ROLE" in vault|node) ;; *) echo "usage: $0 vault|node"; exit 1 ;; esac

set_pkgpath() {
  [ -n "${PKG_PATH:-}" ] && return 0
  export PKG_PATH="https://cdn.openbsd.org/pub/OpenBSD/$(uname -r)/packages/$(uname -m)/"
  say "PKG_PATH=$PKG_PATH"
}

role_vault() {
  say "cryptoBSD · VAULT role — amnesic signing enclave (FuguIta mode 2 / RAM-only)"
  set_pkgpath
  $SUDO pkg_add -I python3 py3-cryptography libqrencode zbar 2>/dev/null || \
    warn "pkg_add failed — set PKG_PATH to a working mirror and retry (offline: pre-stage packages)"
  # vault + signer into /opt (all in RAM under mode 2)
  $SUDO mkdir -p /opt/bankon-vault /opt/bankon-vault-enclave
  _src="$HERE/../../bankon-vault"; [ -d /mnt/bankon-vault ] && _src=/mnt/bankon-vault
  $SUDO cp -R "$_src/bankon_vault" /opt/bankon-vault/ 2>/dev/null || $SUDO cp -R "$_src" /opt/bankon-vault/
  $SUDO cp "$HERE/../enclave/enclave-signer.sh" /opt/bankon-vault-enclave/ 2>/dev/null || true
  $SUDO chmod +x /opt/bankon-vault-enclave/enclave-signer.sh 2>/dev/null || true
  python3 -m pip install --user --quiet embit 2>/dev/null || \
    $SUDO python3 -m pip install --quiet --target /opt/bankon-vault/_vendor embit 2>/dev/null || true
  # AIRGAP: down every non-loopback interface (mode 2 is amnesic; do NOT usbfadm — must not persist)
  for i in $(ifconfig 2>/dev/null | sed -n 's/^\([a-z]*[0-9]\):.*/\1/p'); do
    case "$i" in lo*) : ;; *) $SUDO ifconfig "$i" down 2>/dev/null || true ;; esac
  done
  say "  airgap up · vault path = /tmp/enclave-vault (mfs/RAM). Import a seed AIR-GAPPED, then sign USB PSBTs."
  say "  start:  PYTHONPATH=/opt/bankon-vault BANKON_VAULT_PATH=/tmp/enclave-vault /opt/bankon-vault-enclave/enclave-signer.sh"
  say "  DO NOT run usbfadm in this role — the vault must leave no trace."
}

role_node() {
  say "cryptoBSD · NODE role — persistent encrypted Bitcoin-Core + multi-crypto foundation (mode 3)"
  set_pkgpath
  sh "$HERE/node-setup.sh"
  say "  persist this install:  doas usbfadm   →  (a) save changes to the encrypted volume"
  say "  next boot: choose FuguIta mode 3 and enter the volume passphrase to bring the node back."
}

case "$ROLE" in
  vault) role_vault ;;
  node)  role_node ;;
esac
say "cryptoBSD $ROLE ready."
