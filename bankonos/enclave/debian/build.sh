#!/bin/sh
# build.sh (Debian) — build the bankonOS signing enclave as a Debian live ISO (live-build), amnesic
# via `toram` (the Tails model). Run on Debian, or rootless podman:
#   podman run --rm -it --privileged -v "$PWD":/work:Z -w /work docker.io/library/debian:stable \
#       sh bankonos/enclave/debian/build.sh
# (live-build needs privileges for loop/mount — hence --privileged even under rootless podman.)
set -eu
HERE="$(cd "$(dirname "$0")" && pwd)"
VAULT_DIR="${VAULT_DIR:-$HERE/../../../bankon-vault}"
WORK="$HERE/_lb"
say() { printf '\033[38;5;208m▸ %s\033[0m\n' "$*"; }
command -v apt-get >/dev/null || { echo "run on Debian/Ubuntu (or a debian podman container)"; exit 1; }

say "1/5  live-build tooling…"
apt-get update -y >/dev/null
DEBIAN_FRONTEND=noninteractive apt-get install -y live-build git python3 python3-pip >/dev/null

say "2/5  live-build config — minimal, NO network, boot to RAM (toram)…"
rm -rf "$WORK"; mkdir -p "$WORK"; cd "$WORK"
lb config \
  --distribution stable --archive-areas "main contrib non-free non-free-firmware" \
  --binary-images iso-hybrid \
  --bootappend-live "boot=live components toram noeject nomodeset" \
  --apt-recommends false
mkdir -p config/package-lists
cat > config/package-lists/enclave.list.chroot <<'EOF'
python3
python3-cryptography
qrencode
zbar-tools
rfkill
pmount
EOF

say "3/5  bake the vault + signer into the image (offline device)…"
INCL="config/includes.chroot/opt"
mkdir -p "$INCL/bankon-vault" "$INCL/bankon-vault-enclave"
cp -a "$VAULT_DIR/bankon_vault" "$INCL/bankon-vault/"
cp "$HERE/../enclave-signer.sh" "$INCL/bankon-vault-enclave/"; chmod +x "$INCL/bankon-vault-enclave/enclave-signer.sh"
python3 -m pip install --quiet --target "$INCL/bankon-vault/_vendor" embit
cat > "$INCL/bankon-vault/bankon_vault/_path.pth" <<EOF
import sys, os; sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '_vendor'))
EOF

say "4/5  airgap-at-boot + autostart hooks (systemd)…"
HOOKS="config/includes.chroot/etc/systemd/system"
mkdir -p "$HOOKS" config/includes.chroot/etc/systemd/system/multi-user.target.wants
cat > "$HOOKS/bankon-airgap.service" <<'EOF'
[Unit]
Description=blackICE airgap (cut radios + NICs)
DefaultDependencies=no
Before=network-pre.target
[Service]
Type=oneshot
ExecStart=/bin/sh -c 'rfkill block all 2>/dev/null; for i in $(ls /sys/class/net); do [ "$i" = lo ] || ip link set "$i" down; done'
[Install]
WantedBy=sysinit.target
EOF
cat > "$HOOKS/bankon-enclave.service" <<'EOF'
[Unit]
Description=bankonOS air-gapped PSBT signing enclave
After=bankon-airgap.service
[Service]
ExecStart=/opt/bankon-vault-enclave/enclave-signer.sh
Restart=on-failure
[Install]
WantedBy=multi-user.target
EOF
ln -sf ../bankon-enclave.service config/includes.chroot/etc/systemd/system/multi-user.target.wants/
# mask networking so it never comes up
mkdir -p config/includes.chroot/etc/systemd/system
ln -sf /dev/null config/includes.chroot/etc/systemd/system/networking.service 2>/dev/null || true

say "5/5  build the ISO (this takes a while)…"
lb build || { say "lb build needs root/loop (use --privileged podman or a Debian host)"; exit 1; }
ls -la "$WORK"/*.iso 2>/dev/null && say "ISO ready. Boot OFFLINE with 'toram' → amnesic signer."
