# BANKON PYTHAI v2 — Ecosystem Integration

This package implements BANKON PYTHAI as a LayerZero OFT V2 omnichain token and wires it
into the wider PYTHAI / DELTAVERSE / DAIO stack:

## Surfaces

- **AgenticPlace** (`agenticplace.pythai.net`) — ERC-8004 agent marketplace and chain
  registry. This package's `src/ChainRegistry.sol` mirrors the chain-mapping data model at
  `agenticplace.pythai.net/allchain.html` on-chain, so AgenticPlace agents and the DAIO
  governance contract can resolve `{evmChainId, lzEid, name, oft address}` without an
  off-chain fetch. Populate it from `config/chains.json` as each chain deploys.
- **mindX** (`mindx.pythai.net`) — the BDI cognitive engine with its own API. mindX's API
  is the natural caller of `X402AlgorandGateway.lockForAlgorand` / the settlement read path
  when an agent needs to pay for or receive a BKPY-denominated x402 micropayment. mindX
  publishes findings and training content to `rage.pythai.net`, which can likewise be gated
  behind x402 payments using the same gateway.
- **BANKON** (`bankon.pythai.net`) — identity, payment, governance layer. The DAIO
  governance contract living here (Boardroom / WarCouncil / Aerarium per your existing
  seven-contract suite) is the intended `DAIO_GOVERNANCE_ADDRESS` target of
  `script/TransferToDAIO.s.sol` and the `owner` of `X402AlgorandGateway`.
- **PARSEC / parsec-wallet** — the x402 payment rail on Algorand (via GoPlausible's
  `@x402-avm`). Not LayerZero-compatible (Algorand is not an EVM/LayerZero chain). Bridged
  to the EVM/BKPY side only through `src/gateway/X402AlgorandGateway.sol`, which is a
  DAIO-governed, threshold-attested lock/mint gateway — not a native OFT peer. See
  `docs/X402_BRIDGE.md` for the full trust model.

## Deployment order (mainnet only, Foundry only)

1. `forge install` the dependencies listed in `README.md`.
2. `script/DeployHub.s.sol` — deploy on the hub chain (e.g. Ethereum mainnet), mints the
   fixed 111,111.111 supply, also deploys `ChainRegistry`.
3. `script/DeploySpoke.s.sol` — deploy on every additional chain (Arbitrum, Optimism, Base,
   BNB, Polygon, ...). No minting occurs here.
4. Update `config/chains.json` with each deployed `oft` address as it becomes available.
5. `script/WireOApp.s.sol` — run ONCE PER CHAIN (including the hub) to `setPeer` +
   `setEnforcedOptions` against every other chain in `config/chains.json`.
6. Populate `ChainRegistry` (via `addChain`) on the hub chain with the final chain set.
7. Deploy `X402AlgorandGateway` (per chain that needs an Algorand settlement leg — typically
   the hub only, or wherever mindX's primary treasury sits) and configure DAIO-appointed
   attestors + threshold.
8. `script/TransferToDAIO.s.sol` — LAST step, per chain: hands `owner` + endpoint `delegate`
   to the DAIO governance contract. No EOA retains privileged access after this step.

## Why this order matters

Steps 5–7 all use `onlyOwner` functions. Once step 8 runs on a given chain, only the DAIO
governance contract (via its own vote/multisig process) can call `setPeer`,
`setEnforcedOptions`, `setDelegate`, or `ChainRegistry.addChain` on that chain again. This is
intentional — it satisfies the "no admin keys post-deploy" convention without the
irreversibility of `renounceOwnership()`, which would prevent ever adding a new chain or
correcting a misconfigured DVN/Executor pathway.
