# USAGE — BANKON Vault: Tomb + Multi-Chain Quorum

Complete technical and operational guide for the `bankon_vault` project: an encrypted-secrets
substrate (Tomb / dm-crypt / LUKS) whose key is governed by an N-of-M owner quorum anchored
immutably on multiple EVM chains and mirrored to Algorand for the parsec/x402 payment side.

---

## 1. What this project is, in one diagram

```
                        ┌──────────────────────────────────────────────┐
                        │  HOST (GNU/Linux, swap off)                   │
                        │                                              │
   /mnt/usb (or SSH) ─▶ │  operator.tomb.key ──┐                       │
                        │                      ▼                       │
                        │  operator.tomb ──▶ tomb open ──▶ /media/...  │
                        │        │               │                     │
                        │        │          bind-hooks: .gnupg,        │
                        │        │          .password-store (pass-tomb)│
                        │        │          parsec-wallet creds,       │
                        │        │          mindx .env                 │
                        │        │          exec-hooks: start mindX    │
                        │        │          agent + x402 bridge        │
                        └────────┼──────────────────────────────────────┘
                                 │  sha256(operator.tomb.key) = COMMITMENT
                 ┌───────────────┼──────────────────────┬───────────────────┐
                 ▼               ▼                      ▼                   ▼
        EVM PRIMARY       EVM anchors (same        Algorand mirror     Local check
        VaultQuorum       CREATE2 address on       VaultQuorumMirror   (quorum.py
        quorum → unlock   Base/Arbitrum/OP/…)      (parsec/x402 side)  reconstitute)
        instantly         quorum → 7d timelock     owner attestations
```

One secret hash, anchored everywhere, mutable nowhere. The Tomb key never touches any network.

---

## 2. Components

| Path | What it is |
|---|---|
| `contracts/VaultQuorum.sol` | Immutable N-of-M approval ledger. Primary chain unlocks at quorum; other chains arm a break-glass timelock. No admin, no upgrade. |
| `script/DeployVaultQuorum.s.sol` | Foundry CREATE2 deploy — same salt + same args ⇒ same address on every EVM chain. Predicts, deploys, asserts. |
| `test/VaultQuorum.t.sol` | Foundry tests: threshold, timelock, non-owner rejection, double-approve rejection, cross-chain commitment equality. |
| `bankon_vault/tomb.py` *(guide §4)* | Python control plane over the `tomb` CLI — dig/forge/lock/open/close/slam, argon2 KDF defaults, pinentry-secure by default. |
| `bankon_vault/quorum.py` *(guide §5)* | Offline Shamir split/merge of the Tomb key + commitment check on reconstitution. |
| `bankon_vault/multichain.py` | Reads every chain's anchor; **fails closed** if any commitment diverges from the others or from the local key. |
| `algorand/vault_quorum_mirror.py` | Algopy ARC-4 app: same commitment anchored on Algorand; owner attestations let parsec-wallet / X402AccessGate check unlock state without an EVM RPC. |
| `deploy/deploy-all.sh` | Deploys to every chain in `chains.json` (derived from agenticplace.pythai.net/allchain.html). |
| `deploy/deploy_algorand.py` | Compiles/deploys the mirror, verifies its commitment against the local key. |
| `MULTICHAIN.md` | Design rationale: why same-address, why primary + timelock, why the Algorand mirror is non-authoritative. |
| `BANKON_VAULT_Tomb_Integration_Guide.md` | The full Tomb inclusion guide: all commands, all compatible UIs (pass-tomb, Secrets, Mausoleum, zuluCrypt), hooks, security standard, zuluCrypt→bankon_vault fork blueprint. |

---

## 3. Technical model

### 3.1 The commitment
`COMMITMENT = sha256(operator.tomb.key)` — 32 bytes computed on the host. It is the **only** thing
any chain ever sees. It reveals nothing usable (preimage-resistant over a high-entropy key) but
lets anyone verify a candidate key without trusting the party presenting it.

### 3.2 Integrity, three layers
1. **On-chain immutability.** `VaultQuorum` has no owner-mutation, no upgrade, no admin. The
   Algorand mirror ships no update/delete handlers, which under AVM rules makes the app permanently
   immutable. What was deployed is what exists, forever.
2. **Cross-chain agreement.** Identical CREATE2 address on every EVM chain must report an identical
   commitment; the Algorand mirror must report it too. `multichain.py` + `deploy_algorand.py verify`
   check all of them and raise on any divergence. A single lying chain is self-exposing.
3. **Local verification.** `quorum.py::reconstitute` re-hashes the merged Shamir shares and refuses
   to write a key that doesn't match the commitment. Even a colluding quorum cannot substitute a
   different key without detection.

### 3.3 Authority model
- **Primary EVM chain** (constructor arg `primaryChainId`, identical everywhere): quorum ⇒
  `unlocked()` immediately.
- **Every other EVM chain**: quorum ⇒ arms `fallbackReadyAt = now + fallbackDelay` (default 7 days).
  This is break-glass: if the primary is censored, halted, or its approver set is compromised,
  owners regroup on any anchor chain and the world gets a week of public notice before it
  authorizes. An attacker who subverts only a weak chain gains nothing fast.
