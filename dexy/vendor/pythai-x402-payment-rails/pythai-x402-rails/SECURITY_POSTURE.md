# PYTHAI x402 — Defensive Posture (v2.0.0)

**Supersedes**: the Falcon-1024 cryptographic profile and the open HIGH-severity items in `LIMITATIONS.md`.
**Date**: 2026-07
**Standard**: CP-2048 (see `docs/CP-2048.md`)

This document is the authoritative statement of the x402 rails threat model and the controls that answer it. Where it conflicts with older text in `ARCHITECTURE.md` or `LIMITATIONS.md`, this document wins.

---

## 1. Posture summary

The v1 package was functional but carried six exposures: a single-family PQC root, an unshielded pre-auth attack surface, cross-chain non-atomicity, cross-chain signature replay, an all-or-nothing view of immutability (no way to stop a disclosed break), and validator-key blast radius. v2 closes all six with concrete artifacts, not prose.

| # | Threat | v1 state | v2 control | Artifact |
|---|--------|----------|-----------|----------|
| T1 | Quantum / cryptanalytic break of the signing root | Falcon-1024 only (1 family, unfinalized FIPS) | CP-2048 hybrid, multi-family roots; on-chain agility | `foundry/src/pqc/PQCRegistry.sol`, `CompositeVerifier.sol`, `docs/CP-2048.md` |
| T2 | Pre-auth DDoS via expensive CAIP-122 verification | No IP gate before signature work | Token-bucket + PoW shield *before* any crypto | `api/middleware/preauth_shield.py` |
| T3 | Stranded funds from non-atomic cross-chain transfer | Manual Senatus recovery | Saga with auto-compensation; only ambiguity escalates | `integrations/mindx/saga_coordinator.py` |
| T4 | Cross-chain / cross-context signature replay | chainId in sig only | Full EIP-712 domain binding (chain, contract, suite, nonce, purpose) | `foundry/src/security/DomainSeparator.sol` |
| T5 | Disclosed break with no way to halt (immutability trap) | No pause by design | Deprecation flag + autonomous loss-velocity tripwire; exits never blocked | `foundry/src/security/CircuitBreaker.sol` |
| T6 | Validator key compromise → bridge theft | HSM guidance only | T6 controls below (Tier-0 co-sign for param changes, domain-bound transfers, tripwire) | multiple |

---

## 2. T1 — Cryptographic root (CP-2048)

The single most important change. Roots of trust (genesis, treasury/Aerarium, DeadmansSwitch, the key-rotation authority) move from one lattice signature to **three simultaneous assumptions**: hash-based SLH-DSA + lattice ML-DSA-87 + classical Ed25519 (`CP2048-SIG-0`). To forge a root artifact an attacker must break a hash function *and* module-lattices *and* Curve25519 at once.

- Validators/governance use `CP2048-SIG-1` (ML-DSA-87 + Ed25519).
- Agents/identity use `CP2048-SIG-2` (ML-DSA-65 + Ed25519).
- The `PQCRegistry` **rejects any single-family Tier-0/1 suite at write time** — the Falcon-only mistake is structurally unrepeatable, even by governance error.
- Suites are referenced by ID; a broken one is retired via deprecation + sunset without redeploying immutable consumers.

Full spec and migration steps: `docs/CP-2048.md` §6. Why not "bigger Falcon": `docs/CP-2048-RATIONALE.md`.

## 3. T2 — Pre-auth shield

Signature verification is the most expensive per-request operation, so it must be the *last* gate, not the first. `preauth_shield.py` enforces, in increasing cost order: O(1) IP token bucket → structural header/size validation → proof-of-work challenge for pressured IPs → only then signature verification. A flood is absorbed before a single elliptic-curve or lattice op runs. Demonstrated: 12/30 flood requests admitted to the auth stage, remainder bounced cheaply.

## 4. T3 — Cross-chain atomicity via saga

