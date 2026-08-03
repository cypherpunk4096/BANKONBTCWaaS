# CLEAN-ROOM sBTC BRIDGE REIMPLEMENTATION — DEVELOPER ROADMAP (Claude Code Handoff Brief)

## TL;DR
- Build a legally non-derivative ("clean-room") reimplementation of the Stacks sBTC bridge by documenting sBTC's PUBLIC specs (SIP-028 peg, SIP-021/025 WSTS, Stacks docs, Emily API, Clarity contracts, and the `sbtc` JS library's observable interface) into a Functional Spec, then having a SEPARATE implementation team build from that spec only — never reading `stacks-sbtc/sbtc-bridge` (TypeScript/Next.js) or `stacks-sbtc/sbtc` (Rust) source.
- "openBDK" is almost certainly the open-source Bitcoin Development Kit (`bitcoindevkit/bdk`, Rust; with `bdk-python`/`bdk-wasm` bindings) — there is NO GitHub org literally named "openBDK"; this is FLAGGED as an assumption. Use BDK for all Bitcoin-side wallet/UTXO/PSBT/Taproot work (peg wallet, deposit P2TR, sweep, peg-out).
- Expose the bridge as a paid, agent-accessible service: x402 (HTTP 402) on Algorand as the payment rail (GoPlausible facilitator), ERC-8004 registries for agent identity/reputation/validation, AgenticPlace for marketplace listing, mindX for orchestration, BANKON Vault for multichain custody/revenue, and BONAFIDE DAIO for governance. EVM-side contracts tested with Foundry and deployed to mainnet.

## Key Findings

