# Changelog

All notable changes to the BANKON tools monorepo. Component versions are independent:
**bankon-vault** (`bankon_vault.core.VAULT_VERSION`), **bankon-ord** (`bankon_ord.__version__`),
**bankonOS installer** (`bankonos/install.sh`).

## 2026-07-14 — bankon-ord 0.3.0-alpha: LIVE inscribe/send verified + deploy choice

The last feasible ledger item for ord — **the live mutation flow, executed for real** (regtest:
reproducible, zero sync, and it ran BESIDE the live mainnet node without touching it — separate
ports and a /tmp datadir; the external-drive blockchain was never involved).

- **Live integration test** (`tests/test_live_regtest.py`, self-skipping without ord/bitcoind):
  throwaway regtest bitcoind → `ord server` → create ordinal wallet → fund →
  **`inscribe_gated(dry_run=False)`** → confirm → list → **`send_gated(dry_run=False)`** →
  confirm → cardinal-wallet refusal verified **on the live path**. First live run:
  inscription `32a60420…272f111bi0`, send txid `c66b6e67…2b2bfa77` — the whole gated write
  path is real now, not just dry-run.
- **`OrdCli(server_url=…)`**: modern ord (≥ 0.18) wallet commands need a running `ord server`;
  wallet subcommands now splice `--server-url` automatically.
- **Deploy choice in install.sh**: `ORD_SOURCE=source | binary | fork | auto`. Auto prefers a
  **source build** when cargo exists, because the official prebuilt is linked against
  glibc ≥ 2.38 and simply won't run on stable hosts (verified: it hard-fails on this
  glibc-2.35 machine; the cargo build of ord 0.27.1 runs perfectly). The binary path checks
  the host glibc BEFORE installing and says why it refuses.
- Bug found BY the live test: `_core_ok` ignored the `BANKON_BTC_DATADIR` override that the
  rest of the module honors — preflight reported an unreachable Core on custom datadirs. Fixed.

## 2026-07-14 — bankon-vault 1.7.0

**BIP-322 spec coverage complete: 10/10 generated full-variant types** — the two time-lock
types land via template matching, still with no script interpreter.

