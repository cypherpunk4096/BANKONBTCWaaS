#!/usr/bin/env bash
# clarinet-install.sh — install the Clarinet CLI on Linux from official GitHub releases.
# SPDX-License-Identifier: Apache-2.0
#
# Usage:
#   sudo ./clarinet-install.sh                         # latest, -> /usr/local/bin
#   sudo ./clarinet-install.sh --version v3.2.0        # pinned version
#   ./clarinet-install.sh --prefix "$HOME/.local"      # user-local, no root
#   ./clarinet-install.sh --libc musl                  # force musl asset
#   ./clarinet-install.sh --completions                # also install bash completions
#
# Debian/Ubuntu deps: curl (or wget), tar. Everything else is detected.

set -euo pipefail

REPO="hirosystems/clarinet"
PREFIX="/usr/local"
VERSION=""            # empty = latest
LIBC=""               # empty = autodetect (glibc|musl)
WITH_COMPLETIONS=0

err()  { printf 'error: %s\n' "$*" >&2; exit 1; }
info() { printf '==> %s\n' "$*"; }

# --- args --------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --version)      VERSION="${2:?--version needs a tag like v3.2.0}"; shift 2 ;;
    --prefix)       PREFIX="${2:?--prefix needs a path}"; shift 2 ;;
    --libc)         LIBC="${2:?--libc needs glibc|musl}"; shift 2 ;;
    --completions)  WITH_COMPLETIONS=1; shift ;;
    -h|--help)      grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *)              err "unknown option: $1" ;;
  esac
done

BIN_DIR="${PREFIX}/bin"

# --- preflight ---------------------------------------------------------------
command -v tar >/dev/null || err "tar is required (apt install tar)"
if command -v curl >/dev/null; then
  FETCH() { curl -fsSL "$1" -o "$2"; }
  FETCH_STDOUT() { curl -fsSL "$1"; }
elif command -v wget >/dev/null; then
  FETCH() { wget -qO "$2" "$1"; }
  FETCH_STDOUT() { wget -qO- "$1"; }
else
  err "curl or wget is required (apt install curl)"
fi

# --- detect arch -------------------------------------------------------------
case "$(uname -m)" in
  x86_64|amd64)   ARCH="x64" ;;
  aarch64|arm64)  ARCH="arm64" ;;
  *)              err "unsupported architecture: $(uname -m) — build from source (see guide §4)" ;;
esac

# --- detect libc -------------------------------------------------------------
if [[ -z "$LIBC" ]]; then
  if ldd --version 2>&1 | grep -qi musl; then
    LIBC="musl"
  elif [[ -e /lib/ld-musl-x86_64.so.1 || -e /lib/ld-musl-aarch64.so.1 ]]; then
    LIBC="musl"
  else
    LIBC="glibc"
  fi
fi
[[ "$LIBC" == "glibc" || "$LIBC" == "musl" ]] || err "--libc must be glibc or musl"

ASSET="clarinet-linux-${ARCH}-${LIBC}.tar.gz"

# --- resolve version ---------------------------------------------------------
if [[ -z "$VERSION" ]]; then
  info "resolving latest release tag from GitHub API"
  VERSION="$(FETCH_STDOUT "https://api.github.com/repos/${REPO}/releases/latest" \
    | grep -m1 '"tag_name"' | sed -E 's/.*"tag_name"[^"]*"([^"]+)".*/\1/')"
  [[ -n "$VERSION" ]] || err "could not resolve latest version (API rate-limited?). Use --version vX.Y.Z"
fi
URL="https://github.com/${REPO}/releases/download/${VERSION}/${ASSET}"
info "target: clarinet ${VERSION} (${ARCH}, ${LIBC}) -> ${BIN_DIR}/clarinet"

# --- download + extract in a temp dir ----------------------------------------
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

info "downloading ${URL}"
FETCH "$URL" "${TMP}/${ASSET}" || err "download failed — check the tag exists and has asset ${ASSET}"

tar -xzf "${TMP}/${ASSET}" -C "$TMP"
[[ -f "${TMP}/clarinet" ]] || err "archive did not contain a 'clarinet' binary"
chmod +x "${TMP}/clarinet"

# --- smoke test before touching PATH -----------------------------------------
info "smoke test"
"${TMP}/clarinet" --version >/dev/null || err "downloaded binary failed to execute (wrong libc? try --libc musl)"

# --- install atomically -------------------------------------------------------
if [[ ! -d "$BIN_DIR" ]]; then
  mkdir -p "$BIN_DIR" 2>/dev/null || err "cannot create ${BIN_DIR} (need sudo, or use --prefix \$HOME/.local)"
fi
if [[ -w "$BIN_DIR" ]]; then
  install -m 0755 "${TMP}/clarinet" "${BIN_DIR}/clarinet"
else
  err "no write permission for ${BIN_DIR} — rerun with sudo, or use --prefix \$HOME/.local"
fi

# --- completions (optional) ---------------------------------------------------
if [[ "$WITH_COMPLETIONS" -eq 1 ]]; then
  info "installing bash completions"
  ( cd "$TMP" && "${BIN_DIR}/clarinet" completions bash >/dev/null 2>&1 || true )
  if [[ -f "${TMP}/clarinet.bash" ]]; then
    if [[ -d /etc/bash_completion.d && -w /etc/bash_completion.d ]]; then
      install -m 0644 "${TMP}/clarinet.bash" /etc/bash_completion.d/clarinet
    else
      mkdir -p "${HOME}/.local/share/bash-completion/completions"
      install -m 0644 "${TMP}/clarinet.bash" "${HOME}/.local/share/bash-completion/completions/clarinet"
    fi
  fi
fi

# --- done ---------------------------------------------------------------------
INSTALLED_VERSION="$("${BIN_DIR}/clarinet" --version 2>/dev/null | head -n1 || true)"
info "installed: ${INSTALLED_VERSION:-clarinet ${VERSION}}"
case ":${PATH}:" in
  *":${BIN_DIR}:"*) : ;;
  *) printf 'note: %s is not on your PATH. Add:\n  export PATH="%s:$PATH"\n' "$BIN_DIR" "$BIN_DIR" ;;
esac
info "next: clarinet new my-project && cd my-project && clarinet check"
