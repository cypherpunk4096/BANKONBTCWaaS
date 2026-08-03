# Compilation & Verification Record — v2.1.0

Honest, reproducible account of what was mechanically verified for the CP-2048
defensive stack, and what was not. Compiler used: **solc 0.8.20** (Emscripten build,
`0.8.20+commit.a1b79de6`), obtained via the npm `solc` package.

## Verified ✅

**1. All defensive contracts compile to deployable bytecode.**
Compiled together, `viaIR` disabled (config-independent) and enabled (project config):

| Contract | Result |
|----------|--------|
| `pqc/PQCRegistry.sol` | 0 errors |
| `pqc/CompositeVerifier.sol` | 0 errors |
| `security/DomainSeparator.sol` | 0 errors |
| `security/CircuitBreaker.sol` | 0 errors |
| `core/HardenedSettlement.sol` | 0 errors |

Multi-file compile, no viaIR: **0 errors, 6 deployable contracts, 14,777 bytes bytecode.**

A real `Stack too deep` error was found and fixed during this pass (the EIP-712 digest
functions were refactored into private sub-hashes and `settleFrame` was refactored to
take a `FrameParams` struct). The contracts now compile with **and** without `viaIR`,
so compilation does not depend on the optimizer pipeline.

**2. All test suites and the deploy script compile / type-check.**
Compiled against a forge-std interface stub:

| File | Result |
|------|--------|
| `test/PQCRegistry.t.sol` | parses, type-checks |
| `test/CircuitBreaker.t.sol` | parses, type-checks |
| `test/HardenedSettlement.t.sol` | parses, type-checks |
| `script/DeployHardened.s.sol` | parses, type-checks |

**3. All Python components execute and self-demonstrate.**

| File | Demonstration |
|------|---------------|
| `integrations/mindx/composite_sign.py` | family-diversity guard: rejects single-family Tier ≤1 |
| `api/middleware/preauth_shield.py` | flood absorbed: 12/30 admitted to auth stage, rest bounced cheaply |
| `integrations/mindx/saga_coordinator.py` | terminal in both paths: `done` and `refunded`, no stranded funds |

## NOT verified ❌ (honest limits)

- **`forge test` was not executed.** Foundry could not be installed: `foundry.paradigm.xyz`
  and `release-assets.githubusercontent.com` are outside this environment's egress
  allowlist. The test suites are therefore **verified-compilable, not verified-passing**.
  Run `forge test -vvv` in a Foundry environment to execute the assertions. The tests are
  written to standard forge-std and expect no special setup.
- **Precompiles are assumed, not present.** `CompositeVerifier` calls precompiles at
  `0x0a` (Ed25519) and `0x0b` (ML-DSA). On a chain without them, those calls return
  failure and composite verification fails closed (safe default), but real verification
  requires the 0G Aristotle EIP-8051/8052 profile or an equivalent verifier library.
- **`ISenatus` root-quorum / SLH-DSA co-sign verification is interface-only.** The tests
  use a permissive mock. Production requires a real SLH-DSA verification path.
- **No audit.** Compilation is not correctness. Independent audit remains mandatory
  before mainnet, per the CP-2048 security statement.

## Reproduce

```bash
# solc via npm (no foundry needed for compile-check)
mkdir solc-check && cd solc-check && npm init -y && npm install solc@0.8.20
# then run the standard-JSON compile over foundry/src/{pqc,security,core}/*.sol
# with settings.viaIR = true|false, optimizer on.

# to execute tests (requires Foundry):
forge test -vvv
```

*Record generated 2026-07 for x402 defensive posture v2.1.0.*
