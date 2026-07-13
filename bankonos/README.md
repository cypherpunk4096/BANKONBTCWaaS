# bankonOS installer

The **sovereign Bitcoin workstation**, provisioned cleanly — a ground-up rewrite of the 2024
bankonOS provisioning scripts.

> bankonOS **favours Alpine and OpenBSD**, with **Debian/Ubuntu compatibility**. This installer
> reflects that: it detects the OS and drives the right package manager (`apk` / `pkg_add` / `apt`),
> in **POSIX `sh`** so it runs on Alpine's `ash`, OpenBSD's `sh`, and `bash` alike.

## Why the rewrite
The old scripts were ad-hoc: Ubuntu-22-only, hardcoded single-version downloads, a **GUI editor
(`pluma`) invoked inside an "installer"**, `curl | bash` patterns, and no Alpine/OpenBSD support at
all. This one is:

- **Multi-OS** — Alpine (`apk`), OpenBSD (`pkg_add` / `doas`), Debian·Ubuntu (`apt`); per-OS package
  names handled by a small `pkgname()` map.
- **Modular & idempotent** — pick components; each skips what's already present.
- **Verified, not blind** — Bitcoin Core installs via BANKON's **SHA256SUMS-verified** `install-core`;
  third-party installers (ollama) are **fetched for review**, not piped to a shell.
- **Honest & safe** — `--dry-run` prints every action; no secrets, no GUI, no hardcoded versions.

## Usage
```sh
sh install.sh                                  # detect OS, choose components interactively
sh install.sh --only base,bitcoin,bankon --yes
sh install.sh --os alpine --dry-run            # preview the Alpine plan on any machine
COMPONENTS="base dev bitcoin bankon harden" sh install.sh --yes
```

## Components
| Component | Installs |
|---|---|
| **base** | git · curl · gnupg · tmux · jq · ca-certificates · python3 |
| **dev** | Go · Node · Rust · build toolchain (the sovereign dev workstation) |
| **bitcoin** | Bitcoin Core — BANKON's verified `install-core` if present, else the OS package |
| **bankon** | clones `cypherpunk2048/bankon-tools`, runs the `bankon-vault` installer, points at ICE |
| **ai** | fetches the official ollama installer for review (skipped on OpenBSD) |
| **harden** | firewall (`ufw`/`awall`/`pf`) + the air-gap/`swapoff` guidance for key ops |

## Design notes
- **OpenBSD-first mindset**: prefers `doas`, respects `pf` (built-in), never assumes systemd.
- **Alpine-first mindset**: `--no-cache apk add`, musl-friendly, tiny base.
- **Debian compatibility** is a convenience layer, not the target — the security posture is the point.
- The 2024 archive scripts (and any credential files in them) are **not** carried over — this is a
  clean, auditable reimplementation. bankonOS itself remains a private prototype repo; this is its
  modern installer.

Related: [`../bankon-vault`](../bankon-vault/README.md) (keys), [ICE](https://github.com/Professor-Codephreak/ice)
(perimeter), [`../docs/origins.md`](../docs/origins.md) (lineage).
