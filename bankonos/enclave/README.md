# bankonOS signing enclave (Alpine live image)

An **air-gapped, amnesic Bitcoin signer** — the machine becomes a black box that accepts PSBTs only
via USB, signs them with `bankon-vault`, and forgets everything on power-off. This is **blackICE
Phase 5** made real.

## Why Alpine
Alpine's **diskless mode runs the whole OS from RAM** (a tmpfs root), so the enclave is inherently
**amnesic** — keys, PSBTs, and logs never touch a disk and vanish at power-off. Customization uses
Alpine's official **APKOVL overlay** (a tarball of `/etc` + `/opt` the ISO applies at boot) — clean,
auditable, no forked base image. This maps exactly onto the vault's "prevention from eyes" design
(RAM-vault + no swap).

## Security model
- **Air-gapped by construction.** At boot, `/etc/local.d/00-airgap.start` `rfkill block all` and takes
  every NIC down; **no networking service is ever added to a runlevel**. The signer *additionally
  refuses to run* if a default route exists.
- **PSBT-only, USB-only.** The signer watches `/media /mnt /run/media` for unsigned `*.psbt`, decodes
  each for **explicit on-screen approval**, signs via `bankon-vault` (**sign-don't-export** — the key
  never leaves), and writes `*.psbt.signed` (+ a scannable QR). Broadcast happens on a *different*,
  online machine.
- **Amnesic.** Root is tmpfs; the vault lives at `/dev/shm/enclave-vault`. Power off → everything is
  gone. There is nothing to seize.
- **Offline & self-contained.** `bankon-vault` + `embit` + `py3-cryptography` are **vendored into the
  overlay** at build time, so the device needs no network, ever.

## Build it (rootless podman — no daemon, no root)
```sh
sh bankonos/enclave/podman-build.sh
#   ↳ podman run --rm -it -v "$PWD":/work:Z -w /work docker.io/library/alpine sh bankonos/enclave/build.sh
```
Or on an Alpine host directly: `sh bankonos/enclave/build.sh`.

The build: installs Alpine's `mkimage`/`alpine-conf` tooling, **vendors the vault** into `_stage/opt`,
generates `bankon-enclave.apkovl.tar.gz` (`genapkovl-bankon-enclave.sh`), and builds an **iso-hybrid**.

**Fallback (always works):** even without the full `mkimage` step, the generated
`bankon-enclave.apkovl.tar.gz` **is** the enclave — drop it onto the boot media of a stock Alpine
*extended* ISO (or a USB `/boot`), and Alpine auto-applies the overlay at boot. The overlay is the
product; the ISO is convenience.

## Use it
1. Boot the ISO on a spare, **offline** machine (ideally with the radios physically removed / a
   hardware kill switch).
2. First run: import your seed into the RAM vault — `bankon-vault import-btc` (air-gapped; the seed
   exists only in RAM).
3. Insert a USB stick containing `unsigned.psbt`. Approve on screen. Collect `unsigned.psbt.signed`
   (and `.png` QR).
4. Eject, move to an online machine, `bitcoin-cli finalizepsbt` + `sendrawtransaction`.
5. **Power off** the enclave — the vault and seed are erased.

## Files
- `genapkovl-bankon-enclave.sh` — generates the Alpine APKOVL overlay (packages, airgap, signer service).
- `enclave-signer.sh` — the on-device air-gapped signer (USB-only, refuses if networked).
- `build.sh` — vendors the vault + builds the ISO on Alpine.
- `podman-build.sh` — rootless-podman wrapper for the above.

Pairs with: [`../../bankon-vault`](../../bankon-vault/README.md) (the signer), ICE blackICE Phase 5
(the roadmap), [`../README.md`](../README.md) (the OS installer).
