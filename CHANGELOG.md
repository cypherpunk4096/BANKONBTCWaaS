# Changelog

All notable changes to the BANKON tools monorepo. Component versions are independent:
**bankon-vault** (`bankon_vault.core.VAULT_VERSION`), **bankon-ord** (`bankon_ord.__version__`),
**bankonOS installer** (`bankonos/install.sh`).

## 2026-07-13 — bankon-vault 1.2.0

**BIP-322 generic signed messages ('simple' variant)** — message signing/verification for
**taproot key-path** and native segwit, the address types BIP-137 cannot cover. The message is
committed to the spec's virtual `to_spend`/`to_sign` transaction pair; the signature is the
serialized witness stack.

- `BitcoinAdapter.sign_message_bip322(secret, msg, path, kind)` — `wpkh` (BIP-143 ECDSA + pubkey
  witness) and `tr` (BIP-341 key-path Schnorr, SIGHASH_DEFAULT, taproot-tweaked key).
- `BitcoinAdapter.verify_message_bip322(msg, sig, address)` — p2wpkh (pubkey must hash to the
  address) and p2tr key-path (64- or 65-byte Schnorr); every malformed input returns None.
- `verify_message` dispatch: pinned pubkey → BSM-ECDSA; address + 65-byte compact → BIP-137
  recovery; address + anything else → BIP-322. `make_verifier` therefore gates on taproot
  addresses with zero call-site changes.

**Byte-exact against the spec** (`bitcoin/bips` `bip-0322/basic-test-vectors.json`): tagged
message hash, `to_spend`/`to_sign` txids, all p2wpkh + p2tr valid signatures verify, and all
error vectors (bad base64, empty witness, wrong message, wrong address) are rejected — pinned in
`test_bip322_spec_vectors` / `test_bip322_sign_roundtrip_and_dispatch`. Vault suite: 15 tests;
monorepo total 56. Remaining roadmap: BIP-322 *full* variant / arbitrary-script (p2wsh multisig)
verification.

## 2026-07-13 — bankon-vault 1.1.0

**BIP-137 recoverable message signatures** — gating can now pin an **address alone**, closing the
top documented limitation ("verify by pinned pubkey only"). embit's low-level
`secp256k1.ecdsa_sign_recoverable` / `ecdsa_recover` made this possible without new dependencies.

- `BitcoinAdapter.sign_message_compact(secret, msg, path, kind)` → the 65-byte base64 format
  Bitcoin Core's `signmessage` emits; header byte carries recid + address type
  (p2pkh / p2sh-p2wpkh / p2wpkh; taproot correctly refused — that needs BIP-322).
- `BitcoinAdapter.recover_address(msg, sig)` → recovers the signer's pubkey from the compact
  signature and derives the header-declared address type.
- `verify_message(msg, sig, expected)` now dispatches: hex pubkey → raw BSM-ECDSA verify
  (unchanged); anything else is an ADDRESS and the signature must be BIP-137 compact — the
  recovered address must match exactly (fail-closed on tamper, wrong address, malformed sig).
- `make_verifier(pinned)` accepts a pubkey or an address → plugs into `WalletSignatureOverseer`.

**Interoperability proven both directions against Bitcoin Core v31** (throwaway regtest):
a Core-produced `signmessage` signature recovers to the right address here, and our compact
signature returns `true` from Core's `verifymessage`. Both vectors are pinned in the test suite
(`test_bip137_kat_and_core_interop`, `test_bip137_address_pinning_all_kinds_and_fail_closed`).
Vault suite: 13 tests; monorepo total 54.

## 2026-07-13 — bankon-vault 1.0.1 · bankon-ord 0.1.1-alpha · bankonOS 2.0.0

Adversarial-audit release. Three independent audit passes over the vault, ord, and shell
layers; every confirmed defect fixed **with a regression test**. Full suite green:
vault 11 · policy 11 · ceremony 6 · multisig 3 · ord 12 · blackICE 9 = **52 tests**,
plus `deploy.sh --verify` (10 checks) and POSIX `sh -n` across all installers.

### bankon-vault 1.0.1

Security fixes:
- **CRITICAL — `decode_psbt` hid outputs from the gate** (`chains/btc.py`). Empty PSBT
  output-metadata objects compare equal, so `tx.outputs.index(o)` collapsed every output
  onto `vout[0]` — hidden outputs were invisible to policy and human review (consent
  bypass). Now iterates `tx.tx.vout` by position. Regression: `test_decode_shows_all_outputs`.
- **HIGH — oracle nonce was not payload-bound** (`api.py`). A challenge nonce could be
  redeemed against a *different* (entry_id, PSBT) — the binding was claimed in a comment
  but not implemented. `_NONCES` now stores `(expiry, payload_sha256)`; `/sign` recomputes
  and constant-time-compares the payload hash; `_prune` updated for the tuple value.
  Regression: `test_oracle_rejects_payload_swap` (rebinding rejected, happy path works,
  replay still blocked).
- `PolicyEngine.max_fee_sats` now **fails closed when the fee is unknown** (`fee=None`
  previously passed a fee-capped policy).
- `unlock()` zeroizes + `munlock`s the **old** master key before installing a new one
  (re-unlock no longer leaked the prior key in RAM or left pages pinned).
- `destroy(force=True)` Python fallback now chmods read-only files before shredding.
- `retrieve()` no longer loses a valid decrypt if the post-read metadata save hits a
  full/read-only disk.

Docs: README/SECURITY updated to state what is actually shipped (PolicyEngine,
Shamir K-of-N ceremony) and the real test count.

### bankon-ord 0.1.1-alpha

- **Wallet isolation hardened** (`isolation.py`): ordinal markers now match as **token
  prefixes**, not substrings — `landlord`, `wordpress`, `accord`, `password` no longer
  qualify as ordinal wallets, while `ordinals`/`inscriptions` plurals still do.
  Regression: `test_isolation_rejects_cardinal_substrings`.
- **`guard_mutation` fails closed on unknown balance** (a balance-fetch hiccup can no
  longer wave a hot wallet through); explicit `allow_unknown_balance=True` to override.
  Regression: `test_guard_fails_closed_on_unknown_balance`.
- `ord_cli.py`: `inscription()` no longer calls `list` with a non-outpoint arg; new
  `output()` method for outpoint lookups.

### bankonOS 2.0.0 (fixes)

- `deploy.sh --help` works (usage() defined before the arg loop, exits 0).
- `COINS=` values are character-validated (shell-injection rejected).
- `--log` flag parsing no longer aborts under `set -e` in `deploy.sh` / `install.sh`.
- Enclave apkovl tar is built with `--numeric-owner --owner=0 --group=0` (was uid 1000).
- `.gitignore` covers `_stage/`, `_lb/`, `*.apkovl.tar.gz` build artifacts repo-wide.

### Verified not-bugs (audited, confirmed sound)

HKDF salt reuse (correct domain separation), GCM nonce handling, Shamir GF(2⁸) table
indexing, mlock ctypes usage, testnet cookie subdir, oracle loopback binding.
