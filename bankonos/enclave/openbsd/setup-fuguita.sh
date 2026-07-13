#!/bin/sh
# setup-fuguita.sh — turn a booted FuguIta (live OpenBSD) system into the bankonOS signing enclave.
#
# OpenBSD has no native live-from-RAM mode, so the enclave is built ON FuguIta (https://fuguita.org),
# the mature live-OpenBSD project, in its "boot to RAM" (option 4 at the FuguIta boot menu) → amnesic.
# This script runs INSIDE a booted FuguIta to install the vault + signer and cut the network. Persist
# it with FuguIta's own `usbfadm` (saves a config chunk to the USB) if you want it to survive a rebuild.
#
#   doas sh setup-fuguita.sh            # run inside FuguIta (boot it in copy-to-RAM mode first)
#
# Prereqs on the FuguIta box: python3 + py3-cryptography (pkg_add), and this repo's bankon-vault +
# enclave-signer.sh copied to the USB (this script finds them under /mnt or the current dir).
set -eu
say() { printf '\033[38;5;208m▸ %s\033[0m\n' "$*"; }
[ "$(uname -s)" = OpenBSD ] || { echo "run this INSIDE a booted FuguIta (live OpenBSD)"; exit 1; }
SUDO=""; [ "$(id -u)" != 0 ] && { command -v doas >/dev/null && SUDO=doas; }

say "1/4  packages (system py3-cryptography — pip can't build it on OpenBSD)…"
$SUDO pkg_add -I python3 py3-cryptography py3-pip libqrencode 2>/dev/null || \
  warn_pkg="set PKG_PATH to a mirror, e.g. export PKG_PATH=https://cdn.openbsd.org/pub/OpenBSD/$(uname -r)/packages/$(uname -m)/"
[ -n "${warn_pkg:-}" ] && { echo "$warn_pkg"; exit 1; }

say "2/4  install the vault + signer to /opt (RAM — amnesic under FuguIta copy-to-RAM)…"
SRC="."; [ -d /mnt/bankon-vault ] && SRC=/mnt
$SUDO mkdir -p /opt/bankon-vault /opt/bankon-vault-enclave
$SUDO cp -R "$SRC/bankon-vault/bankon_vault" /opt/bankon-vault/ 2>/dev/null || \
  $SUDO cp -R "$SRC/bankon_vault" /opt/bankon-vault/
$SUDO cp "$SRC/enclave-signer.sh" /opt/bankon-vault-enclave/ 2>/dev/null || \
  $SUDO cp "$(dirname "$0")/../enclave-signer.sh" /opt/bankon-vault-enclave/
$SUDO chmod +x /opt/bankon-vault-enclave/enclave-signer.sh
python3 -m pip install --user --quiet embit 2>/dev/null || \
  $SUDO python3 -m pip install --quiet --target /opt/bankon-vault/_vendor embit
[ -d /opt/bankon-vault/_vendor ] && printf 'import sys,os;sys.path.insert(0,os.path.join(os.path.dirname(__file__),"..","_vendor"))\n' \
  | $SUDO tee /opt/bankon-vault/bankon_vault/_path.pth >/dev/null

say "3/4  AIRGAP: cut every radio + interface (OpenBSD)…"
# down every non-loopback interface; disable wifi/bluetooth
for i in $(ifconfig 2>/dev/null | sed -n 's/^\([a-z]*[0-9]\):.*/\1/p'); do
  case "$i" in lo*) : ;; *) $SUDO ifconfig "$i" down 2>/dev/null || true ;; esac
done
say "  interfaces down. (Physically remove the wifi card / use a machine with a hardware kill switch.)"

say "4/4  a memory fs for the RAM vault (OpenBSD has no /dev/shm)…"
$SUDO mount | grep -q ' /tmp .*mfs' || say "  tip: mount an mfs at /tmp (fstab: swap /tmp mfs rw 0 0) so BANKON_VAULT_PATH=/tmp is RAM-backed"
export BANKON_VAULT_PATH=/tmp/enclave-vault
say "start the signer:  PYTHONPATH=/opt/bankon-vault /opt/bankon-vault-enclave/enclave-signer.sh"
say "import a seed first (air-gapped):  PYTHONPATH=/opt/bankon-vault python3 -m bankon_vault.cli import-btc"
say "Enclave ready. Insert USB with *.psbt → get *.psbt.signed. Power off (copy-to-RAM) = amnesia."