- **Algorand mirror**: never authoritative. `mirrored_unlocked()` is an owner-attested *reflection*
  of the EVM unlock so Algorand-native services don't need EVM RPC access.

### 3.4 Why CREATE2 / deterministic addresses
`address = f(deployer, salt, keccak256(initcode))`. Constructor args are part of initcode, so
identical args ⇒ identical initcode ⇒ identical address via the canonical deterministic deployer
(`0x4e59...956C`, present or auto-provisioned by Foundry on all major chains). One address to put
in mindX config, in the AgenticPlace registry entry, on paper in the safe. Rotating the key means
bumping the salt (`.../v2`) and deploying a fresh set — old anchors remain as permanent history.

### 3.5 Key lifecycle
```
forge (argon2 KDF) ─▶ lock tomb ─▶ sha256 ─▶ deploy anchors (EVM×N + Algorand)
        │
        ├─▶ engrave (QR on paper)          cold backups of the
        ├─▶ bury (steganography in JPEG)   password-protected key
        └─▶ Shamir split (M shares) ─▶ distribute to DAIO owners out-of-band
                                          │
        loss/compromise event ─▶ owners approve() on-chain ─▶ unlocked
                                          │
        ≥N shares merged offline ─▶ reconstitute ─▶ commitment check ─▶ key restored
                                          │
        then: tomb setkey (new key) ─▶ new commitment ─▶ deploy v2 anchors
```
**Rule: an unlocked quorum means the key was reconstructed — always rotate afterwards.**
`unlocked = true` is public; treat it as the old key being burned.

---

## 4. Use cases

### UC-1 · Solo operator, daily driver
You, one machine, one vault. Key on USB, tomb on disk.
```bash
tomb open /opt/vaults/operator.tomb -k /mnt/usb/operator.tomb.key
# exec-hooks start mindx-agent + parsec-x402-bridge; bind-hooks expose .gnupg, pass tree
tomb slam all        # panic close: everything vanishes, services stop
```
The multi-chain quorum here is pure **disaster recovery**: you hold 1 share, family/lawyer/safe-
deposit hold the rest. Lose the USB and your own quorum restores you.

### UC-2 · DAIO treasury custody (the core case)
The DAIO's operational signing keys live inside the vault. The vault key is Shamir-split 3-of-5
across counsellors. No single counsellor — including you — can open the treasury vault alone.
On-chain `approve()` calls are the public, timestamped record of *who* authorized custody access
and *when*, on the primary chain, visible from every anchor chain. Governance meets opsec.

### UC-3 · Dead-man / succession
Owner incapacitated. Remaining shareholders reach quorum on the **primary** ⇒ immediate. If the
primary itself is unusable, they reach quorum on any anchor chain and the 7-day timelock gives all
watchers (mindX monitors, RAGE publication, other owners) public notice to contest before
authorization. Estate continuity without a custodian.

### UC-4 · x402 payment rail gating (parsec/Algorand)
`X402AccessGate`-style contracts and the parsec-wallet bridge need to know "is the operator vault
in a compromised/recovery state?" without running an EVM light client. They read
`mirrored_unlocked()` and `get_commitment()` from the Algorand app: if the mirror shows unlocked
(= key was reconstructed) the payment bridge can require re-attestation or halt until key rotation
lands (`v2` anchors). Payment safety derives from vault state, natively on Algorand.

### UC-5 · mindX autonomous-agent secrets
mindX's 350+ routes need RPC keys, publish credentials for rage.pythai.net, and wallet material.
All of it lives in the tomb; `exec-hooks` start the agent only after the vault opens, and the
agent's startup check calls `multichain.py.verify_all()` — the agent **refuses to boot** if the
commitment diverges anywhere. An agent that self-verifies its own secret substrate against five
public chains before acting.

### UC-6 · Tamper canary
Run `verify_all` on a cron/systemd timer from an independent watcher box. Because every anchor is
immutable, the only way a report ever changes is RPC compromise or a fraudulent chain state — both
worth an alarm. Cheap, chain-diverse tamper-evidence for the whole identity root.

### UC-7 · Geographic / jurisdictional resilience
Owners in different countries; chains with different validator geographies. No single jurisdiction
can both seize the local vault file *and* suppress the quorum's ability to coordinate recovery,
because coordination can occur on whichever anchor chain remains reachable.

---

## 5. End-to-end runbook

### Phase 0 — host prep
```bash
sudo swapoff -a                          # Tomb aborts on active swap; don't --force past this
sudo apt install zsh file gnupg cryptsetup pinentry-gtk2 qrencode argon2 lsof steghide
# install Tomb 2.12 verified (guide §3.3), or: sudo make install from source
```

### Phase 1 — create the vault
```bash
tomb dig -s 256 /opt/vaults/operator.tomb
tomb forge /mnt/usb/operator.tomb.key --kdf 10 --kdftype argon2 --kdfmem 18 --use-random
tomb lock /opt/vaults/operator.tomb -k /mnt/usb/operator.tomb.key
tomb open /opt/vaults/operator.tomb -k /mnt/usb/operator.tomb.key /media/operator
# install bind-hooks / exec-hooks (guide §6), move secrets in, close
tomb close
```

