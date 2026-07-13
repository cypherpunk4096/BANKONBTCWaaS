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
| **bankon** | clones `cypherpunk2048/bankon-tools`, installs the **vault** (below), points at ICE |
| **vault** | **bankon-vault on any OS, correctly** — see below |
| **ai** | fetches the official ollama installer for review (skipped on OpenBSD) |
| **harden** | firewall (`ufw`/`awall`/`pf`) + the air-gap/`swapoff` guidance for key ops |

## bankon-vault on any OS — correct vault handling
`sh install.sh --only vault` (or as part of `bankon`) installs bankon-vault properly on **every**
supported OS, because the pieces differ:

- **Crypto backend.** `cryptography` ships a *compiled* backend; a bare `pip install` on **Alpine
  (musl)** or **OpenBSD** tries to build it (needs a Rust/OpenSSL toolchain). So the installer uses
  the **system package** — `py3-cryptography` (Alpine/OpenBSD) / `python3-cryptography` (Debian) —
  and only pip-installs the pure-Python `embit` (with `--break-system-packages` where the distro
  externally-manages pip).
- **Key-hygiene tools** (all optional; the vault degrades honestly without them):
  - **Alpine** — `coreutils` for a real `shred -n` (busybox's `shred` lacks passes); `cryptsetup`.
  - **Debian** — `coreutils` + **`tomb`** (the LUKS "frozen" backend).
  - **OpenBSD** — no Tomb/LUKS; the installer points you at `rm -P`, the **RAM-vault**, and
    **softraid(4) CRYPTO** for cold storage instead.
- **RAM-vault path** differs: `/dev/shm` on Linux, an `mfs`/`tmpfs` mount on OpenBSD — the installer
  prints the right `BANKON_VAULT_PATH` for your OS.
- Always ends with the same guidance: **create keys air-gapped (ICE AIRGAP) with swap off**.

The result is identical vault *behaviour* (AES-256-GCM + HKDF, mlock, sign-don't-export, Shamir
ceremony, policy engine) on Alpine, OpenBSD, and Debian — only the provisioning path adapts.

## Design notes
- **OpenBSD-first mindset**: prefers `doas`, respects `pf` (built-in), never assumes systemd.
- **Alpine-first mindset**: `--no-cache apk add`, musl-friendly, tiny base.
- **Debian compatibility** is a convenience layer, not the target — the security posture is the point.
- The 2024 archive scripts (and any credential files in them) are **not** carried over — this is a
  clean, auditable reimplementation. bankonOS itself remains a private prototype repo; this is its
  modern installer.

Related: [`../bankon-vault`](../bankon-vault/README.md) (keys), [ICE](https://github.com/Professor-Codephreak/ice)
(perimeter), [`../docs/origins.md`](../docs/origins.md) (lineage).
