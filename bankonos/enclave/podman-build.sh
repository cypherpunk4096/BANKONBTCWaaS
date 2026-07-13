#!/bin/sh
# podman-build.sh — build the enclave ISO in a rootless PODMAN Alpine container (no docker, no root
# daemon). Run from anywhere; it resolves the repo root and mounts it into the container.
#
#   sh bankonos/enclave/podman-build.sh
#
# Requires: podman (rootless is fine). The build needs privileges for loop/squashfs; if mkimage fails
# under rootless, re-run with `--privileged` (uncomment below) or on an Alpine host directly — the
# generated .apkovl.tar.gz overlay alone is a working enclave when dropped on a stock Alpine ISO.
set -eu
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
command -v podman >/dev/null || { echo "podman not found — install podman (rootless), then re-run"; exit 1; }

printf '\033[38;5;208m▸ building the enclave in rootless podman (alpine)…\033[0m\n'
exec podman run --rm -it \
  -v "$REPO":/work:Z \
  -w /work \
  --device /dev/fuse \
  docker.io/library/alpine:latest \
  sh -c 'apk add --no-cache fuse-overlayfs squashfs-tools >/dev/null 2>&1 || true; \
         sh bankonos/enclave/build.sh'
# If loop/iso creation is blocked under rootless podman, add:  --privileged  (line above)
