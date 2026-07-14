# bankon-vault — Security

`bankon-vault` holds private keys. This is its threat model and disclosure policy. It conforms to the
[cypherpunk2048 / CP2048-QR](https://github.com/cypherpunk2048) standard (non-custodial, client-side
keys, crypto-agile).

## Guarantees (enforced in code, verified by tests)
1. **The private key never leaves the module.** `sign_psbt` / `gated_sign_psbt` return a *signed PSBT*;
   the oracle and JS client actively reject any reply containing `mnemonic|xprv|wif|privkey|seed`.
2. **Master material is never persisted.** Only the random 32-byte `.salt` is stored. The master key
   exists only in RAM, in a `bytearray`, and `lock()` zeroes it. Unlock → act → relock; inactivity
   auto-lock; the `session()` context manager locks on exit.
3. **AEAD binding.** AES-256-GCM with `AAD = entry_id` — a record's ciphertext is bound to its name; a
   rename or a wrong passphrase yields `InvalidTag`, never silent garbage.
4. **Per-entry domain separation.** Every secret uses its own HKDF-SHA512 subkey; compromising one
   entry's derived key does not help against another.
5. **Fail-closed gating.** No signature is produced unless a `Gate` explicitly approves; the default
   gate is `DenyAll`. Spends require an explicit per-sign approval that is shown the decoded PSBT.
6. **Signature-bound custody.** `WalletSignatureOverseer` derives the master from a wallet signature
   that is never stored — lose the signer, lose the vault (non-custodial).

