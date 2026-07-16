# The PYTHAI Definitive Guide to Stablecoins and Pegged Assets
### Production-grade technical guide for PAI (USD measurement, Circle Arc) and FOUR BUCKS (4-CAD Maker-style CDP)
*cypherpunk2048 · Apache-2.0 · immutable · mainnet-only · Foundry · Popperian falsification*

> Condensed repo edition of the original research deliverable. Source code referenced here
> lives in `../src/`. **Not legal advice.**

---

## TL;DR

- **PAI is a GENIUS-Act-aware in-house measurement token, not a public payment stablecoin.**
  Under the GENIUS Act (signed July 18, 2025), issuing a USD-pegged *payment stablecoin* to the
  public without Permitted Payment Stablecoin Issuer (PPSI) status becomes unlawful three years
  after enactment. PAI stays outside that perimeter: a **1:1 USDC-collateralized,
  non-interest-bearing internal unit of account** — its reserve *is* USDC (already a compliant
  payment stablecoin), it pays no yield, and it is an organizational accounting/measurement
  unit rather than a settlement asset sold to U.S. persons. Canonical deploy: **Circle Arc**
  (USDC-as-gas L1; testnet chain ID 5042002, RPC `https://rpc.testnet.arc.network`, explorer
  `https://testnet.arcscan.app`; mainnet beta expected 2026).
- **FOUR BUCKS is a permissionless, over-collateralized MakerDAO-style CDP pegged to 4 CAD —
  legally the safest design, not the riskiest.** Exogenously collateralized (WETH/WBTC),
  redeemable only for $4-CAD-worth of collateral (never a fixed fiat amount from an issuer), so
  it is **not** a GENIUS "payment stablecoin" and **not** an "endogenously collateralized
  stablecoin" (the Terra-style design GENIUS §11 singled out for a Treasury study — the
  definition requires reliance on an asset "created or maintained by the same originator";
  WETH/WBTC are not). Oracles: **Pyth Network** pull feeds (CAD/USD + ETH/USD) with an in-house
  median/TWAP fallback; Liquidation 2.0 Dutch auctions. In Canada it is a CSA "value-referenced
  crypto asset" — keep it non-custodial, don't operate a Canadian trading platform for it,
  don't market it to retail as a stable store of value.
- **cypherpunk2048 to the letter:** no admin keys post-deploy, no proxies, immutable contracts,
  permissionless liquidations and circuit breakers. DAIO/BONAFIDE governance is confined to a
  timelocked, permissionless collateral-onboarding factory plus advisory attestation — it can
  add new isolated collateral vaults but never touch existing depositor funds, freeze balances,
  or change the 4-CAD math of a live vault. Library: **Solmate** (audited, gas-minimal,
  no proxy machinery) + Maker `dss` WAD/RAY/RAD math, Solidity 0.8.26, Foundry unit + fuzz +
  invariant suites.

---

## 1. GENIUS Act & CLARITY Act (and Canada)

