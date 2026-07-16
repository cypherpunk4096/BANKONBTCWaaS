# Clarinet on Linux: The Developer's Guide

Clarinet is the canonical development toolchain for Clarity — REPL, static analyzer, unit-test harness (simnet), local devnet, and mainnet deployment planner. It is to Stacks what Foundry is to EVM. This guide covers installation on Debian-family Linux (Debian, Ubuntu, Mint, Pop!_OS, Kali, Raspberry Pi OS) plus notes for musl/Alpine, the full development workflow, and a hardened bash installer.

There is **no official apt repository or .deb package** for Clarinet. On Linux you have four legitimate paths, in order of preference:

| Path | When |
|---|---|
| 1. Pre-built binary from GitHub releases | Default. Fast, reproducible, pinnable. The bash installer below automates it. |
| 2. Build from source with Cargo | You want `develop`-branch features, patched builds, or an arch without a release asset. |
| 3. Container image (`hirosystems/clarinet`) | CI, or zero-footprint usage. Works with Podman. |
| 4. `brew` via Homebrew-on-Linux | Only if you already run Linuxbrew; otherwise skip. |

---

## 1. Prerequisites (Debian/Ubuntu family)

```bash
sudo apt update
sudo apt install -y curl wget tar ca-certificates git

# For the JS/TS unit-test harness (clarinet-sdk + vitest) — Node.js LTS ≥ 18.
# Debian/Ubuntu repo Node is often stale; use NodeSource or nvm:
curl -fsSL https://deb.nodesource.com/setup_lts.x | sudo -E bash -
sudo apt install -y nodejs

# Only needed for source builds:
sudo apt install -y build-essential pkg-config libssl-dev
```

Devnet (local Bitcoin regtest + Stacks node + API) requires a container runtime. Docker is the documented default; **Podman works** — see §6.

## 2. Path 1 — Pre-built binaries (recommended)

Release assets follow this naming (check the releases page for the current tag):

```
clarinet-linux-x64-glibc.tar.gz     # standard Debian/Ubuntu/Fedora
clarinet-linux-arm64-glibc.tar.gz   # ARM64 (RPi 4/5 64-bit, Graviton, Ampere)
clarinet-linux-x64-musl.tar.gz      # Alpine / static-musl environments
```

Manual install (the documented sequence from book.clarity-lang.org / the Clarinet README):

```bash
# Replace vX.Y.Z with the latest tag from https://github.com/hirosystems/clarinet/releases
wget -nv https://github.com/hirosystems/clarinet/releases/download/vX.Y.Z/clarinet-linux-x64-glibc.tar.gz \
  -O clarinet-linux-x64.tar.gz
tar -xf clarinet-linux-x64.tar.gz
chmod +x ./clarinet
sudo mv ./clarinet /usr/local/bin/
clarinet --version
```

Prefer the **bash installer** in §3 — it detects arch and libc, resolves the latest release from the GitHub API, supports version pinning and user-local install, and verifies the binary runs before committing it to PATH.

### Shell completions (bash)

```bash
mkdir -p ~/.local/share/bash-completion/completions
clarinet completions bash
mv clarinet.bash ~/.local/share/bash-completion/completions/clarinet
# or system-wide: sudo mv clarinet.bash /etc/bash_completion.d/clarinet
exec bash
```

(`clarinet completions` also emits zsh, fish, elvish, powershell.)

### Updating / uninstalling

Binary installs update by re-running the installer (it overwrites atomically). Uninstall is `sudo rm /usr/local/bin/clarinet` plus the completions file. Nothing else touches the system; per-project state lives in the project directory and `~/.clarinet` / `~/.cache`.

## 3. Path 1 automated — the bash installer

Ships alongside this guide as `clarinet-install.sh`. Usage:

```bash
# Latest release, system-wide (/usr/local/bin, needs sudo):
sudo ./clarinet-install.sh

# Pin a version (reproducible toolchains — recommended for teams/CI):
sudo ./clarinet-install.sh --version v3.2.0

# User-local, no root (~/.local/bin):
./clarinet-install.sh --prefix "$HOME/.local"

# Force musl asset (Alpine or static environments):
./clarinet-install.sh --libc musl --prefix "$HOME/.local"
```

What it does, in order: detects `x86_64`/`aarch64` and glibc vs musl (via `ldd`), resolves the tag via the GitHub releases API (or uses `--version`), downloads to a temp dir, extracts, smoke-tests `./clarinet --version`, installs atomically with `install -m 0755`, and offers bash completions. It refuses to run as a pipe-to-shell one-liner pattern internally (`set -euo pipefail`, explicit temp dir, cleanup trap) — read it before running, as you should any installer.

## 4. Path 2 — Build from source (Cargo)

