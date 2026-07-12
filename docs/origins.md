# Origins — the lineage of BANKON and bankon-vault

> **North star (github.com/bankonme):** *"privacy, integrity and security for your personal banking
> information."* — **be your own bank, keys offline.**

BANKON is not a new idea; it is the maturation of a decade of self-custody work into a running,
non-custodial system. This document records where the parts come from, honestly — including what is
*not* a public repo and what secret material is deliberately excluded.

## Timeline

1. **bankonme (the founding vision, ~2014 →).** [github.com/bankonme](https://github.com/bankonme) —
   the original *Bank On Me* Bitcoin-payments project and self-custody archive. Its
   [`cold-storage`](https://github.com/bankonme/cold-storage) repo ("scripts to safely store bitcoin
   offline") plus the bitaddress.org / Bitcoin Armory heritage is the **original bankon-vault plan**:
   encrypted, offline, client-controlled keys. A full index of the archive's tools (URLs +
   descriptions) is in [**bankonme-archive.txt**](bankonme-archive.txt). **bankonme precedes bankonOS.**
2. **bankonvault (the umbrella).** [github.com/bankonvault](https://github.com/bankonvault) — the
   key-management / encrypted-storage / OpenPGP collection; `password-manager` is its client-side vault.
3. **Legacy Tomb/LUKS scripts.** The `bankonvault.sh` family (GNU Tomb → LUKS loopback) — the source of
   the operational ceremony that `bankon-vault` keeps (swap-off, trap-cleanup zeroization, inactivity
   auto-lock, `shred` delete, strict perms).
4. **mindX bankon_vault (the canonical crypto).** AES-256-GCM + two-stage HKDF-SHA512, per-entry AAD,
   the overseer custody model, and the **sign-don't-export oracle** — reproduced in the module.
   Its client sibling `walletcreator` binds the master key to a **wallet signature**.
5. **bankoneth (the ETH sibling).** The BANKON ENS/registrar stack (viem/SIWE, `BankonAuthGate`) that
   proved signature-gated sessions on Ethereum; `bankon-vault` generalises it to Bitcoin.
6. **gnugui / GNUVAULT (the abstraction, our work).** [github.com/gnugui/GNUVAULT](https://github.com/gnugui/GNUVAULT)
   — the cleanest chain-agnostic vault (Overseer.material(), tomb/mausoleum, airgap), GPLv3. The
   module's `overseer.py` and license descend from it.
7. **cypherpunk2048 / CP2048-QR (the standard).** [github.com/cypherpunk2048](https://github.com/cypherpunk2048)
   — non-custodial, per-connection client-side keys, crypto-agile (hybrid-PQC ready). BANKON conforms.

## bankonOS (context, honestly)

**bankonOS** is the author's sovereign-workstation tooling/design. It **favours Alpine and OpenBSD,
with Debian compatibility** (the on-disk Ubuntu-22.04 provisioning kit is one deb-compat instance of
that design, not the whole thing). It is documented here as **lineage/tools context — it is not a
current public repo** — and BANKON vendors none of it.

## What bankon-vault delivers on the plan

The decades-old plan — *encrypted, offline, client-controlled Bitcoin keys* — is now a single clean
module: [`bankon-vault/`](../bankon-vault/README.md). AES-256-GCM at rest, **signature-gated access**,
**sign-don't-export** PSBT signing, and in ICE, **frozen (very-cold) storage** that will not thaw to
sign unless the host is air-gapped. Chain-agnostic core; Bitcoin first. See
[`bankon-vault/LINEAGE.md`](../bankon-vault/LINEAGE.md).

## Exclusions (secrets hygiene)

Historical run logs and credential files that contain real key material are treated as **burned** and
are never read into, copied to, or referenced-by-content in this repository. Only *projects* are cited.
