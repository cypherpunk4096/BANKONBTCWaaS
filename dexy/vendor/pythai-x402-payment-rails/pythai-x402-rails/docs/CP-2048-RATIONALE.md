# RATIONALE — Why Falcon-1024 Is Not Enough

A focused argument for replacing the single-algorithm Falcon-1024 profile with CP-2048's hybrid, multi-family, agile profile.

## The three defects of a Falcon-1024-only root

**1. It standardizes on a non-final standard.**
FN-DSA (Falcon) is FIPS 206, which was still *in progress* as of April 2026. ML-DSA (FIPS 204) and SLH-DSA (FIPS 205) were finalized in August 2024. Anchoring an immutable, no-admin-key, mainnet-only system to a scheme whose parameters and encoding could still shift is a self-inflicted wound. CP-2048 roots use only finalized FIPS.

**2. Its danger lives in implementation, not the math.**
Falcon needs constant-time discrete Gaussian sampling. That is notoriously hard to implement without side channels, and a signing service is the single worst place to inherit a timing leak — the secret you leak is the one that authorizes value transfer. CP-2048's P4 forbids hand-rolled samplers and its default signing families (ML-DSA, SLH-DSA) do not require Gaussian sampling at all.

**3. One family is one point of failure.**
Falcon-1024 is module/NTRU-lattice. So is ML-DSA. A single sufficiently strong lattice result would threaten *both* at once. A system whose genesis, treasury, and kill-switch all rest on one assumption has no fallback the day that assumption weakens. CP-2048 Tier-0 roots require **two disjoint PQ families** (hash-based + lattice) plus a classical witness. To forge a root you must break a hash function *and* lattices *and* Curve25519 simultaneously.

## Why hybrid, not "bigger"

Increasing Falcon's parameter size does nothing about defects 1–3. The correct axis of improvement is not *strength of one primitive* but *independence of assumptions*:

- **Hybrid signature** — classical ⊕ PQC, both must verify. You inherit the max of the two securities, not the min.
- **Family diversity** — lattice ⊕ hash for roots. Cryptanalysis is family-specific; diversity is the hedge.
- **Agility** — an on-chain registry lets you retire a broken suite by deprecation + sunset, without redeploying immutable consumers. The registry is the one movable joint in an otherwise rigid structure, and it *structurally refuses* to register a single-family root suite — so the Falcon-only mistake cannot be re-made even by governance error.

## What we keep from the old profile

FN-DSA still has a legitimate niche: its signatures are compact (~1280 B at level 1024), which is attractive for bandwidth-constrained leaves. CP-2048 keeps it as an **optional Tier-L leaf** via a vetted constant-time library — never as a root, never as a sole signer. That preserves the one real advantage of Falcon while removing it from every position where its weaknesses matter.

## One-line summary

> Falcon-1024 is one unfinalized algorithm from one math family with a dangerous sampler. CP-2048 replaces it with finalized, multi-family, hybrid suites behind an agile on-chain registry that cannot regress to a single-family root.
