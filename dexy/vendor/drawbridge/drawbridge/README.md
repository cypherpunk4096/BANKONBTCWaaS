# Drawbridge — PYTHAI / BANKON pegged-asset & multichain gateway

Production Solidity for a USD-measurement token (**PAI**), a permissionless MakerDAO-style
CAD-denominated CDP (**FOUR BUCKS**), and a same-address multichain PAI↔USDC gateway guarded
by a golden-ratio tollkeeper (**Troll**), a royalty module (**RoyalT**), and a two-phase
**Drawbridge** that closes completely on confirmation.

Built to the **cypherpunk2048** standard: Apache-2.0, **no admin keys post-deploy, no
upgradeable proxies, immutable contracts, mainnet-only**, Foundry for everything, reproducible
bytecode, Popperian falsification testing.

> Author: Gregory L. (Professor Codephreak). Regulatory framing: US **GENIUS Act** (payment
> stablecoin law) and **CLARITY Act** (market-structure, not yet law), plus Canadian **VRCA /
> Bill C-15** context. See `docs/01_stablecoin_guide.md`. **Not legal advice** — engage counsel
> before any mainnet value is at risk, and commission an external audit + bug bounty first.

---

## What's here

| Module | File | Salt | Role |
|---|---|---|---|
| PAI (canonical) | `src/pai.sol` | `bankon.eth/pai/v1`* | USDC-backed USD measurement token on Circle Arc (18:6 exact parity) |
| PAIm | `src/pai_multichain.sol` | `bankon.eth/pai/v1` | Multichain PAI; minters = gate + drawbridge, immutable |
| PAIrep | `src/pairep.sol` | — | Non-Arc representation; inherits canonical rate beacon |
| **Troll** | `src/troll.sol` | `pythai.net/tollkeeper/v1` | Guardian & tollkeeper: golden toll, moat, ACK funding |
| **RoyalT** | `src/royalt.sol` | `bankon.eth/royalt/v1` | Golden royalty → bankon.eth; ERC-2981 view |
| **Drawbridge v2** | `src/drawbridge.sol` | `bankon.eth/drawbridge/v2` | LZ V2 burn-and-mint + two-phase complete closure |
| Spring | `src/spring.sol` | `bankon.eth/spring/v1` | Plain capped-bps USDC well (alt to Troll) |
| TollDial | `src/toll_dial.sol` | `pythai.net/toll_dial/v1` | Timelocked permissionless fee dial |
| FourBucksVat | `src/vat.sol` | `pythai.net/four_bucks_vat/v1` | CDP core (dss-style), FOUR BUCKS @ 4 CAD |
| FourBucksOracle | `src/four_bucks_oracle.sol` | `pythai.net/four_bucks_oracle/v1` | Pyth + in-house fallback, 4-CAD math |
| Abacus | `src/abacus.sol` | `pythai.net/abacus/v1` | Liquidation 2.0 Dutch-auction price curve |
| DeterministicBreaker | `src/breaker.sol` | `pythai.net/breaker/v1` | Permissionless circuit breaker (no admin pause) |
| CollateralFactory | `src/collateral_factory.sol` | `pythai.net/collateral_factory/v1` | Timelocked permissionless collateral onboarding |

*Canonical PAI on Arc has different initcode from PAIm (distinct constructor) → distinct address
family. See `SALT_REGISTRY.md`.

---

## Quickstart

```bash
make install          # forge install: forge-std, solmate, LayerZero devtools, pyth-sdk
make build
make test             # unit + fuzz
make fuzz             # 10k-run fuzz
make invariant        # invariant/falsification suites
make salts            # print canonical salts + (with cast) predicted addresses
```

