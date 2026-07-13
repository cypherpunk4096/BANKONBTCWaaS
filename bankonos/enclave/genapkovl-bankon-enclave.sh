#!/bin/sh -e
# genapkovl-bankon-enclave.sh — generate the Alpine APKOVL overlay for the bankonOS SIGNING ENCLAVE.
# APKOVL is Alpine's official ISO-customization mechanism: a tarball of /etc + /opt that a stock
# Alpine ISO applies at boot. In diskless mode Alpine runs entirely from RAM (tmpfs root) → the
# enclave is AMNESIC: keys, PSBTs and logs vanish on power-off. blackICE Phase 5, made real.
#
# Usage (run from build.sh, which stages the vendored vault into $OVERLAY_SRC first):
#   OVERLAY_SRC=./_stage sh genapkovl-bankon-enclave.sh bankon-enclave
#   → bankon-enclave.apkovl.tar.gz   (feed to mkimage / drop on the ISO's boot media)
HOSTNAME="${1:-bankon-enclave}"
OVERLAY_SRC="${OVERLAY_SRC:-./_stage}"          # holds the vendored /opt/bankon-vault + embit

tmp="$(mktemp -d)"
cleanup() { rm -rf "$tmp"; }
trap cleanup EXIT

makefile() { OWNER="$1"; PERMS="$2"; FILENAME="$3"; mkdir -p "$(dirname "$FILENAME")"; cat > "$FILENAME"; chmod "$PERMS" "$FILENAME"; }
rc_add() { mkdir -p "$tmp/etc/runlevels/$2"; ln -sf "/etc/init.d/$1" "$tmp/etc/runlevels/$2/$1"; }

mkdir -p "$tmp/etc"
echo "$HOSTNAME" > "$tmp/etc/hostname"

# ── packages the enclave needs (apk installs these from the ISO's local cache — NO network) ──
makefile root:root 0644 "$tmp/etc/apk/world" <<'EOF'
alpine-base
python3
py3-cryptography
coreutils
qrencode
zbar
util-linux
rfkill
EOF

# ── the vendored vault + signer (staged by build.sh into $OVERLAY_SRC/opt) ──
if [ -d "$OVERLAY_SRC/opt" ]; then
  mkdir -p "$tmp/opt"; cp -a "$OVERLAY_SRC/opt/." "$tmp/opt/"
fi

# ── AIRGAP at boot: cut every radio and never bring up networking (defence in depth) ──
makefile root:root 0755 "$tmp/etc/local.d/00-airgap.start" <<'EOF'
#!/bin/sh
# hard airgap the enclave the instant it boots
command -v rfkill >/dev/null && rfkill block all 2>/dev/null || true
for i in $(ls /sys/class/net 2>/dev/null); do [ "$i" = lo ] && continue; ip link set "$i" down 2>/dev/null || true; done
echo "blackICE enclave: airgap UP (all radios/NICs down)" > /dev/kmsg 2>/dev/null || true
EOF

# ── the enclave signer service: waits for USB media with *.psbt, signs via the vault, writes back ──
makefile root:root 0755 "$tmp/etc/init.d/bankon-enclave" <<'EOF'
#!/sbin/openrc-run
description="bankonOS air-gapped PSBT signing enclave"
depend() { after local; }
command="/opt/bankon-vault-enclave/enclave-signer.sh"
command_background=true
pidfile="/run/bankon-enclave.pid"
output_log="/run/bankon-enclave.log"
error_log="/run/bankon-enclave.log"
EOF

# ── runlevels: airgap + signer come up; NO networking service is added on purpose ──
rc_add devfs sysinit
rc_add local default
rc_add bankon-enclave default

# banner on the login TTY
makefile root:root 0644 "$tmp/etc/motd" <<'EOF'
  🖤 bankonOS SIGNING ENCLAVE — air-gapped, amnesic (runs from RAM).
  Radios & NICs are DOWN. Insert USB media with unsigned *.psbt files; signed
  *.psbt.signed (+ QR) are written back. Power off to erase everything.
  Never bring up networking. See /opt/bankon-vault-enclave/README.
EOF

tar -c -C "$tmp" etc opt 2>/dev/null | gzip -9n > "$HOSTNAME.apkovl.tar.gz"
echo "wrote $HOSTNAME.apkovl.tar.gz"
