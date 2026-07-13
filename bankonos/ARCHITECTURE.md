# bankonOS architecture — two layers, three platforms

bankonOS (the next evolution of the [bankonme](https://github.com/bankonme) project) **separates two
concerns** so each can be reasoned about, hardened, and swapped independently:

```
   ┌─────────────────────────────────────────────────────────────┐
   │  crypto layer   — the cryptocurrency systems                 │
   │  cryptoBSD · cryptoAlpine · cryptoDebian                     │
   │    bankon-vault (keys/signing) · Bitcoin Core · other coins  │
   ├─────────────────────────────────────────────────────────────┤
   │  OS layer       — the operating system foundation            │
   │  bankonBSD · bankonAlpine · bankonDebian                     │
   │    live/amnesic + persistence · airgap · firewall · users    │
   └─────────────────────────────────────────────────────────────┘
```

- **bankon\<OS\>** owns *the machine*: how it boots (live/amnesic vs persistent), disk/RAM, the
  firewall, radios/air-gap, users. No coin logic lives here.
- **crypto\<OS\>** owns *the money*: `bankon-vault` (encrypted keys, signing, gating, ceremony),
  Bitcoin Core, and other cryptocurrency daemons. It assumes a bankon\<OS\> underneath and never
  reaches below it.

The same crypto layer therefore runs on any of the three OS foundations; you pick the OS for its
properties, and the crypto behaviour is identical.

## The family
| OS layer | crypto layer | base | live/amnesic | persistence | in repo |
|---|---|---|---|---|---|
| **bankonAlpine** | **cryptoAlpine** | Alpine | diskless (tmpfs root) | apkovl / lbu | `enclave/` (Alpine) |
| **bankonDebian** | **cryptoDebian** | Debian live | `toram` (Tails model) | persistence partition | `enclave/debian/` |
| **bankonBSD** | **cryptoBSD** | OpenBSD + FuguIta | mode 2 (RAM-only) | mode 3 (encrypted vol) | `cryptobsd/` |

### Two standard deployments (any platform)
1. **Vault builder / signing enclave** — the OS boots **amnesic** (Alpine diskless · Debian `toram` ·
   FuguIta mode 2); the crypto layer is just `bankon-vault` + the USB PSBT signer. Air-gapped,
   sign-don't-export, power-off = amnesia. (`enclave/`, `cryptobsd.sh vault`.)
2. **Foundational node** — the OS boots **persistent + encrypted** (LUKS on Alpine/Debian · FuguIta
   mode 3 encrypted volume); the crypto layer runs **Bitcoin Core + other coins** as durable daemons,
   with the BANKON read-only Console/WaaS attached. (node setups: `cryptobsd/node-setup.sh` · `cryptoalpine/node-setup.sh` · `cryptodebian/node-setup.sh`.)

## Why this split matters
- **Audit surface** — you can verify the OS air-gap/hardening separately from the key handling.
- **Portability** — cryptoBSD/cryptoAlpine/cryptoDebian are the *same* crypto layer; only the OS
  provisioning differs (see each platform's build).
- **Honest capability** — the OS layer declares live/amnesic vs persistent; the crypto layer declares
  Tier-C/Tier-Q (see `bankon-vault/SECURITY.md`). Nothing is conflated.

## Pick your OS foundation
- **bankonAlpine** — smallest, cleanest, fastest to build (musl, tiny base).
- **bankonDebian** — most battle-tested amnesic lineage (Tails is amnesic Debian).
- **bankonBSD** — OpenBSD kernel hardening (pledge/unveil, W^X, `pf`) via FuguIta; heaviest setup.

Guides: [`cryptobsd/GUIDE.md`](cryptobsd/GUIDE.md) · [`enclave/PLATFORMS.md`](enclave/PLATFORMS.md) ·
OS installer (Debian-compat convenience): [`README.md`](README.md).