**GENIUS Act** (S.394/S.1582, https://www.congress.gov/bill/119th-congress/senate-bill/394/text):

| Provision | Rule | Consequence |
|---|---|---|
| Reserves | ≥1:1 in cash, insured deposits, ≤90-day T-bills, overnight repos, govt MMFs | PAI's reserve is 100% USDC → inherits GENIUS-grade backing without holding T-bills directly |
| Interest ban | Issuers prohibited from paying interest to holders | Neither token pays yield. FOUR BUCKS charges a *stability fee to borrowers* — opposite direction |
| Not securities/commodities | Permitted payment stablecoins excluded | Only helps PPSIs; PYTHAI deliberately stays outside the "issued to the public" perimeter |
| §3 secondary-market prohibition | 3y post-enactment, DASPs may not offer non-PPSI payment stablecoins to U.S. persons; P2P exempt | Never list PAI on a U.S.-facing DASP as a payment stablecoin |
| §11 endogenous study | Treasury study (not ban) of coins relying on assets "created or maintained by the same originator" | FOUR BUCKS uses WETH/WBTC → outside the definition |
| Accounting | Non-PPSI stablecoins may not be treated as cash-equivalents | Book PAI as a *claim on segregated USDC*, not cash — the honest-measurement posture |
| Effective date | Earlier of 18 months post-enactment or 120 days after final regs | Rulemaking ongoing (FDIC NPRMs Dec 2025, 2026) |

Trackers: Federal Register GENIUS Implementation ANPRM
(https://www.federalregister.gov/documents/2025/09/19/2025-18226/genius-act-implementation),
FDIC rulemaking, Paul Hastings guide
(https://www.paulhastings.com/insights/crypto-policy-tracker/the-genius-act-a-comprehensive-guide-to-us-stablecoin-regulation).

**CLARITY Act** (H.R. 3633, https://www.congress.gov/bill/119th-congress/house-bill/3633):
House-passed 294-134 (July 17, 2025); **not yet law** (Senate stalled early 2026). Would route
spot "digital commodities" to the CFTC, keep investment-contract assets at the SEC, and add the
"mature blockchain system" decentralization test. WETH/WBTC would be CFTC-side digital
commodities; a future DAIO governance token could be an investment-contract asset at issuance.

**Canada:** CSA Staff Notice 21-333 classifies stablecoins as **value-referenced crypto assets
(VRCAs)** that may constitute securities and/or derivatives
(https://www.osc.ca/en/securities-law/instruments-rules-policies/2/21-333/csa-staff-notice-21-333-crypto-asset-trading-platforms-terms-and-conditions-trading-value).
The federal **Stablecoin Act (Bill C-15)**
(https://www.canada.ca/en/department-finance/programs/financial-sector-policy/canadas-stablecoin-framework.html)
requires 1:1 reserves, par redemption, and Bank of Canada registration — but only for
*fiat-backed* issuers. FOUR BUCKS (crypto-collateralized, non-custodial, no issuer
redemption-at-par) sits in the VRCA/securities-or-derivatives lane. Practical posture: stay
permissionless and non-custodial, no Canadian trading platform, FINTRAC MSB registration if
operating exchange/transfer as a business, CSA risk disclaimers if the word "stablecoin" is
ever used.

---

## 2. Taxonomy (with source repos)

| Model | Mechanism | Reference source | GENIUS status |
|---|---|---|---|
| Fiat-backed (custodial) | 1:1 off-chain reserves, issuer mint/redeem, blocklist | Tether `TetherToken.sol` — https://github.com/tethercoin/USDT/blob/main/TetherToken.sol · https://github.com/tetherto · USDT mainnet `0xdAC17F958D2ee523a2206206994597C13D831ec7`; Circle USDC | The regulated "payment stablecoin" — needs PPSI |
| Crypto-collateralized CDP | Lock volatile collateral, mint against it, liquidate if unsafe | **https://github.com/makerdao/dss** (now https://github.com/sky-ecosystem) · dss wiki · readable fork https://github.com/alexvansande/MuchClearerDAI | Exogenous → outside §11; not a payment stablecoin |
| Algorithmic / endogenous | Peg defended by sibling token minted by same originator | Terra/UST — collapse erased tens of billions in days | The §11 study target; **do not build** |
| Rebrand note | Maker → **Sky** (2024): USDS/SKY, Sky Savings Rate; USDS adds a freeze function | https://sky.money · https://makerdao.com/da/whitepaper/ | dss engine remains the reference architecture |

Repo verification: `github.com/dairef` does **not** resolve to a canonical Dai reference — the
real reference is `makerdao/dss`. The Tether links above are real.

**Library choice — Solmate over OpenZeppelin:** both externally audited; Solmate's `ERC20`,
`SafeTransferLib` are gas-minimal, dependency-light, and philosophically aligned with
cypherpunk2048 (no proxy/`Ownable` machinery inviting the admin-key pattern). Maker's audited
WAD/RAY/RAD integer math is ported directly for core accounting.

Standards & tooling: ERC-20 (https://eips.ethereum.org/EIPS/eip-20) · ERC-8004
(https://eips.ethereum.org/EIPS/eip-8004) · Foundry Book (https://book.getfoundry.sh/) ·
Pyth EVM integration
(https://docs.pyth.network/price-feeds/core/use-real-time-data/pull-integration/evm) ·
Pyth EVM addresses (https://docs.pyth.network/price-feeds/core/contract-addresses/evm) ·
x402 (https://x402.org/) · x402 Algorand / GoPlausible (https://x402.goplausible.xyz/) ·
Circle Arc (https://www.arc.io/ · https://docs.arc.network).

---

## 3. PAI design (source: `src/pai.sol`, `src/pairep.sol`)

18-decimal ERC-20 fully backed 1:1 by 6-decimal USDC; parity factor `1e12`, so
`1.000000000000000000 PAI : 1.000000 USDC` exactly. Mint locks USDC; redeem requires an exact
multiple of `1e12` (no reserve-rounding dust) and releases it. No admin keys, no pause, no
blocklist. The canonical Arc deployment emits a `RateBeacon` (par `1e18`); **allchain
representations** (`PAIrep`) on every chain in the AgenticPlace registry
(https://agenticplace.pythai.net/chainmarketcap; machine-readable
https://agenticplace.pythai.net/api/export?table=agents&format=json) inherit the rate from the
relayed beacon (1h staleness ceiling, par-only invariant) rather than re-deriving the peg.

Cross-check chain IDs against https://chainlist.org /
https://github.com/ethereum-lists/chains before enabling any representation.

---

## 4. FOUR BUCKS design (source: `src/vat.sol`, `src/four_bucks_oracle.sol`, `src/abacus.sol`, `src/breaker.sol`, `src/collateral_factory.sol`)

Denomination: **1 FOUR BUCKS = value of 4 CAD.** Architecture mirrors Maker dss
(Vat/Spot/Join/Dog/Clip/Abacus/Vow/Jug/OSM) modernized to Solidity 0.8.26.

**4-CAD reference math** (RAY, 1e27): `ref = (collateralUSD / cadUSD) / 4` — the number of
4-CAD units one unit of collateral is worth. Pyth CAD/USD and collateral/USD prices are
validated for staleness (`getPriceNoOlderThan`) and confidence (`conf/price ≤ maxConfBps`),
with an in-house 16-slot median/TWAP ring buffer as fallback (BANKON aggregation pattern).

**Liquidation 2.0**: keeper `bark` → `kick` starts a Dutch auction from `top = ref × buf`,
decaying via the StairstepExponentialDecrease Abacus; partial `take` allowed. (Reference:
https://docs.makerdao.com/smart-contract-modules/dog-and-clipper-detailed-documentation and
https://github.com/makerdao/dss/blob/master/src/clip.sol — complete the Dog/Clip/Vow/Jug
bodies from dss before audit.)

**Deterministic circuit breaker** replaces admin pause: trips automatically when the fallback
median and the Pyth read diverge beyond a hard bps threshold; untrips when they reconverge. No
key can override it.

**Governance without admin keys**: the only governance action is adding a **new isolated
collateral type** through the 7-day timelocked, permissionless `CollateralFactory` — anyone
proposes, anyone executes after the delay; it can never touch existing depositors' `ink`/`art`,
freeze balances, or alter a live vault's 4-CAD math. DAIO/BONAFIDE integrates as
**advisory/attestation only** (verification gap: BONAFIDE contract names/addresses were not
confirmable on live pages at research time — pin them when the DAIO deployment is public).

**Pyth deployment pins**: Ethereum mainnet current `0x4305FB66699C3B2702D4d05CF36551390A4c69C6`;
**use the upgraded address `0x14b9932cc9AC8Ee03301665a8644A753f46D8552` for new integrations**
(Pyth Core upgrade 2026-07-31; API key required). ETH/USD feed id
`0xff61491a931112ddf1bd8147cd1b641375f79f5825126d665480874634fd0ace`; BTC/USD
`0xe62df6c8b4a85fe1a67db44dc12de5db330f7ac66b72dc658afedf0f4a415b43`; CAD/USD id from
https://docs.pyth.network/price-feeds/core/price-feeds. Fetch signed updates from Hermes
(https://docs.pyth.network/price-feeds/core/fetch-price-updates) and pass into
`FourBucksOracle.poke` in the same tx as any mint.

---

## 5. Deployment matrix

| Target | Chain ID | Notes |
|---|---|---|
| Circle Arc (PAI canonical) | 5042002 (testnet) | USDC = native gas (6 dec); pin mainnet addresses at Circle's 2026 beta |
| Ethereum mainnet (FOUR BUCKS) | 1 | Pyth upgraded address; one conservative WETH-A ilk at launch |
| allchain PAI reps | per AgenticPlace registry | reps read canonical Arc rate; verify each chainId |

No `Ownable`, no `initialize`, no proxy — constructors fully configure immutable contracts;
nothing to renounce because there was never an owner (LayerZero OApp is the one exception:
peers set in the deploy batch, then owner renounced). Verify source on every explorer
immediately post-deploy.

---

## 6. x402 Algorand payment gating (accounting/oracle API)

Metered access via HTTP 402 (RFC 9110) over x402, settled on **Algorand mainnet** via
GoPlausible's x402-avm (https://x402.goplausible.xyz/; verify package names against
https://github.com/GoPlausible and the parsec / parsec-wallet references at deploy time).
Mainnet CAIP-2: `algorand:wGHE2Pwdvd7S12BL5FaOP20EGYesN73ktiC1qzkkit8=`. See
`../integrations/x402_server.py` for the FastAPI (Python ≥3.12) resource server gating
`GET /accounting/pai/reserve` and `POST /oracle/post`. Consistent with the existing PYTHAI
x402 rails (Ethereum mainnet, Algorand mainnet, 0G Aristotle).

---

## 7. In-house accounting & accountability (any organization)

1. **Choose the unit of account**: PAI for USD measurement (backing provable on demand via
   `backingOk()`); FOUR BUCKS vaults for a 4-CAD denomination.
2. **Measure, don't speculate**: neither pays yield (GENIUS-clean, Canada-clean).
3. **Accountability by construction**: every mint/redeem is a signed, timestamped on-chain
   ledger entry an auditor can independently reconstruct. Book PAI as a claim on segregated
   USDC (not cash-equivalent); book FOUR BUCKS positions as collateralized borrowings marked
   to the Pyth 4-CAD reference.
4. **Gate & monetize** the accounting API via x402 — pay-per-query, no accounts, agent-native.
5. **Govern without keys**: 7-day public timelock anyone can execute; depositor funds
   mathematically untouchable.

---

## 8. Staged rollout (thresholds)

- **Stage 0 (now)**: Arc testnet PAI + Ethereum-mainnet-fork FOUR BUCKS against live Pyth.
  Advance only on zero invariant violations across ≥512 invariant runs / ≥10k fuzz runs plus a
  clean external review of the oracle staleness/confidence path.
- **Stage 1 (PAI production)**: Arc mainnet at Circle's beta. Keep PAI strictly
  internal/measurement; never cross the GENIUS §3 line.
- **Stage 2 (FOUR BUCKS, Ethereum mainnet)**: one conservative WETH-A ilk, breaker armed,
  Pyth upgraded address + API key provisioned. Raise ceilings only after 30 days of clean
  liquidations; add WBTC only through the timelocked factory.
- **Stage 3 (allchain + ecosystem)**: PAI reps per registry, mindX routes wired once live
  paths confirmed (verification gap at research time), x402 gating on.
- **Standing benchmarks**: Treasury §11 study; CLARITY enactment; Canada C-15 final regs;
  GENIUS final rules — each re-opens the compliance analysis.

## Caveats

Not legal advice; GENIUS rulemaking mid-flight, CLARITY not law, C-15 regs pending. PYTHAI
internals (mindX route paths, BANKON addresses, BONAFIDE suite) carried verification gaps at
research time — pin at integration, never from this doc. Oracle risk dominates FOUR BUCKS;
set conservative `maxStale`/`maxConfBps` and fork-test against live Pyth. Source is
reference-grade — complete abbreviated dss modules and commission an audit + bounty before
mainnet value. Arc is pre-mainnet. Terra is the standing warning FOUR BUCKS is built to be
the opposite of.
