# bankon-vault

**The definitive chain-agnostic vault — Bitcoin-first.** An isolated, dependency-light module that
holds secrets encrypted at rest and grants **signature-gated access** to them — for BANKON BTC and
for *any* other chain or project. It fulfils the decades-old [github.com/bankonme](https://github.com/bankonme)
plan — *be your own bank, keys offline* — as a clean, auditable, non-custodial primitive.

- **Standard:** [cypherpunk2048 / CP2048-QR](https://github.com/cypherpunk2048) — non-custodial,
  client-side keys, crypto-agile.
- **License:** GPL-3.0-or-later (client-side crypto stays free and auditable — the GNUVAULT lineage).
- **No daemon, no LUKS/loopback required, no network for key ops.** Just files you can carry to a cold drive.

---

## Why it exists

BANKON never custodies keys. The dangerous instants are **key generation** and **signing**, when a
secret briefly lives in memory. `bankon-vault` makes those instants safe: the secret is encrypted at
rest, the master key exists only in RAM (in a wipeable buffer), and **the private key never leaves the
module** — you hand it a PSBT and it hands back a *signed* PSBT. Access is **gated by a wallet
signature** and, for spends, by an **explicit per-sign approval**. In ICE it becomes **frozen
storage**: it will not thaw to sign unless the host is air-gapped.

## Architecture

```
bankon_vault/
  core.py        BankonVault — AES-256-GCM + two-stage HKDF-SHA512, AAD=id, RAM-only master,
                 bytearray zeroization, _secure_write (0600+fsync), inactivity auto-lock
  overseer.py    custody: PassphraseOverseer · KeyfileOverseer · WalletSignatureOverseer
  policy.py      gating: DenyAll (default) · ApprovalGate (alpha) · Policy (Step-3 stub) · gated_sign_psbt
  api.py         VaultOracle — loopback HTTP; returns SIGNED PSBTs only, never key material
  cli.py         `bankon-vault` command
  chains/
    base.py      ChainAdapter ABC (derive · address · sign_message · verify_message · sign_psbt)
    btc.py       BitcoinAdapter (embit) — BIP32/39/44/84/86, bech32 P2WPKH + P2TR, PSBT, BSM-ECDSA gating
clients/vault-client.mjs   thin JS/HTTP client (WaaS/offline pages → gated signature, never a key)
tests/         11 tests: HKDF, GCM nonce-uniqueness, AAD binding, wrong-pass, lock, addresses, gating, PSBT
install.sh     one-shot installer (deps → self-check → tests → launcher)
```

### The cryptography (what actually protects your keys)

| Layer | Construction |
|---|---|
| Master material | supplied by an **Overseer** — never persisted. Passphrase → PBKDF2-HMAC-SHA512 (600k); key file → 64 random bytes (0400); **wallet signature → the signature itself is the IKM** |
| Master → vault key | `HKDF-SHA512(ikm, salt=.salt, info="bankon-vault-master-key", 32B)` |
| Per-entry subkey | `HKDF-SHA512(vault_key, salt, info="bankon-vault-entry:<id>", 32B)` — domain separation per secret |
| Record cipher | **AES-256-GCM**, fresh 96-bit nonce every write, **AAD = entry_id** (rename a record → decrypt fails) |
| On disk | `entries.json` (`{version,cipher,kdf,entries:[{id,nonce,ct,context,…}]}`, 0600) + `.salt` (32B, 0600). Portable across hosts; no loopback/LUKS |

**Discipline:** master key held in a `bytearray` and zeroed on `lock()` (a `str` can't be wiped);
**unlock → act → relock** minimises the open window; `_secure_write` creates 0600 with umask + fsync
(no create-0644→chmod race) and fsyncs the directory; an **inactivity timer auto-locks** a forgotten
vault.

### Signature access & gating

- **Who may open** the vault: `WalletSignatureOverseer` — the master key is derived from a secp256k1
  signature over a fixed challenge; the signature is never stored. No signer, no vault.
- **Whether a given transaction signs**: the `Gate`. Alpha = `ApprovalGate` — the approver is shown the
  **decoded PSBT** (inputs, outputs, amounts, fee) and must confirm. Default is `DenyAll` (fail-closed).
  Programmable policy (spend limits / allowlists / timelocks / N-of-M) is the Step-3 `Policy` stub.
- **Sign-don't-export**: `gated_sign_psbt()` retrieves the seed, signs the PSBT, and **wipes the
  plaintext** — the key is never returned by any function or HTTP endpoint. (Verified by the test suite
  and by the oracle/JS client, which reject any reply containing key material.)

## Installation

```bash
# from the repo:
bash bankon-vault/install.sh
# or piped:
curl -fsSL https://raw.githubusercontent.com/cypherpunk2048/bankon-tools/main/bankon-vault/install.sh | bash
```
The installer installs `cryptography` + `embit` (both pure-Python / offline-friendly), runs a crypto
self-check **and the full test suite**, and drops a `bankon-vault` launcher in `~/.local/bin`.
Manual: `python3 -m pip install --user cryptography embit`, then `python3 -m bankon_vault.cli …`.

## Usage summary

```bash
bankon-vault init                              # create/open an encrypted vault (passphrase-gated)
bankon-vault gen-btc  --net regtest            # mint a new BIP-39 mnemonic, store it
bankon-vault import-btc --id btc.seed          # store an existing mnemonic / xprv
bankon-vault address  --id btc.seed --kind wpkh   # bc1q… (or --kind tr for bc1p… taproot)
bankon-vault list                              # entries (metadata only — never secrets)
bankon-vault sign     --id btc.seed --psbt -@unsigned.psbt   # REVIEW & APPROVE → prints signed PSBT
bankon-vault serve    --port 8099              # loopback signing oracle (returns signed PSBTs only)
```

Library:
```python
from bankon_vault import BankonVault, PassphraseOverseer
from bankon_vault.chains.btc import BitcoinAdapter
from bankon_vault.policy import ApprovalGate, gated_sign_psbt

v = BankonVault("~/.bankon-vault")
salt = open(v.salt_file, "rb").read()
with v.session(PassphraseOverseer("…", salt)):          # unlock → auto-lock on exit
    v.store("btc.seed", BitcoinAdapter().new_secret(), context="bitcoin_wallet")
    signed = gated_sign_psbt(v, BitcoinAdapter(), "btc.seed", psbt_b64,
                             ApprovalGate(lambda summary: confirm(summary)))
```

Browser / WaaS (never receives a key):
```js
import { VaultClient } from './clients/vault-client.mjs';
const signed = await new VaultClient('http://127.0.0.1:8099').sign('btc.seed', psbtBase64);
```

## Chain-agnostic by construction

The core knows nothing about Bitcoin. To add a chain, implement `chains/base.ChainAdapter`
(`derive/address/sign_message/verify_message/sign_psbt`) — the EVM and Algorand siblings from the
lineage drop in the same way. A non-BTC secret is just another entry:
`vault.store("api.openai", token)`.

## Status

**Alpha** (Step 1 of the stepwise plan). Shipped: agnostic core, three overseers, BTC adapter
(addresses + BSM-ECDSA gating + PSBT sign-don't-export), approval gate, loopback oracle, JS client,
CLI, installer, 11 passing tests. **Next steps:** ordinals (`ord`) alpha (wallet-isolated), the
programmable `Policy` engine, and frozen-storage hardening (Shamir split + LUKS removable-media
automation). See `LINEAGE.md` and `SECURITY.md`.
