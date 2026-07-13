# bankon-vault — Lineage

`bankon-vault` is not new — it is the consolidation of a decade of the author's own self-custody work
into one clean, chain-agnostic module. This file records where each part comes from.

## The founding vision — bankonme (the decades-old plan)
[**github.com/bankonme**](https://github.com/bankonme) — *"privacy, integrity and security for your
personal banking information."* The original **Bank On Me** project (a 2014 Bitcoin-payments effort)
is the root of the brand and the plan: **be your own bank, keys offline.** Its
[`cold-storage`](https://github.com/bankonme/cold-storage) repo ("scripts to safely store bitcoin
offline"), together with the bitaddress.org / Bitcoin Armory heritage it collects, *is* the original
spec that this module now fulfils definitively for BTC: **encrypted at rest + air-gap-frozen +
sign-don't-export.** bankonme **precedes** bankonOS.

## The umbrella — bankonvault
[**github.com/bankonvault**](https://github.com/bankonvault) — the key-management / encrypted-storage /
OpenPGP collection ("client to data storage facilitation"); its `password-manager` is the first-party
client-side encrypted vault. The name and the "client controls the keys" posture come from here.

## The operational ceremony — legacy Tomb/LUKS scripts
The `bankonvault.sh` family (GNU **Tomb** → LUKS dm-crypt loopback: `aes-xts-plain64`/`sha512`).
**Kept:** swap-off before touching secrets, `trap`-based cleanup that zeroes vars and unmounts on any
exit, inactivity **auto-lock**, `shred -u -z` secure delete, refuse-to-read on loose permissions,
multi-vault naming. **Dropped:** storing secrets as cleartext *inside* a mounted container (exposed
whole while open) — replaced by per-entry AES-256-GCM.

## The canonical crypto — mindX bankon_vault
The author's `bankon_vault/vault.py` (production): **AES-256-GCM + two-stage HKDF-SHA512**, per-entry
**AAD binding**, the **overseer** custody model (Machine / Human / DAIO), two-phase atomic rotation,
and the **sign-don't-export oracle** (`sign_routes.py`) — sign a payload without ever returning the
key, behind a single-use, scope+params-bound signed challenge. This is the core reproduced here.
Its client sibling `openagents/bankoneth/walletcreator/bankon_vault.py` binds the master key to a
**wallet signature** (`from_participant_signature`) — the model for `WalletSignatureOverseer`.

## The ETH sibling — bankoneth
`openagents/bankoneth/` — the BANKON ENS/registrar stack (viem/SIWE, `BankonAuthGate`). It proved the
**signature-gated session** pattern on Ethereum (EIP-191/4361). `bankon-vault` generalises it: BTC uses
a Bitcoin-Signed-Message ECDSA signature (BIP-322 to follow) in place of EIP-191, same HKDF-over-
signature construction. `walletcreator` already mints EVM (secp256k1) + Algorand (ed25519) — this
module adds the missing **Bitcoin** adapter.

## The abstraction — gnugui / GNUVAULT
[**github.com/gnugui/GNUVAULT**](https://github.com/gnugui/GNUVAULT) (the author's, GPLv3) — the
cleanest chain-agnostic shape: `Overseer.material()` with Passphrase / Keyfile / **WalletSignature**
overseers, the *tomb* / *mausoleum* multi-vault model, and `airgap.py` offline signing. This module's
`overseer.py` and the GPLv3 license follow directly from it. (Related: `Professor-Codephreak/gnutomb`.)

## The standard — cypherpunk2048 / CP2048-QR
[**github.com/cypherpunk2048**](https://github.com/cypherpunk2048) — *mathematics replaces authority,
verification replaces trust.* **Non-custodial, per-connection client-side keys, crypto-agile
(hybrid-PQC ready), ≥112-bit symmetric-equivalent.** `bankon-vault` targets this standard.

## bankonOS (context, not a code origin)
**bankonOS** is the author's sovereign-workstation tooling/design — it **favours Alpine and OpenBSD,
with Debian compatibility** (the on-disk Ubuntu-22.04 provisioning kit is one deb-compat instance of
it, not the whole design). It is a **private prototype repo** — an early proof-of-concept alongside
[github.com/bankonvault](https://github.com/bankonvault) (also prototype-stage) — documented here as
lineage/context; this module vendors none of it.

## What is deliberately NOT carried over
No key material, run transcripts, or credential files from any legacy artifact are read into, copied
to, or referenced-by-content in this repo. Historical secret logs are treated as burned.