### Phase 2 — cold backups + Shamir
```bash
tomb engrave -k /mnt/usb/operator.tomb.key        # print QR, hide in a book
tomb bury -k /mnt/usb/operator.tomb.key cover.jpg # steganographic copy
python - <<'PY'
from pathlib import Path
from bankon_vault.quorum import QuorumPolicy, shard, commitment
key = Path("/mnt/usb/operator.tomb.key")
print("COMMITMENT:", commitment(key))
for i, s in enumerate(shard(key, QuorumPolicy(threshold=3, shares=5)), 1):
    Path(f"/mnt/usb/share_{i}.bin").write_bytes(s)   # distribute out-of-band, then wipe
PY
```

### Phase 3 — test, then anchor on EVM mainnets
```bash
export VAULT_COMMITMENT=0x$(sha256sum /mnt/usb/operator.tomb.key | awk '{print $1}')
export VAULT_THRESHOLD=3
export VAULT_PRIMARY_CHAIN=1             # your call — see "decisions" below
export VAULT_OWNERS=0x...,0x...,0x...,0x...,0x...

forge test -vvv                          # all green before any broadcast
cp deploy/chains.example.json deploy/chains.json   # sync with allchain.html
bash deploy/deploy-all.sh deploy/chains.json
```

### Phase 4 — anchor on Algorand (parsec side)
```bash
algokit compile py algorand/vault_quorum_mirror.py --out-dir algorand/artifacts
ALGOD_URL=https://mainnet-api.algonode.cloud \
VAULT_KEY=/mnt/usb/operator.tomb.key VAULT_THRESHOLD=3 VAULT_PRIMARY_CHAIN=1 \
python deploy/deploy_algorand.py         # route signing through parsec-wallet in prod
```

### Phase 5 — continuous verification
```bash
VAULT_ADDRESS=<create2_address> VAULT_KEY=/mnt/usb/operator.tomb.key \
  python -m bankon_vault.multichain deploy/chains.json
python deploy/deploy_algorand.py verify <app_id>
# wire both into a systemd timer on a watcher host + mindX startup check
```

### Phase 6 — recovery drill (do this once, for real, on testnet first)
1. Owners call `approve()` on the primary until `unlocked() == true`.
2. ≥3 shareholders convene **offline**, merge shares via `quorum.py::reconstitute`
   (auto-verifies the commitment).
3. Open the tomb with the restored key. Immediately `tomb setkey` to a fresh key.
4. Re-split, re-hash, bump salt to `v2`, redeploy anchors, re-verify. Old anchors stay
   on-chain as the permanent audit trail of the event.

---

## 6. Threats & failure modes, honestly

| Scenario | Outcome |
|---|---|
| Vault file stolen | Useless without key: LUKS/AES-XTS + argon2-hardened key password. |
| Key file stolen | Still needs its password; rotate via quorum anyway (`setkey` + v2 anchors). |
| < N shares leaked | Nothing recoverable; Shamir gives no partial information. |
| N shares collude | They get the key — that's the design (they *are* the quorum) — but reconstitution is loudly public (`unlocked=true`) and the commitment check stops key substitution. |
| One chain censored/dead | Ignore it; primary or any other anchor still works. |
| Primary chain compromised | Anchor-chain quorum + 7-day public timelock = contestable break-glass. |
| RPC endpoint lies | Cross-chain divergence check fails closed; use ≥2 RPC providers per chain for the watcher. |
| Host coerced while vault open | `tomb slam all` (rehearse it); everything unmounts, holders killed. |
| Swap leaks key pages | That's why Phase 0 says `swapoff -a`; Tomb refuses to run otherwise. |
| Quorum contract bug | Surface is ~50 lines, no external calls, no value held; still: testnet drill before mainnet, and the contract can't lose funds because it never holds any. |

**What this system does NOT provide:** cryptographic deniability (dm-crypt has none), protection
from a fully compromised host *while the vault is open*, or recovery if all shares AND all cold
backups (QR, steg, key file) are lost. Physical key hygiene remains the foundation.

---

## 7. Open decisions (defaults used here)

- **Primary chain** — examples use Ethereum mainnet (`1`) for maximal settlement assurance; Base or
  Arbitrum are defensible if you want cheap approvals. Set `VAULT_PRIMARY_CHAIN` before deploying;
  it is immutable afterwards.
- **N-of-M** — examples use 3-of-5. Match it to your actual counsellor structure.
- **fallbackDelay** — 7 days default. Longer = safer against chain compromise, slower break-glass.

---

## 8. License

EVM contracts and scripts: GPL-3.0-or-later. The future `bankon_vault` GUI forked from zuluCrypt
must inherit upstream's copyleft license (pin the exact upstream LICENSE first — GitHub metadata
reports it as non-SPDX). Tomb itself is GPL, by dyne.org. Credit: Denis "Jaromil" Roio & the Tomb
crew; Mhogo Mchungu (zuluCrypt); roddhjav (pass-tomb); mandeep (Mausoleum).