Two-leg transfers are wrapped in a compensating saga. A transfer always reaches a terminal state — `DONE` or `REFUNDED` — without manual intervention. The only state that escalates to Senatus is genuine ambiguity (destination mint submitted but unconfirmed past window), where auto-unlocking the source would risk a double-mint. Everything else self-heals. Demonstrated: no-consensus scenario auto-refunds `locked → compensating → refunded`.

## 5. T4 — Replay resistance

Every signed transfer is bound to an EIP-712 domain over `(name, version, chainId, verifyingContract, suiteId)` and a struct over `(transferId, amount, token, srcChain, dstChain, recipient, nonce, expiry)`. A signature valid for one chain, contract, suite, or purpose cannot be replayed into another. Transfers also carry an `expiry`, bounding the replay window even within-context.

## 6. T5 — Immutability without the trap

"No admin keys, no upgrades" must not mean "no way to stop the bleeding." `CircuitBreaker` provides two key-free halts: a Senatus-supermajority **deprecation flag** for planned migration or disclosed breaks, and an **autonomous tripwire** that latches when settlement velocity exceeds a rolling ceiling (the signature of an active exploit). Both halt *intake only* — refunds and withdrawals of already-committed funds are **always** permitted. Users can never be trapped.

## 7. T6 — Validator blast-radius reduction

Key compromise remains the highest-consequence event; it is contained rather than merely warned about:
- **Parameter changes** (fees, rate limits, suite registration) require a Tier-0 SLH-DSA co-sign, so a compromised validator EVM key alone cannot alter protocol economics.
- **Transfers are domain-bound and expiring** (T4), so a leaked signature has narrow, time-boxed utility.
- **The tripwire** (T5) caps the loss rate from any single-session compromise before governance can respond.
- Operational: HSM/air-gap for 1R, geographic separation, quarterly CP-2048 key rotation with hash-based forward security.

---

## 8. Residual risks (honest accounting)

These are reduced, not eliminated:

- **Ambiguous saga states** still require Senatus review; they are rare but real.
- **Tripwire calibration** trades false-halts against loss ceiling; too tight harms availability, too loose raises max loss. Set per-deployment from observed volume.
- **SLH-DSA co-sign verification is off-chain** (commitment-attested on-chain); the co-signing ceremony's operational security is now a load-bearing assumption.
- **All CP-2048 primitives depend on vetted libraries** (liboqs/AWS-LC/CIRCL). A library-level flaw is inherited. Mandatory: independent audit before mainnet; no hand-rolled samplers.
- **Reference artifacts here are scaffolding**, not audited production code. As of v2.1.0 the five defensive contracts compile to deployable bytecode under solc 0.8.20 (with and without viaIR), and the test suites + deploy script type-check against forge-std (see `COMPILE_PROOF.md`). Not yet done: `forge test` execution (Foundry install blocked by egress allowlist), real PQC precompiles (`0x0a`/`0x0b`), real `ISenatus` root-quorum + SLH-DSA co-sign verification, and independent audit. Composite verification **fails closed** where precompiles are absent.

## 9. What was wired in v2.1.0

The controls are no longer standalone files — they are composed into a settlement path:
`foundry/src/core/HardenedSettlement.sol` calls `CircuitBreaker.guardIntake` on intake,
binds every transfer through `DomainSeparator`, and requires `CompositeVerifier` (which
resolves the suite from `PQCRegistry`) to pass before committing. `settleFrame` is the
0G Aristotle Frame-Transaction entry point; exits route through `processExit`, which is
never blocked by a halt. Deploy order and suite seeding: `foundry/script/DeployHardened.s.sol`.
Proving tests: `PQCRegistry.t.sol` (single-family-rejection guard + fuzz), `CircuitBreaker.t.sol`
(velocity latch + exits-always-allowed + fuzz), `HardenedSettlement.t.sol` (composite gate,
replay, expiry, nonce, halt propagation).

---

*x402 defensive posture v2.0.0 — hybrid roots, shielded intake, self-healing transfers, replay-bound signatures, key-free halts. Cypherpunk2048 standard.*