- The canonical hodl script `OP_IF <pk1> OP_ELSE <n> CLTV/CSV OP_DROP <pk2> OP_ENDIF
  OP_CHECKSIG` is recognized in both p2wsh (ECDSA) and **p2tr script-path** form (tapscript
  leaf + BIP-341 control-block verification: TapLeaf/TapBranch merkle fold, x-only tweak must
  reproduce the address's output key; `ext_flag=1` script-path sighash).
- The IF branch verifies against pk1 with no time condition; the ELSE branch **enforces real
  BIP-65/BIP-112 semantics** before any signature check: CLTV needs locktime ≥ n, matching
  time/height type, sequence ≠ 0xFFFFFFFF; CSV needs tx v2+, disable flag clear, matching type
  bits, masked value ≥ n. Nine unit checks pin those rules; both spec vectors verify and both
  reject a tampered message.
- Scoreboard against `bip-0322/generated-test-vectors.json`: **full 10/10 · simple 4/4 ·
  basic 6/6 · error 0/28 leaked**. What remains refused is refused for stated reasons:
  proof-of-funds payloads (UTXO-set context) and scripts outside the covered templates.

Vault suite: 22 tests; monorepo total 72.

## 2026-07-14 — bankon-vault 1.6.0

**BIP-322 FULL variant** — the payload is the complete `to_sign` transaction (any
version/locktime/sequence), whose single input must spend the virtual `to_spend` for
(message, address).

- `verify_message_bip322_full()`: **8 of the spec's 10 generated full-variant types verify** —
  p2pkh (legacy scriptSig + legacy sighash), p2wpkh, p2sh-p2wpkh, p2tr key-path,
  p2wsh K-of-N, p2sh-p2wsh K-of-N, and legacy p2sh K-of-N multisig. All **28 error vectors**
  from `generated-test-vectors.json` rejected; the 4 simple-variant generated vectors still
  verify (regression). Honest refusals, stated in SECURITY.md: time-locked scripts (arbitrary
  script evaluation) and proof-of-funds payloads with extra inputs (need UTXO-set context).
- `sign_message_bip322(variant="full")` — emit the full-variant payload for wpkh/tr.
- Internals: the simple/full paths now share one `_bip322_verify_input()` (the sighash is
  computed over the *provided* tx in full mode, the canonical one in simple mode); new
  fail-closed scriptSig push parser (`_script_pushes` — any non-push opcode → None).
- Unified `verify_message` dispatch: address + witness payload → simple, then full fallback.

Vault suite: 21 tests; monorepo total 71.

## 2026-07-13 — bankon-qt: optional 🜚 Ordinals tab (ord 0.2.1-alpha)

The ord README's "optional Qt panel" item, delivered inside the read-only contract:

- New `OrdinalsTab` in bankon-qt — toolbar checkbox **🜚 Ordinals**, default OFF, lazy
  build/destroy exactly like the Geo Map toggle ("default off = nothing running"); inserts
  before RPC Console.
- **Read-only only**: preflight (honest readiness report), wallet balance / inscriptions /
  outputs, all off the UI thread (FnWorker). Every mutating action (inscribe/send/etch/mint)
  stays in bankon-ord's gated CLI — the tab says so on its face.
- Live **isolation badge** while typing a wallet name (🜚 ordinal / ⛔ cardinal via
  `is_ordinal_wallet`), so the ordinal-vs-cardinal rule is visible before anything is queried.
- Degrades honestly: missing bankon-ord module → status says so; missing `ord` binary → the
  preflight report says so. Verified offscreen: tab construction, badge logic, module wiring,
  preflight report, and Main-window toggle insert/remove.

## 2026-07-13 — bankon-vault 1.5.0

**BIP-322 for p2wsh K-of-N multisig** — closes the "needs a script interpreter" gap for the case
that matters: the standard multisig template doesn't need one; its CHECKMULTISIG semantics
(ordered signatures, exactly K, all SIGHASH_ALL, script must hash to the address) are applied
directly.

- `verify_message_bip322` grows a p2wsh branch — verified against the spec's 3-of-3 vector from
  `bip-0322/basic-test-vectors.json` (valid → address; wrong message / wrong address → None).
- **Cosigner flow**: `bip322_multisig_partial()` (each signer contributes one sig over the
  virtual-tx sighash) + `bip322_multisig_assemble()` (matches sigs to pubkeys, orders them per
  CHECKMULTISIG, drops junk, refuses below quorum). Round-trip proven with our own 2-of-3:
  partials supplied in the wrong order assemble correctly and verify through the unified
  `verify_message` dispatch; under-quorum assembly raises.
- Remaining (documented): BIP-322 *full* variant and truly arbitrary scripts.

Vault suite: 19 tests; monorepo total 69.

## 2026-07-13 — bankon-vault 1.4.0 · bankon-ord 0.2.0-alpha

### bankon-vault 1.4.0 — master re-key / custody rotation

`BankonVault.rekey(new_overseer)` — rotate the vault master to ANY new overseer, **two-phase and
verified**: every entry is decrypted, re-encrypted under the new key, and round-trip byte-compared
*in memory*; only then is the store written (atomic write-tmp→fsync→rename) and the RAM key
swapped. A failure at any point — bad evidence, same-master no-op, verification mismatch, disk
refusal — leaves the vault exactly as it was, old custody intact.

This completes the hybrid-PQC story: `bankon-vault pqc enroll` then **`bankon-vault rekey
--hybrid`** migrates an *existing* classical vault into ML-KEM hybrid custody (previously enroll
only worked for new vaults). Plain `rekey` rotates to a new passphrase. Tests: rotation works and
old custody stops decrypting; fail-closed matrix (same master, locked vault) leaves everything
readable; enroll→rekey→hybrid-unlock proven end-to-end (`test_rekey_into_hybrid_custody`).

### bankon-ord 0.2.0-alpha — gated rune etch/mint

- `OrdCli.mint_gated()` (`ord wallet mint`) and `OrdCli.etch_gated()` (modern batchfile etching
  via `ord wallet batch` — the YAML is generated and, in dry-run, returned verbatim so a human
  reviews EXACTLY what would be etched before a sat moves). Both run the same fail-closed
  `guard_mutation` gates as inscribe/send: ordinal wallet only, no material funds, known balance,
  human approval; `divisibility` bounded to the runes consensus range (0–38).
- `validate_rune_name()`: A–Z with `•` spacers (`.` accepted as alias), 1–26 letters, no
  leading/trailing/double spacers — typos and shell-adjacent garbage are rejected before any fee
  is spent.

Monorepo: 67 tests green (44 vault-module · 14 ord · 9 blackICE).

## 2026-07-13 — bankon-vault 1.3.0

**CP2048-QR roadmap complete** — the post-quantum items SECURITY.md promised are now code:

- **Hybrid-PQC custody** (`pqc_hybrid.py`, `HybridPQCOverseer`): the vault master becomes
  `HKDF-SHA512(classical_material ‖ ML-KEM-768 shared secret)` (FIPS 203). Wraps ANY inner
  overseer; never weaker than the classical layer alone. Enrollment (`bankon-vault pqc enroll`)
  stores only public artifacts (`.pqc.json`: KEM ciphertext + ungrindable ss-commitment); the
  decapsulation key goes to the operator, offline. Wrong key fails EARLY at the commitment
  (defeats ML-KEM implicit rejection); classical-only unlock of a hybrid vault fails; missing
  `.pqc.json` refuses rather than silently degrading.
- **ML-DSA (FIPS 204) identity + post-quantum quorum** (`pqc_mldsa.py`):
  `make_verifier()` plugs into `PolicyEngine(verify_sig=…)` — N-of-M signing approval can be
  collected from ML-DSA keys, making the *authorization* layer post-quantum while BTC signing
  stays secp256k1 (consensus-frozen, honestly declared).
- **Tier-Q chain adapter** (`chains/pqc.py`, `MLDSAAdapter`): PQ identity keys minted/stored/
  used through the normal chain-agnostic vault flow; `sign_psbt` refuses with the honest reason.
- Design note recorded: PQC *signatures* (Falcon/ML-DSA) are randomized and cannot replace the
  deterministic signature-as-IKM trick — a KEM is the correct primitive for PQ custody, which is
  exactly what the roadmap specified.
- Backends: pure-Python `kyber-py`/`dilithium-py` (POC-grade, installed opportunistically by
  install.sh and in CI) or `liboqs`; `bankon-vault pqc status` reports the truth; every module
  degrades honestly. New `tests/test_pqc.py` (6 tests, self-skipping without backends):
  hybrid roundtrip, fail-closed matrix, ML-DSA sign/verify/tamper, PQ quorum through the real
  PolicyEngine, adapter identity + refusal. Monorepo total: 62 tests.

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