## cypherpunk2048 / CP2048-QR conformance (all key handling)
Every key path in this module is held to the [cypherpunk2048](https://github.com/cypherpunk2048)
standard. Point-by-point:

| Tenet | How bankon-vault meets it |
|---|---|
| **Non-custodial** | no server or third party ever holds a key. The master material is never persisted (only the random 32-byte `.salt`); the private key never leaves the module (`sign_psbt` returns a *signed PSBT*; the oracle/JS client reject any reply with key material). |
| **Client-side keys** | keys are minted, stored, split and used **locally**; no network is required for any key operation. The oracle binds to loopback only. |
| **≥112-bit symmetric-equivalent** | exceeded everywhere: **AES-256-GCM** (256-bit), **HKDF-SHA512**, **PBKDF2-HMAC-SHA512 @ 600k**, **GF(2⁸) Shamir**, **secp256k1** (~128-bit). No MD5/SHA-1/DES/ECB; no `random.random` — entropy is `os.urandom` throughout, including Shamir coefficients. |
| **Verification replaces trust** | AEAD `AAD=entry_id` binds every record; the ceremony **manifest fingerprint** proves a reconstruction is the original; the policy **quorum** verifies each approver signature; migration is **round-trip byte-verified**. |
| **Crypto-agility (PQC-ready)** | the `Overseer` / `ChainAdapter` abstractions isolate the primitives so they can be swapped. *Honest note:* the signing curve is classical **secp256k1** because **Bitcoin itself is pre-PQC** — a hybrid-PQC overseer (e.g. layering a PQC KEM over the master material) is the roadmap and does not require changing the vault core. |
| **Zeroization** | the master key lives in a `bytearray` and is wiped on `lock()`; retrieved plaintext, the reconstructed Shamir master, and the signing seed are all zeroed after use. |

## Quantum posture — CP2048-QR (declared honestly)
The [CP2048-QR](https://github.com/cypherpunk2048/quantum-standard) standard insists systems **declare
their real capability, not the aspirational one.** So, honestly:

- **Symmetric / hash layer — already PQC-baseline.** AES-256-GCM + HKDF/PBKDF2-**SHA-512** meet the
  standard's "AES-256 + SHA-512/SHA-3, ≥112-bit floor." Grover only halves symmetric strength, so
  AES-256 stays ~128-bit post-quantum — fine.
- **Signature layer — Tier-C (classical), by necessity.** BTC signing is **secp256k1 (ECDSA)**, which
  Shor's algorithm breaks — but **Bitcoin itself is pre-PQC**, so no vault can change that today.
  bankon-vault is therefore **Tier-C for BTC signing** and does not pretend otherwise.
- **Crypto-agile — Tier-A ready.** The `Overseer`/`ChainAdapter`/gate abstractions make the signature
  scheme **swappable, not hard-wired**, per CP2048-QR — so a PQC signer drops in without touching the
  core. Roadmap: an **FN-DSA (Falcon) / ML-DSA** overseer+adapter, following **Algorand's Falcon**
  quantum-native initiative (the first Tier-Q mainnet, Nov 2025); the multi-chain `walletcreator`
  already carries Algorand keys, so Falcon is the natural next adapter.
- **Key custody — conformant.** Client-side keys, **no server escrow, one key per use, open source** —
  exactly CP2048-QR's custody mandate.

> **Honesty note on `shred --pow2 8192`:** more overwrite passes is **anti-forensic depth** (defeating
> disk-recovery of erased data), **not** quantum resistance. Quantum resistance lives in the signature
> algorithm above, not in how many times a file is overwritten. The option exists for those who want
> maximal overwrite; it makes no quantum claim.

## Trust boundary
- The vault trusts the **local host** and the **overseer**. Anyone who can run code as your user and
  observe RAM while the vault is *unlocked* can read secrets — no software vault can prevent that.
  Minimise the window (auto-lock), and for cold keys use **ICE frozen storage** (unlock only under
  AIRGAP) and/or removable media detached between signings.
- The oracle binds to **127.0.0.1** only, caps request bodies (1 MiB), issues **single-use,
  payload-bound nonces**, recomputes the payload hash server-side (a replayed token can't redirect the
  signature), and supports an optional bearer token (`BANKON_VAULT_TOKEN`, constant-time compared).

## Prevention from eyes — RAM, mlock, swap, TEE
Keeping a live key away from observers is a layered problem; the vault does what software can and is
honest about what needs hardware:

- **`mlock` (done).** On unlock the 32-byte master key's pages are pinned with `mlock` so they can
  **never be swapped to disk** — no plaintext key ever lands in swap for later forensics. `munlock` +
  zeroize on `lock()`. `info()` reports `mlocked`.
- **RAM-backed vault (supported).** Point the vault at a **tmpfs/ramfs** mount (e.g. `/dev/shm`) and
  the encrypted store lives entirely in RAM — nothing hits disk, and it's gone on power-off (the
  amnesic model). `info()` reports `on_ram_fs`. For a fully cold path, combine with the **GNU Tomb**
  LUKS backend (closed = plaintext gone).
- **Swap (advisory).** `info()` reports `swap_active`; for high-value key ops run `swapoff -a` (or use
  encrypted swap) so nothing can page out. ICE can do this as part of a "freeze" ceremony.
- **TEE (hardware-gated, honest).** A true Trusted Execution Environment (Intel SGX, ARM TrustZone,
  AMD SEV) hides keys even from root — the strongest "prevention from eyes." It requires modern
  hardware; the reference machine (HD 3000) has none, so this is a roadmap item behind a secure
  element / hardware wallet, not a software claim.
- **Residual (unavoidable in software):** a root-level attacker can read live process RAM while the
  vault is *unlocked*, and cold-boot/DMA can read RAM briefly after power-off. Minimise the unlocked
  window (auto-lock), create keys **air-gapped via ICE**, and prefer the Tomb/RAM path for cold keys.

## Known limitations (honest)
- **Gating verifies by pinned pubkey OR by address** — **BIP-137 recoverable signatures are shipped**
  (`sign_message_compact` / `recover_address`, cross-verified against Bitcoin Core v31
  `signmessage`/`verifymessage` in both directions). Full **BIP-322** (incl. taproot message
  signing) remains roadmap — BIP-137 covers p2pkh / p2sh-p2wpkh / p2wpkh only.
- Retrieved plaintext is returned as a `bytearray` you should `for i: buf[i]=0` after use;
  `gated_sign_psbt` does this for you.
- Shamir K-of-N split + multi-operator ceremony are **shipped** (`shamir.py`, `ceremony.py`) — or use
  a single passphrase / signer if you don't need quorum custody.
- `PolicyEngine` (limits/allowlists/timelocks/N-of-M quorum + audit) is **implemented and tested**;
  it fails closed on unknown fee and on quorum shortfall.
- The vault does **not** protect a already-compromised host or a keylogged passphrase.

## Never in the repo
No key material, `.master.key`, `.salt`, `.enc`, or legacy secret logs are committed
(`.gitignore` blocks them). The `vault_bankon/`-style state lives outside version control.

## Reporting
Open a private GitHub Security Advisory on the repository, subject `BANKON-VAULT SECURITY`. In scope:
anything that exfiltrates a key/seed, escapes the gate, returns key material over any interface, or
breaks the AEAD/HKDF constructions. Acknowledgement within 72h; coordinated disclosure preferred.
