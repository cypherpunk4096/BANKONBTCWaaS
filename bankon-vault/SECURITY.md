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

## Trust boundary
- The vault trusts the **local host** and the **overseer**. Anyone who can run code as your user and
  observe RAM while the vault is *unlocked* can read secrets — no software vault can prevent that.
  Minimise the window (auto-lock), and for cold keys use **ICE frozen storage** (unlock only under
  AIRGAP) and/or removable media detached between signings.
- The oracle binds to **127.0.0.1** only, caps request bodies (1 MiB), issues **single-use,
  payload-bound nonces**, recomputes the payload hash server-side (a replayed token can't redirect the
  signature), and supports an optional bearer token (`BANKON_VAULT_TOKEN`, constant-time compared).

## Known limitations (alpha — honest)
- **Gating verifies by pinned pubkey**, not address-recovery: BIP-137 recoverable signatures and full
  **BIP-322** are Step-2 (embit has no recid). Verification against a pinned pubkey is equally sound;
  it just needs the pubkey, not only the address.
- Retrieved plaintext is returned as a `bytearray` you should `for i: buf[i]=0` after use;
  `gated_sign_psbt` does this for you.
- No Shamir split / multi-operator ceremony yet (Step-4). Single-passphrase or single-signer for now.
- `Policy` (limits/allowlists/timelocks/N-of-M) is a documented stub that currently fails closed.
- The vault does **not** protect a already-compromised host or a keylogged passphrase.

## Never in the repo
No key material, `.master.key`, `.salt`, `.enc`, or legacy secret logs are committed
(`.gitignore` blocks them). The `vault_bankon/`-style state lives outside version control.

## Reporting
Open a private GitHub Security Advisory on the repository, subject `BANKON-VAULT SECURITY`. In scope:
anything that exfiltrates a key/seed, escapes the gate, returns key material over any interface, or
breaks the AEAD/HKDF constructions. Acknowledgement within 72h; coordinated disclosure preferred.
