# Signing enclave — one model, three platforms

The bankonOS signing enclave — **air-gapped · amnesic · PSBT-over-USB-only · sign-don't-export** — is
a *security model*, not one OS. The same signer (`enclave-signer.sh`, OS-aware) runs on all three;
only the way each OS boots-to-RAM and is customized differs.

| | **Alpine** (`../`) | **Debian** (`debian/`) | **OpenBSD** (`../cryptobsd/`) |
|---|---|---|---|
| Live/amnesic mechanism | diskless mode (tmpfs root) — native | `live-build` + `toram` boot (the **Tails** model) | **FuguIta** "copy-to-RAM" mode (third-party) |
| Customization | **APKOVL** overlay (etc + opt) | `live-build` `includes.chroot` + systemd units | `usbfadm` saved config / this setup script |
| Amnesic by default? | **yes** (RAM root) | yes with `toram`, no persistence | yes in copy-to-RAM mode |
| Base is first-party? | yes | yes | **no** — needs FuguIta (external, mature) |
| RAM vault path | `/dev/shm` | `/dev/shm` | `/tmp` on an **mfs** mount |
| Airgap at boot | `local.d` rfkill + NIC down | `bankon-airgap.service` (systemd) | `ifconfig down` in the setup script |
| Maturity for this use | smallest, cleanest | **most proven** (Tails lineage) | most conservative kernel; heaviest setup |

## So — can OpenBSD and Debian be used the same way?
- **Debian: yes, fully** — arguably the reference (Tails *is* an amnesic Debian). `debian/build.sh`
  builds a `toram` live ISO with the vault baked in.
- **OpenBSD: yes, via FuguIta** — base OpenBSD has no live-from-RAM mode, so the enclave is built on
  FuguIta (which does). `../cryptobsd/cryptobsd.sh vault` provisions a booted FuguIta (mode 2) into the enclave; `node` gives the persistent Bitcoin foundation. See cryptobsd/GUIDE.md.
  Trade-off: FuguIta is a superb but *external* project and trails OpenBSD releases slightly.

## Picking one
- Want the **smallest, cleanest, easiest** image → **Alpine**.
- Want the **most battle-tested amnesic lineage** → **Debian** (Tails-proven).
- Want **OpenBSD's kernel hardening** (pledge/unveil, W^X, strong `pf`) and will accept FuguIta as the
  live layer → **OpenBSD**.

All three enforce the same invariants: no networking service ever comes up, the signer **refuses if a
default route exists**, keys live only in RAM, and power-off erases everything. The vault's behaviour
(AES-256-GCM + HKDF, mlock, sign-don't-export, Shamir ceremony, policy engine) is identical on each.