### sBTC protocol (public spec surface — what to reimplement behaviorally)
- **1:1 peg / status**: sBTC is a SIP-010 fungible token on Stacks; 1 sBTC = 1 BTC. Backed by a single peg-wallet UTXO on Bitcoin in a Taproot multisig controlled by the sBTC signer set. sBTC deposits went live on Stacks mainnet **December 17, 2024** (Phase 1, deposits only) with an initial 1,000 BTC cap; withdrawals (Phase 2) targeted for March 2025 (per stacks.co "The sBTC Mainnet Release is Live with Bitcoin Deposits").
- **Signer set**: sBTC launched with **14 elected signers** (originally 15; Figment withdrew). Per Stacks.org "SIP-028 Update": "The network will move forward with 14 elected Signers, still requiring the originally intended 70% threshold (or 10/14 Signers)." (Hiro describes the intended full set as 70% ≈ 11/15.) Signers can rotate keys.
- **Deposit (peg-in)**: user builds a P2TR deposit address committing to two tapscripts — a **deposit script** (encodes destination Stacks address + max signer fee) and a **reclaim script** (CSV locktime + reclaim pubkey + OP_CHECKSIG; lets depositor reclaim if signers don't process). User sends BTC there, then notifies signers via Emily API. Signers sweep the deposit UTXO into the consolidated peg UTXO and mint sBTC to the Stacks address. Timing: per Hiro's SIP-028 breakdown, "sBTC deposits require 3 Bitcoin blocks (~30 minutes)." Minting does NOT wait for Bitcoin finality because (stacks-network/docs) "movements from the Bitcoin layer to the Stacks layer don't need to wait for finality to mint because the Stacks layer will just reorganize itself" if Bitcoin reorgs.
- **Withdrawal (peg-out)**: sBTC holder calls `withdraw-request` in the `.sbtc` contract; this transfers sBTC to the contract and mints a non-transferable locked-sBTC placeholder. Signers create a Bitcoin tx returning BTC to the designated address, then call `complete-withdrawal-accept` (or `-reject`) to settle/burn. Timing: per stacks-network/docs (deposit-withdrawal-times.md) a withdrawal "can only be processed 6 Bitcoin blocks (the finality criteria the Signers are comfortable with) after the withdrawal was made on the Stacks blockchain" (~1 hour).
- **Clarity contracts** (public interfaces to re-derive, not copy): `sbtc-token.clar` (SIP-010 fns transfer/get-name/get-symbol/get-decimals/get-balance/get-balance-available/get-balance-locked; plus protocol mint/burn/lock — sBTC is an "implicit" SIP-010, implementing the fns directly rather than via impl-trait), `sbtc-deposit.clar` (`complete-deposit-wrapper`, `complete-deposits-wrapper`; constants txid-length=32, dust-limit=546 sats), `sbtc-registry.clar` (central state: withdrawal-requests, withdrawal-status, completed-deposits, aggregate-pubkeys, multi-sig-address, protocol-contracts; fns `create-withdrawal-request`, `complete-withdrawal-accept/reject`, `complete-deposit`, `rotate-keys`), `sbtc-withdrawal.clar`.
- **Emily API**: supervisory REST API tracking deposits/withdrawals with status lifecycle PENDING → ACCEPTED → CONFIRMED (or FAILED). Named after Emily Warren Roebling (Brooklyn Bridge). Liaison between users and signers; also system health.
- **Signer binary**: Rust binary observing both Bitcoin and Stacks (event-observer pattern; stacks-node registers event observers at signer endpoints, e.g. `[[events_observer]] endpoint = "127.0.0.1:30000"` with keys `["stackerdb","block_proposal","burn_blocks"]`). A coordinator constructs the peg UTXO; the set consolidates deposit/withdrawal batches into one UTXO (single-UTXO invariant).
- **WSTS/FROST**: sBTC uses WSTS (Weighted Schnorr Threshold Signatures), a FROST variant producing a single aggregate Schnorr signature verifiable like any Schnorr sig; weighted so the threshold is a function of key shares. Defined in SIP-021; SIP-025 was an interim concatenated-multisig step. Reference crate `stacks-sbtc/wsts` (do NOT copy — reimplement from FROST literature/RFC 9591 + SIP text, or use an independent FROST library).
- **`sbtc` JS library observable interface** (behavioral spec source, not to be copied): `buildSbtcDepositAddress`, `buildSbtcDepositTx`, `sbtcDepositHelper`; API clients `SbtcApiClientMainnet/Testnet/Devenv` with methods `fetchSignersPublicKey`, `fetchSignersAddress`, `fetchFeeRate`, `fetchUtxos`, `fetchTxHex`, `fetchBalance`, `broadcastTx`, `notifySbtc`, `fetchDeposit`, `fetchSbtcBalance`. Defaults: maxSignerFee 80,000 sats, reclaimLockTime 950.

### sbtc-bridge repo (public, describe behavior only)
- `stacks-sbtc/sbtc-bridge` = "The sBTC web bridge," ~98.8% TypeScript, a Next.js app (next.config.js, tailwind, components.json, yarn). Structure: `docs/`, `public/`, `src/`. Build-time feature flags (e.g., `reskin`). Docker + GitHub Actions publishing to AWS ECR; health endpoint embeds Git commit. It is a UI/orchestration layer that depends on the Emily API, the signers, and the `sbtc` JS lib — NOT the consensus-critical component. Document its flows (deposit tab, withdraw tab, settings, status polling) from README/observable behavior; do not copy source.

### openBDK (ASSUMPTION — flagged)
- No GitHub org literally named "openBDK" exists (github.com/OpenBD is an unrelated Java CFML engine). Most likely meaning: the open-source **Bitcoin Development Kit** (`bitcoindevkit/bdk`), a Rust descriptor/miniscript-based wallet library handling key management, coin selection, transaction construction, Taproot, and chain sync via Electrum/Esplora/Core RPC/Kyoto; with `bdk-python` and `bdk-wasm` bindings. Confirmed by BDK team/OpenSats: BDK "reached its 1.0 release in December 2024 … and has since shipped 2.0 and beyond on an 8-week release cadence" (bdk_wallet 3.0.0 release candidates shipped Q1 2026). Use it for all Bitcoin-side plumbing.

### Payments / agent rails
- **x402**: an HTTP-402 payment protocol authored/open-sourced by Coinbase and **contributed to the x402 Foundation under the Linux Foundation on April 2, 2026** (now neutrally governed, not solely Coinbase). Flow: client requests → server returns 402 + payment requirements → client signs a stablecoin transfer and retries with a payment header → facilitator verifies/settles → 200 + resource. Chain-agnostic (CAIP-2). **Algorand support is live** (Algorand Foundation + GoPlausible), using the "exact" scheme, ALGO + ASA support, atomic transaction groups for fee abstraction/delegation, sub-5s finality. Facilitator at facilitator.goplausible.xyz; spec `scheme_exact_algo.md` in coinbase/x402. Resource server MUST opt-in to the ASA it charges in. Adoption (Coinbase via Eco.com, late April 2026, directional/marketing): "69,000 active agents, 165 million transactions, and approximately $50 million in cumulative volume," ≈ $0.30 average per call.
- **ERC-8004** ("Trustless Agents"): three per-chain singleton registries — Identity (ERC-721 + URIStorage; tokenURI → agent registration JSON), Reputation (feedback attestations), Validation (validator contracts post results). Draft authored by contributors from MetaMask, Ethereum Foundation, Google, Coinbase; CC0. Deployed Identity/Reputation contract addresses begin `0x8004…`.
- **AgenticPlace** (agenticplace.pythai.net): ERC-8004 agent registry/marketplace indexing agents across many EVM chains, with Algorand-native NFT minting. Its chain map (/allchain.html, a.k.a. /chainmarketcap "ALLCHAIN from A-Z") is EVM-focused — top indexed chains by live agent count include BNB Smart Chain, Base, Ethereum, Monad, MegaETH, Celo, Gnosis, Arbitrum, OP, Polygon, Avalanche, Linea, Scroll, Mantle, Taiko, SKALE, Abstract, Injective, Metis, X Layer (plus testnets). Stacks/Bitcoin are not listed there; Algorand is the minting/verification chain.
- **mindX** (mindx.pythai.net): autonomous BDI cognitive system ("augmentic intelligence orchestration") with an API; publishes to rage.pythai.net (RAGE memory/knowledge engine). Soul-Mind-Hands framework (AGInt P-O-D-A reasoning; BDI agent swarms with 21+ tools).
- **BANKON** (bankon.pythai.net): BANKON Vault multichain system; BANKON PYTHAI (knowledge-asset value token) and BANKON PAI (inference pricing) in PYTHAI tokenomics.
- **BONAFIDE DAIO**: dual-chain DAIO governance ("Code is Law" constitution, automated treasury, supermajority for constitutional changes), deploying now; roadmap adds governance hooks.

### Clean-room legal method
- Two-team Chinese-wall: a **Spec Team** (reads PUBLIC sBTC docs/specs/behavior, NOT proprietary/restrictively-licensed source) writes a Functional Spec; a **lawyer** reviews it to ensure no copyrighted expression; an **Implementation Team** builds ONLY from the reviewed spec, never having read the target source. Verification: spec team confirms spec accuracy only; bug fixes are done by the impl team (spec team must not suggest code). Precedents: Sega v. Accolade, Sony v. Connectix, Oracle v. Google — functional requirements/APIs are broadly not protectable; specific code expression is.
- IMPORTANT: `stacks-sbtc/sbtc`, the Clarity contracts, the `wsts` crate, and the `sbtc` JS lib are open source but under licenses you MUST check. Clean-room is chosen here to produce a demonstrably independent, non-derivative codebase regardless of license, enabling relicensing/commercialization in the PYTHAI ecosystem. The reclaim/deposit script formats, contract function signatures, and wire protocols are functional interfaces that MUST match for interoperability — matching an interface is permitted; copying its implementation is not. Clean-room defends copyright/trade-secret, NOT patents.

## Details — ROADMAP

Repo layout proposal (monorepo, e.g. `pai-sbtc-bridge`):
```
/spec/                  # Functional Spec (Spec Team output; canonical)
  01-glossary.md
  02-deposit-flow.md
  03-withdrawal-flow.md
  04-peg-wallet-utxo.md
  05-signer-wsts.md
  06-emily-api.openapi.yaml
  07-clarity-interfaces.md
  08-bitcoin-tx-formats.md   # P2TR deposit, reclaim/deposit tapscripts, sweep, peg-out
  09-bridge-ui-flows.md
  10-legal-cleanroom-log.md  # provenance trail: who read what, when; lawyer sign-offs
/contracts-clarity/     # reimplemented Clarity contracts (impl team)
/contracts-evm/         # Foundry: ERC-8004 integration, x402 receipts, gov hooks
/bitcoin-core-rs/       # Rust: BDK-based peg wallet, deposit/sweep/peg-out builders
/wsts-rs/               # independent threshold-sig module (FROST-based)
/signer/                # Rust signer binary (chain observers, coordinator, batching)
/emily-api/             # REST supervisory API (Rust axum or TS) + OpenAPI
/bridge-web/            # front-end (framework of choice; NOT copied from Next.js repo)
/x402-gateway/          # HTTP 402 middleware + Algorand facilitator integration
/agent-integration/     # ERC-8004 registration, mindX API client, AgenticPlace listing
/deploy/                # IaC, docker, mainnet deployment scripts
```

### PHASE 0 — Clean-room setup & guardrails (Week 0–1)
- Stand up two GitHub teams with enforced separation. Spec Team access: public Stacks docs (docs.stacks.co), SIPs (stacksgov/sips: SIP-010, SIP-021, SIP-025, SIP-028), Emily API docs, `sbtc` npm README, observable testnet/devnet behavior, Bitcoin BIPs 340/341/342. Impl Team access: ONLY `/spec/`.
- PROHIBITED for Impl Team: reading `stacks-sbtc/sbtc-bridge`, `stacks-sbtc/sbtc`, `stacks-sbtc/wsts` source, or the `sbtc` JS lib source. Allowed: their public docs/READMEs and behavioral observation.
- Create `/spec/10-legal-cleanroom-log.md`: record every source consulted (timestamp, person) and a lawyer review checkpoint before Impl begins each module. Add CONTRIBUTING rule + CODEOWNERS blocking cross-team commits.
- Deliverable: signed clean-room policy; empty spec skeleton; CI that fails if Impl Team members review spec-source PRs.

### PHASE 1 — Spec extraction (Week 1–4) [Spec Team]
Documents to read and characterize:
- SIP-028 (sBTC peg): deposit/withdrawal state machines, security model, signer duties, 14-signer/70% threshold.
- SIP-021 (WSTS) + SIP-025 (interim schema): threshold model, DKG, coordinator/BFT, weights.
- Stacks docs: Emily API (status lifecycle, endpoints, user groups), Peg Wallet UTXO (single-UTXO model, batching, key rotation), Clarity contract pages (deposit/registry/withdrawal/token), Signer Process Walkthrough, deposit-vs-withdrawal times, run-a-signer (event observer config).
- `sbtc` JS lib README (behavioral interface: builders, client methods, defaults).
- Bitcoin: BIP-340 Schnorr, BIP-341 Taproot, BIP-342 Tapscript; P2TR key-path vs script-path, MAST, control blocks.
Behaviors to characterize (black-box):
- Exact bytes/layout of the deposit tapscript (Stacks addr + max fee) and reclaim tapscript (CSV locktime + reclaim pubkey + OP_CHECKSIG). Derive the P2TR output from internal key + script tree; verify against a testnet deposit you construct independently.
- Emily request/response schemas for create-deposit, get-deposit(s), withdrawal status query, health → produce `/spec/06-emily-api.openapi.yaml`.
- Peg-wallet sweep tx structure (inputs: prev peg UTXO + swept deposits; output: new single peg UTXO; fee accounting). Peg-out tx (spend peg UTXO → user address + change back to peg UTXO).
- Clarity function signatures, error constants, data maps — as INTERFACES to match (`/spec/07-clarity-interfaces.md`).
Interfaces to define:
- Rust traits: `PegWallet`, `DepositBuilder`, `SweepBuilder`, `WithdrawalFulfiller`, `ChainObserver` (Bitcoin + Stacks), `ThresholdSigner` (DKG + sign), `EmilyClient`.
- OpenAPI for Emily; JSON schemas for signer coordination messages (reimplement wire format from SIP-021, not from wsts source).
Deliverable: complete `/spec/` reviewed & signed off by counsel.

### PHASE 2 — Architecture design (Week 4–6) [Impl Team, from spec only]
- Bitcoin core in Rust on **BDK** (bitcoindevkit/bdk) for descriptors, PSBT, Taproot, coin selection, chain sync (Esplora/Electrum/Core RPC). Threshold sigs in an independent FROST implementation (`/wsts-rs`) built from FROST/RFC 9591 + SIP-021 (NOT the wsts crate).
- Signer binary: two chain observers → mempool/block ingestion → deposit/withdrawal detector → coordinator (leader election, BFT round) → WSTS DKG + signing → tx broadcaster. Enforce the single-UTXO batching invariant.
- Emily API: axum (Rust) or Fastify (TS) backed by Postgres; status lifecycle machine; idempotency keys on create endpoints; webhook/poll for status.
- Clarity contracts: reimplement token/deposit/registry/withdrawal to documented interfaces; deploy plan via Clarinet.
- EVM contracts (Foundry): thin — an ERC-8004 agent-registration helper, an x402 payment-receipt/attestation contract, and DAIO governance hooks (proposal/param gating for maxSignerFee bounds, signer set, pause). Bitcoin/Stacks/Algorand are NOT EVM — keep EVM scope to agent identity + payment receipts + governance signaling.
- Draw trust boundaries: consensus-critical (signer, WSTS, peg wallet, Clarity) vs supervisory (Emily) vs presentation (web) vs monetization (x402 gateway) vs agent/governance (ERC-8004, mindX, DAIO).
Deliverable: architecture doc + module interface stubs compiling with `todo!()`.

### PHASE 3 — Implementation milestones [Impl Team]
- **M1 Bitcoin core (BDK)**: build deposit P2TR address + reclaim path; build & sign reclaim tx; construct sweep tx (deposit→peg UTXO); construct peg-out tx. Unit tests on regtest/signet. Deliverable: `bitcoin-core-rs` lib + CLI mirroring `sbtc` builders' behavior.
- **M2 Threshold signatures**: independent FROST/WSTS DKG + weighted signing producing a single Schnorr sig spendable by a Taproot key-path. Deliverable: `wsts-rs` with property tests + KATs vs BIP-340 test vectors.
- **M3 Clarity contracts**: token (implicit SIP-010), deposit, registry, withdrawal + rotate-keys. Clarinet unit tests. Deliverable: deployable contract set on devnet.
- **M4 Signer binary**: chain observers + coordinator + batching + integration with M1/M2/M3; single-UTXO invariant enforced. Deliverable: `signer` running against devnet (bitcoind regtest + stacks devnet).
- **M5 Emily API**: implement OpenAPI; PENDING/ACCEPTED/CONFIRMED/FAILED lifecycle; signer notification + status polling. Deliverable: `emily-api` + client SDK.
- **M6 Bridge web**: deposit/withdraw/status UI, wallet connect (Stacks + Bitcoin), independent design (no copied components). Deliverable: `bridge-web`.
- **M7 x402 gateway**: HTTP-402 middleware fronting bridge service endpoints (e.g., "notify deposit," "priority sweep," "withdrawal SLA"); integrate GoPlausible Algorand facilitator; ASA opt-in; atomic-group fee abstraction; idempotency to prevent payment replay. Deliverable: `x402-gateway`.
- **M8 Agent integration**: register the bridge as an ERC-8004 agent (Identity NFT + registration JSON advertising endpoints & pricing); post reputation/validation hooks; list on AgenticPlace; mindX API client so mindX BDI agents can invoke the bridge as a tool; publish operational memory to rage.pythai.net. Deliverable: `agent-integration`.
- **M9 Governance (BONAFIDE DAIO)**: DAIO controls bridge parameters (signer set, maxSignerFee bounds, pause/emergency, fee recipients); dual-chain governance signaling; timelock; supermajority for constitutional/param changes. Deliverable: `contracts-evm` gov module + wiring to signer/Emily config.
- **M10 BANKON Vault**: route bridge fee revenue and/or peg-adjacent custody through BANKON Vault multichain accounting; map chains per the AgenticPlace chain list. Deliverable: vault integration adapters.

### PHASE 4 — Testing
- Bitcoin: rust-bitcoin/BDK unit tests; regtest + signet integration; construct real testnet deposits and reclaims; verify tapscript bytes and P2TR outputs match independently-derived values (interop check vs public sBTC testnet).
- WSTS: property-based tests, BIP-340 Schnorr KATs, adversarial/byzantine-coordinator tests, DKG abort/rotation.
- Clarity: Clarinet unit + integration; invariant tests (total supply = peg balance; locked-sBTC accounting on withdrawal).
- Signer: end-to-end devnet (bitcoind regtest + stacks devnet + Emily + signer) exercising deposit→mint and withdraw-request→peg-out→burn; single-UTXO invariant fuzzing; reorg handling (Bitcoin reorg → Stacks reorg for mint safety).
- **EVM contracts: Foundry** (`forge test`) — unit + fuzz + invariant tests for the ERC-8004 helper, x402 receipt contract, DAIO gov hooks; fork tests against mainnet ERC-8004 singleton addresses; `forge coverage`; gas snapshots.
- x402: 402→pay→retry happy path on Algorand testnet; replay/idempotency; ASA-not-opted-in failure; facilitator-down fallback.
- Agent: ERC-8004 registration resolves; reputation/validation writes authorized; mindX invokes bridge tool end-to-end.
- Security: threshold-sig external audit; Clarity audit; peg-wallet key-management review; x402 payment-replay review; DAIO governance-capture review (low-turnout/vote-buying scenarios).

### PHASE 5 — Deployment (mainnet)
- Testnet dress rehearsal: Bitcoin testnet/signet + Stacks testnet + Algorand testnet; full deposit/withdraw cycles; monitored for N cycles.
- Clarity: deploy token/deposit/registry/withdrawal to Stacks mainnet via Clarinet deployment plan; run initial `rotate-keys` to publish aggregate pubkey; verify deployer bootstrap.
- Signer set: stand up ≥ threshold independent signer nodes (bitcoind mainnet + stacks-node mainnet event observers); DKG ceremony to produce the peg aggregate key; fund/initialize the peg-wallet UTXO.
- Emily API: deploy behind TLS; idempotency; monitoring/health.
- **EVM contracts: deploy to mainnet with Foundry** (`forge script --broadcast --verify`); record ERC-8004 Identity NFT id + registration URI; deploy x402 receipt + DAIO gov contracts; transfer admin to the DAIO timelock.
- x402 gateway: production Algorand facilitator (or self-host x402-rs); opt-in to USDC/ASA; set per-endpoint pricing.
- AgenticPlace: publish agent listing; mindX + rage integration live; BANKON Vault revenue routing enabled.
- Runbooks: key rotation, emergency pause (via DAIO), signer failover, reorg response, incident disclosure.

## Recommendations
1. START Phase 0 + Phase 1 in parallel with a lawyer engaged NOW — the clean-room provenance log is the highest-value artifact and cheapest to get right early. No implementation before counsel signs off the spec module.
2. TREAT the peg wallet + WSTS as crown jewels: independent FROST implementation, external audit before ANY mainnet funds. Threshold gate: do not go to mainnet until WSTS passes audit AND a testnet signer set has run ≥ 2 weeks of live deposit/withdraw cycles with zero invariant violations.
3. CONFIRM the "openBDK = bitcoindevkit/bdk" assumption with the user before M1; if they meant a specific private/derivative kit, only the Bitcoin-core module changes.
4. KEEP EVM scope minimal and Foundry-tested: EVM is for agent identity (ERC-8004), payment receipts, and DAIO governance — NOT for holding BTC. Bitcoin custody stays in the BDK/WSTS/Clarity path.
5. USE x402-on-Algorand for METERED convenience services (deposit notification, priority sweeps, SLA withdrawals) rather than gating the core peg — keep the trust-minimized peg free/permissionless so you don't create a censorship chokepoint on user funds; monetize the orchestration layer.
6. WIRE governance last but design its hooks first: bridge config (signer set, fee bounds, pause) should read from DAIO from day one even if DAIO holds only test authority initially; flip to full DAIO control after audits.

Benchmarks that change the plan: if an external audit finds the independent WSTS unsafe → fall back to a vetted third-party FROST library (still clean-room re: sBTC). If the Algorand x402 facilitator proves unreliable → self-host x402-rs or add a Base/USDC facilitator as a CAIP-2 fallback. If ERC-8004 singletons are not deployed on your target chain → deploy them yourself or pick a chain from the AgenticPlace list that already has them.

## Caveats
- **openBDK is an assumption.** No GitHub project literally named "openBDK" was found; the roadmap assumes `bitcoindevkit/bdk`. Confirm with the user.
- **AgenticPlace chain list**: /allchain.html could not be fetched directly; enumerated chains come from AgenticPlace's public pages/API and are EVM-centric. Stacks and Bitcoin are not listed there; Algorand is used for NFT minting, not indexed as an EVM chain. Confirm the authoritative chain map with the user.
- **mindX / BANKON / BONAFIDE DAIO**: public technical detail is thin and marketing-heavy (lablab.ai, pythai.net). API shapes for mindX and BANKON Vault, and the exact dual-chain design of BONAFIDE DAIO, must be supplied by the user; the roadmap defines integration seams, not their internals.
- **License check required**: verify the actual licenses of `stacks-sbtc/sbtc`, `sbtc-bridge`, `wsts`, and the `sbtc` npm lib. Clean-room is used regardless to guarantee non-derivative provenance, but licenses affect whether you may even read the source at all.
- **Interoperability vs independence**: to interoperate with real sBTC on mainnet you must MATCH wire/script/contract interfaces exactly. If instead you are launching an INDEPENDENT PYTHAI-ecosystem peg (not canonical sBTC), interface divergence is fine — clarify intent with the user, as it changes M3/M4 acceptance criteria.
- **x402 adoption figures** (69k agents / 165M txns / ~$50M cumulative, late April 2026) are Coinbase/vendor numbers, directional only, not independently verified.
- **Patent risk is NOT eliminated** by clean-room; if any sBTC mechanism is patented, independent creation is not a defense — a separate patent clearance is advisable.