```bash
# Rust toolchain via rustup (Debian's packaged rustc is usually too old):
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
source "$HOME/.cargo/env"

# Build deps (Debian/Ubuntu):
sudo apt install -y build-essential pkg-config libssl-dev

git clone https://github.com/hirosystems/clarinet.git --recursive
cd clarinet
# develop = unreleased; main = latest stable
git checkout main
cargo clarinet-install     # alias defined in the repo; builds + installs to ~/.cargo/bin
clarinet --version
```

Note the classic failure on fresh Debian/Mint: `linker 'cc' not found` → you skipped `build-essential`. If you're hacking on Clarinet itself, the repo's contributing docs describe the SDK split (`clarinet-sdk-wasm` Rust→Wasm core + TypeScript wrapper, built with pnpm) and a functional-test loop over `components/clarinet-cli/examples`.

## 5. Path 3 — Container (works with Podman)

```bash
podman pull docker.io/hirosystems/clarinet:latest
alias clarinet='podman run --rm -it -v "$(pwd)":/workspace -w /workspace docker.io/hirosystems/clarinet:latest'
clarinet check
```

CI (GitHub Actions) equivalent:

```yaml
- name: Check contracts
  uses: docker://hirosystems/clarinet:latest
  with:
    args: check
```

## 6. Devnet on Podman instead of Docker

Clarinet's devnet orchestrates containers through the Docker API socket. Podman's compatibility socket satisfies it:

```bash
systemctl --user enable --now podman.socket
export DOCKER_HOST="unix://$XDG_RUNTIME_DIR/podman/podman.sock"
clarinet devnet start
```

Add the `DOCKER_HOST` export to `~/.bashrc`. Rootless Podman is fine; if image pulls fail, fully qualify registries (`docker.io/...`) in `settings/Devnet.toml`. If a devnet component misbehaves under rootless networking, `podman machine`-style setups or a rootful socket (`sudo systemctl enable --now podman.socket`, `DOCKER_HOST=unix:///run/podman/podman.sock`) are the fallback. Devnet is optional for pure contract work — simnet unit tests (§7) need no containers at all.

## 7. The development workflow

```bash
clarinet new satpay && cd satpay        # scaffold: Clarinet.toml, contracts/, tests/, settings/
clarinet contract new bankon-satpay     # adds contracts/bankon-satpay.clar + tests/*.test.ts
clarinet check                          # static analysis of the whole project
clarinet check contracts/bankon-satpay.clar   # single file
clarinet console                        # REPL against simnet
npm install && npm test                 # clarinet-sdk + vitest unit tests
clarinet devnet start                   # full local chain (containers)
clarinet deployments generate --mainnet --medium-cost
clarinet deployments apply -p deployments/default.mainnet-plan.yaml
```

Pin the interpreter per contract in `Clarinet.toml` (`clarity_version = 3`, `epoch = "3.0"`), enable the `check_checker` analysis pass, and use `--enable-remote-data` simnet for mainnet-fork testing against live contracts (the Foundry-fork-test equivalent). REPL essentials inside `clarinet console`: `(contract-call? .bankon-satpay wrap u100)`, `::get_assets_maps`, `::set_tx_sender`, `::advance_chain_tip 1`.

Editor: the **Clarity VS Code extension** (works in VSCodium) gives LSP diagnostics, autocomplete, and DAP step-debugging; it requires the `clarinet` binary on PATH.

## 8. Version pinning and CI hygiene

- Pin the Clarinet CLI version in CI with the installer's `--version` flag; pin `@hirosystems/clarinet-sdk` and `@stacks/transactions` in `package.json`. CLI and SDK versions should move together (`npm install @hirosystems/clarinet-sdk@latest @stacks/transactions@latest` when you bump the CLI).
- `clarinet check` in CI on every push; unit tests with coverage (`--coverage` → lcov) feed codecov.
- Never commit `settings/Mainnet.toml` with a plaintext mnemonic; use `clarinet deployments` encryption or inject via environment in CI.

## 9. Troubleshooting quick table

| Symptom | Cause / fix |
|---|---|
| `clarinet: command not found` after install | `/usr/local/bin` or `~/.local/bin` not on PATH; `hash -r` or re-login |
| `linker 'cc' not found` (source build) | `sudo apt install build-essential` |
| OpenSSL build errors (source build) | `sudo apt install pkg-config libssl-dev` |
| `GLIBC_2.xx not found` on old Debian | Use the musl asset (`--libc musl`) or build from source |
| Devnet can't reach Docker | Export `DOCKER_HOST` to the Podman socket (§6) or install Docker |
| Tests can't find `simnet` global | Run through vitest with the project's `vitest.config.js` (clarinet-sdk environment), not bare node |

## Sources
- hirosystems/clarinet README and releases (github.com/hirosystems/clarinet)
- Clarity Book, "Installing Tools" (book.clarity-lang.org/ch01-01-installing-tools.html)
- Clarinet docs (docs.hiro.so, docs.stacks.co/clarinet)
