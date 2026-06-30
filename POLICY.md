# BANKON — Licensing & Encryption Policy

## TL;DR
- **Client-facing encryption software is GPLv3** (copyleft). The code that mints keys
  and signs transactions stays free and auditable in every derivative.
- **Everything else is MIT** (permissive). It never touches a private key.
- **You own your filesystem.** BANKON asserts no control over your machine.

## Why GPLv3 for the encryption components — the "PGP is GPL" argument

When software holds a user's keys, the user must be able to verify it does *only* what it
claims — and must be protected from a future where someone forks it into a closed,
unauditable, possibly backdoored product. Permissive licenses allow exactly that closure;
**copyleft forbids it.**

This is the settled precedent of the cryptography world:

- **PGP** (Pretty Good Privacy) established that individuals deserve strong, personal
  encryption they control.
- **GnuPG / GPG** — the free, standard implementation of the OpenPGP standard — is
  licensed under the **GNU GPL** for precisely this reason: cryptographic tools that guard
  people's secrets should be copyleft, so the source and *all* derivatives remain open,
  inspectable, and free. (https://gnupg.org, https://www.gnu.org/licenses/gpl-3.0.html)

BANKON adopts the same stance. The components that handle private key material —
`keygen.mjs`, `sign.mjs`, the offline client, and the in-browser keygen/signing — are
**GPLv3-or-later**. Anyone who ships a modified BANKON wallet must ship their source too.
Users get the GnuPG-grade guarantee: the code that touches your keys can never be quietly
turned proprietary.

The non-key infrastructure (servers, diagnostics, launcher, ops) is **MIT**, because it
holds no secrets and permissive licensing lowers the friction to integrate BANKON into a
running Bitcoin Core.

## User sovereignty

BANKON is a tool you run on hardware you own. Accordingly:

- **You own your filesystem.** You may install, modify, copy, move, inspect, delete, or
  destroy any part of BANKON or its data, at any time, for any reason you deem
  appropriate. No license term here restricts what you do to *your own* files.
- **Your keys never leave your device.** BANKON is non-custodial by construction (see
  [docs/security.md](docs/security.md)): keys are minted client-side, the node holds only
  watch-only descriptors, signing is local.
- **No phone-home.** The software runs entirely on infrastructure you control and makes
  no telemetry or callback to BANKON.

The GPLv3's copyleft constrains *redistribution of modified encryption code* (you must
pass on the same freedoms) — it does **not** constrain your private use or what you do to
your own filesystem.

## The permissive tier is BSD/MIT-family — and built on it

BANKON's permissive components are MIT, which belongs to the same family of short,
permissive, attribution-preserving licenses as the **BSD** licenses (2- and 3-clause).
For a user they are practically equivalent: use, modify, redistribute freely, keep the
copyright notice. In this sense BANKON's non-encryption tier **is a form of BSD-style
licensing**, and it deliberately **builds on BSD/MIT-family software**:

| Upstream | Role | License (permissive family) |
|----------|------|------------------------------|
| Bitcoin Core | the node BANKON extends | MIT |
| Node.js | runtime for the WaaS/Console | MIT |
| Express | HTTP server | MIT |
| `@scure/*`, `@noble/*` (bip32/bip39/btc-signer/base/hashes/curves) | audited client crypto | MIT |
| OpenSSL (via `bankon-backup.sh`) | AES backup encryption | Apache-2.0 (BSD-style) |

These permissive (MIT/BSD) foundations are *compatible with* the GPLv3 client tier: GPLv3
can incorporate MIT/BSD code, so the `@scure`/`@noble` crypto libraries are linked into the
GPLv3 keygen/signing components without conflict. The reverse does not hold — that
asymmetry is exactly why the key-handling code is GPLv3 and the infrastructure is MIT.

## Recommended free-software OS foundations

Owning your filesystem starts with the OS under it. BANKON runs well on minimal, free,
auditable systems — and for **air-gapped signing** (the offline client) a clean,
disposable install is ideal. Recommended foundations:

- **Alpine Linux** — tiny, security-oriented (musl + hardened), great for nodes and
  air-gapped USB installs. Downloads / ISOs: https://alpinelinux.org/downloads/
- **Debian** — the stable, fully-free GNU/Linux base. Install media & ISOs:
  https://www.debian.org/distrib/ · netinst: https://www.debian.org/distrib/netinst ·
  CD/DVD images: https://cdimage.debian.org/debian-cd/current/amd64/iso-cd/
- **OpenBSD** — the BSD project that sets the bar for correctness, cryptography, and
  security-by-default; the spiritual home of the BSD-license ethos discussed above.
  Project: https://www.openbsd.org/ · download/mirrors: https://www.openbsd.org/ftp.html

For air-gapped use: install one of these on a spare/USB machine with networking disabled,
open `offline-client.html` (with the `@scure` libs vendored locally), generate or sign
there, and move only the signed hex out.

## Practical notes
- Each source file carries an `SPDX-License-Identifier` declaring its license; the header
  governs in case of doubt.
- Contributions to the GPLv3 components are accepted under GPLv3-or-later.
- Full texts: [LICENSE.GPLv3](LICENSE.GPLv3), [LICENSE.MIT](LICENSE.MIT); summary in
  [LICENSE](LICENSE).