Requires [Foundry](https://book.getfoundry.sh/). Copy `.env.example` → `.env` and fill RPCs and
the **bankon.eth resolution** (resolve twice against your subname registrar before pinning —
it is baked into `RoyalT.CROWN` as a constant and is eternal on every chain).

---

## The golden toll (exact, 18 decimals)

The Troll charges the chain's contract-call fee with the golden ratio applied **one decimal
position to the right**, to the 18th decimal:

```
toll = callFee × 1.161803398874989485      (1 + φ/10)
0.001000000000000000 → 0.001161803398874989
```

The surcharge (the φ/10 part) splits φ-on-φ: **61.803…% → Moat** (unwithdrawable
overcollateralization, sole outflow = ACK fees) and **38.196…% → RoyalT** (→ bankon.eth). The
toll rides on native gas; the USDC redemption leg is never skimmed (1 PAI → 1.000000 USDC).

---

## Complete closure

A crossing is `OPEN → CONFIRMED → CLOSED`. The destination mints, then fires a CONFIRM (ACK)
message; receiving it flips the gate to CONFIRMED and `_close()` **deletes** the record in the
same tx (settling `inFlight`). Failsafe: after `RECLAIM_DELAY` (30d) with no ACK, anyone can
`slam()` for a refund — race-free because destinations **bounce** transits older than the same
delay, so mint-and-refund can never both occur for one id. ACK return messages are financed from
the Troll's moat: the golden surcharge literally funds the guarantee the bridge closes.

---

## Deploy (same address, every chain)

```bash
# 1. Pin RoyalT.CROWN = bankon.eth resolution (resolve twice), then:
make build
# 2. Per chain from the allchain registry export:
forge script script/DeployDrawbridgeV2.s.sol --rpc-url ethereum --broadcast --verify
# 3. Set LZ peer matrix (each peer == this same address), renounce owner to address(0),
#    assert address equality on every chain.
```

Uses the canonical CREATE2 proxy `0x4e59…956C`. Same salt + same zero-arg/pinned initcode +
same deployer ⇒ identical address on Ethereum and every EVM chain. Reproducible-bytecode
settings (`bytecode_hash="none"`, `cbor_metadata=false`) are already in `foundry.toml`.

---

## Ecosystem integration

- **allchain mapping** — `agenticplace.pythai.net/allchain.html` (machine-readable via
  `agenticplace.pythai.net/api/export`) drives the per-chain USDC/endpoint matrix and the
  deploy loop. Verify every chainId against chainlist.org before enabling.
- **mindX API** (`mindx.pythai.net`) — point accounting routes at the single gateway address:
  `GET /accounting/troll/{chainid}` → `moat`, `reserve`, `backingOk`. mindX publishes moat-depth
  and solvency reports to **rage.pythai.net**.
- **DAIO / BONAFIDE** (deploying now) — no privileged hook exists by design; DAIO's role is
  attestation. `fortify()`/registry hashes are the on-chain material for solvency attestations.
- **x402 (parsec / parsec-wallet, Algorand mainnet)** — gate the moat/solvency dashboard API
  behind x402 pay-per-query rails (see `docs/01_stablecoin_guide.md`).

---

## Docs

1. `docs/01_stablecoin_guide.md` — GENIUS/CLARITY + Canada analysis, PAI & FOUR BUCKS design, taxonomy, full base source
2. `docs/02_pai_multichain_spring_drawbridge.md` — Spring/Drawbridge, toll → bankon.eth
3. `docs/03_troll_golden_gateway.md` — same-address golden-toll gateway
4. `docs/04_create2_salt_conventions.md` — salt theory, naming convention, reproducibility
5. `docs/05_drawbridge_v2_troll_royalt.md` — Troll + RoyalT modules + complete closure

---

## Status & caveats

Reference-grade, **not audit-complete**. Several modules are abbreviated ports of audited
[`makerdao/dss`](https://github.com/makerdao/dss) (grab/heal/suck/fold, Dog/Clipper bodies) and
the invariant handlers are scaffolds with explicit `TODO(audit)` targets. External trust
dependency: LayerZero endpoint/executor (mitigated by peer pinning + renounce, monitored by the
global-backing invariant). Circle Arc is pre-mainnet — re-verify Arc USDC/Pyth addresses at
launch. Pin the Pyth **upgraded** EVM address (Core upgrade 2026-07-31). Resolve bankon.eth
twice before freezing `CROWN`. Commission an audit + Immunefi-style bounty before mainnet value.

Apache-2.0 © 2026 Professor Codephreak — PYTHAI / DELTAVERSE / BANKON.
