# cryptoBSD — the bankonOS guide to OpenBSD (on FuguIta)

**cryptoBSD** is the OpenBSD member of the bankonOS family: the **cryptocurrency layer** running on a
**bankonBSD** base, which is [OpenBSD](https://www.openbsd.org) made live/amnesic by
[**FuguIta**](https://github.com/ykaw/FuguIta) (© Yoshihiro Kawamata — an OpenBSD-based live system).
This is an *original* bankonOS guide, adapted from the FuguIta Guide, for two purposes on **one image**:

- **vault** — an **amnesic air-gapped signing enclave** (FuguIta **mode 2**, RAM-only).
- **node** — a **persistent, encrypted Bitcoin-Core + multi-crypto foundation** (FuguIta **mode 3**).

> Why OpenBSD for crypto? Kernel hardening you don't configure — `pledge(2)`/`unveil(2)` syscall &
> filesystem confinement, W^X, strong ASLR, and a first-class `pf` firewall. For key custody and a
> sovereign node, that default posture is the point.

## FuguIta's modes → cryptoBSD's roles
FuguIta's boot menu offers (verbatim from its `rc`):
```
0: fresh boot - standard mode as a live system
1: fresh boot - less memory, faster boot   (/usr non-writable)
2: fresh boot - works using only RAM        ← cryptoBSD VAULT (amnesic)
3: boot with retrieving saved files / encrypted volume passphrase  ← cryptoBSD NODE (persistent)
4: retrieving saved files from floppy
5: interactive shell for maintenance
```
cryptoBSD ships two **`noasks`** presets that select the mode unattended: `noasks.vault` (mode 2) and
`noasks.node` (mode 3).

## 1 · Build the bankonBSD (FuguIta) USB
On any OpenBSD box (or from a downloaded FuguIta image):
```sh
# get a FuguIta live image for your OpenBSD release/arch from https://fuguita.org/ (verify the SHA256)
doas dd if=FuguIta-<rel>-<arch>-*.img of=/dev/rsdXc bs=1m   # write to USB (rsdXc = your stick, raw)
```

## 2 · Apply a cryptoBSD role preset (unattended boot)
Copy the matching `noasks` onto the FuguIta media so it boots straight into the role:
```sh
# mount the FuguIta USB's config area and drop the preset (path uses your OpenBSD rel/arch):
cp bankonos/cryptobsd/noasks.vault  /mnt/usb/livecd-config/<osrel>/<osmac>/noasks   # amnesic vault
# …or noasks.node for the persistent node.  Also copy this repo's bankon-vault/ onto the USB.
```

## 3 · Boot and provision the role
- **VAULT (mode 2, amnesic):** boot the USB → it comes up RAM-only. Then:
  ```sh
  doas sh bankonos/cryptobsd/cryptobsd.sh vault
  # imports the vault, cuts every interface (airgap), starts the USB-PSBT signer. Nothing persists.
  ```
  Import a seed **air-gapped** (`bankon-vault import-btc`), sign PSBTs from USB, **power off = amnesia**.
- **NODE (mode 3, persistent+encrypted):** first boot creates the encrypted saved volume; enter its
  passphrase at boot. Then:
  ```sh
  COINS="bitcoin monero" doas sh bankonos/cryptobsd/cryptobsd.sh node   # Bitcoin Core + chosen coins
  doas usbfadm                                                          # PERSIST to the encrypted volume
  doas rcctl start bitcoind
  ```
  Next boots: choose **mode 3**, enter the passphrase, the node returns.

## 4 · Attach the BANKON stack
Point BANKON's **read-only Console / WaaS** at the node's loopback `:8332` (from the same box or a
paired machine) for diagnostics + non-custodial wallet services — the node stays sovereign, keys stay
in the vault (or the air-gapped enclave).

## Separation of concerns
- **bankonBSD** = the OS (OpenBSD + FuguIta live/persistence). Owns: boot modes, disk/RAM, `pf`, users.
- **cryptoBSD** = the cryptocurrency systems on top. Owns: bankon-vault, Bitcoin Core, other daemons.
See [`../ARCHITECTURE.md`](../ARCHITECTURE.md) for the whole bankon\*/crypto\* family.

## Credits
FuguIta by Yoshihiro Kawamata — <https://github.com/ykaw/FuguIta>, <https://fuguita.org>. OpenBSD by
the OpenBSD project. cryptoBSD adds only the bankonOS crypto layer; it forks neither.
