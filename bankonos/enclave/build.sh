#!/bin/sh
# build.sh — build the bankonOS Alpine SIGNING-ENCLAVE ISO. Runs ON Alpine. The easy way is rootless
# PODMAN (no daemon, no root — fitting for a security build). From the repo root:
#
#   podman run --rm -it -v "$PWD":/work:Z -w /work docker.io/library/alpine:latest \
#       sh bankonos/enclave/build.sh
#
# Or just `sh bankonos/enclave/podman-build.sh` (wrapper). It vendors bankon-vault + embit into an
# overlay (so the enclave needs NO network), generates the APKOVL, and builds an iso-hybrid with
# Alpine's official mkimage. Output: bankon-enclave*.iso
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
HOSTNAME="bankon-enclave"
HERE="$(cd "$(dirname "$0")" && pwd)"
VAULT_DIR="${VAULT_DIR:-$HERE/../../bankon-vault}"
STAGE="$HERE/_stage"
OUT="${OUT:-$HERE/iso}"
[ "$(uname -s)" = Linux ] || { echo "run on Alpine Linux (or an alpine podman container)"; exit 1; }
command -v apk >/dev/null || { echo "not Alpine — apk missing. Use rootless podman:"; \
  echo "  podman run --rm -it -v \"\$PWD\":/work:Z -w /work docker.io/library/alpine sh $0"; exit 1; }

say "1/5  build tooling (alpine-sdk + mkimage + apkovl deps)…"
apk add --no-cache alpine-sdk alpine-conf squashfs-tools xorriso grub grub-efi mtools dosfstools \
  python3 py3-pip py3-cryptography git >/dev/null

say "2/5  vendor the vault (+ embit) into the overlay — the enclave is fully offline…"
rm -rf "$STAGE"; mkdir -p "$STAGE/opt/bankon-vault" "$STAGE/opt/bankon-vault-enclave"
cp -a "$VAULT_DIR/bankon_vault" "$STAGE/opt/bankon-vault/"
cp "$HERE/enclave-signer.sh" "$STAGE/opt/bankon-vault-enclave/"; chmod +x "$STAGE/opt/bankon-vault-enclave/enclave-signer.sh"
cp "$HERE/README.md" "$STAGE/opt/bankon-vault-enclave/README" 2>/dev/null || true
# vendor embit (pure python) so no pip/network is needed on the device
python3 -m pip install --quiet --target "$STAGE/opt/bankon-vault/_vendor" embit
# make the vendored embit importable alongside the vault
cat > "$STAGE/opt/bankon-vault/bankon_vault/_path.pth" <<EOF
import sys, os; sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '_vendor'))
EOF

say "3/5  generate the APKOVL overlay (etc + opt)…"
( cd "$HERE" && OVERLAY_SRC="$STAGE" sh genapkovl-bankon-enclave.sh "$HOSTNAME" )

say "4/5  mkimage: build the iso-hybrid with the overlay baked in…"
mkdir -p "$OUT"
# Alpine's mkimage profile: a minimal, no-network image with our apkovl embedded.
# (mkimage.sh lives in /usr/share/mkinitfs or the aports 'scripts' dir; apk 'alpine-conf' provides it.)
MKIMG="$(command -v mkimage.sh || echo /usr/share/mkimage/mkimage.sh)"
if [ -x "$MKIMG" ]; then
  "$MKIMG" --tag edge --outdir "$OUT" --arch x86_64 \
    --repository https://dl-cdn.alpinelinux.org/alpine/edge/main \
    --profile bankon_enclave --apkovl "$HERE/$HOSTNAME.apkovl.tar.gz" || \
  say "mkimage profile step needs an aports checkout — see README for the two-line profile + fallback"
else
  say "mkimage.sh not found — fallback: extend a stock Alpine 'extended' ISO by dropping"
  say "  $HOSTNAME.apkovl.tar.gz onto its boot media (Alpine auto-applies an apkovl at boot)."
fi

say "5/5  done. Artifacts:"
ls -la "$HERE/$HOSTNAME.apkovl.tar.gz" 2>/dev/null || true
ls -la "$OUT"/*.iso 2>/dev/null || say "  (no ISO — use the apkovl fallback above; the overlay IS the enclave)"
say "Boot it OFFLINE. Insert USB with *.psbt → get *.psbt.signed back. Power off = amnesia."
