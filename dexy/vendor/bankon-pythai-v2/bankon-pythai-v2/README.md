# BANKON PYTHAI v2

LayerZero OFT V2 implementation of BANKON PYTHAI (BKPY) — fixed supply 111,111.111 tokens,
omnichain across EVM chains, with a governed bridge to the Algorand/x402 payment rail.
Built to the cypherpunk2048 standard: Foundry-only testing, mainnet-only deployment, no
upgradeable proxies, no admin EOA after DAIO handoff.

## Contents

```
src/
  BankonPythaiOFT.sol           OFT V2 token — hub mints fixed supply, spokes don't
  ChainRegistry.sol             on-chain mirror of agenticplace.pythai.net/allchain.html
  gateway/
    X402AlgorandGateway.sol     DAIO-governed bridge to PARSEC x402 / Algorand rail
script/
  DeployHub.s.sol                deploy + mint (run once, one chain)
  DeploySpoke.s.sol              deploy, no mint (run per additional chain)
  WireOApp.s.sol                 setPeer + setEnforcedOptions (run per chain)
  TransferToDAIO.s.sol           final ownership + delegate handoff (run per chain)
test/
  BankonPythaiOFT.t.sol          Foundry unit tests
config/
  chains.json                    eid / chainId / endpoint / oft-address mapping
docs/
  INTEGRATION.md                 AgenticPlace / mindX / BANKON / PARSEC integration notes
  DVN_SECURITY.md                DVN & security-stack checklist
  X402_BRIDGE.md                 Algorand bridge trust model
```

## Setup

```bash
forge init --no-git   # if starting fresh; otherwise just `forge install` into this repo

forge install layerzero-labs/devtools
forge install layerzero-labs/LayerZero-v2
forge install OpenZeppelin/openzeppelin-contracts
forge install foundry-rs/forge-std
git submodule add https://github.com/GNSPS/solidity-bytes-utils.git lib/solidity-bytes-utils
```

`remappings.txt` is already configured for the above layout. Adjust if your `lib/` paths
differ (e.g. if `oft-evm` / `oapp-evm` resolve from a different devtools subpath — check
`lib/devtools/packages/` after install).

## Environment variables

```
PRIVATE_KEY=                 # deployer key (never reused after TransferToDAIO.s.sol runs)
LZ_ENDPOINT=                 # LayerZero Endpoint V2 address for the target chain
HUB_RECIPIENT=                # treasury/Aerarium address to receive the fixed supply
BKPY_ADDRESS=                 # this chain's deployed BKPY OFT (for Wire/Transfer scripts)
DAIO_GOVERNANCE_ADDRESS=      # BANKON DAIO governance contract (bankon.pythai.net stack)
ETHEREUM_MAINNET_RPC=
ARBITRUM_MAINNET_RPC=
OPTIMISM_MAINNET_RPC=
BASE_MAINNET_RPC=
BNB_MAINNET_RPC=
POLYGON_MAINNET_RPC=
ETHERSCAN_API_KEY= / ARBISCAN_API_KEY= / etc.
```

Verify all `LZ_ENDPOINT` values and `lzEid`s in `config/chains.json` against
https://docs.layerzero.network/v2/deployments/deployed-contracts before broadcasting —
this package ships the standard published V2 values as of authoring time, but LayerZero
periodically updates its deployment list.

## Test

```bash
forge test -vvv
```

`test/BankonPythaiOFT.t.sol` covers chain-local invariants (fixed-supply minting, hub/spoke
gating, ownership/delegate handoff, `ChainRegistry` access control) using a minimal endpoint
stub. For full cross-chain send/receive simulation, add a second test file using
`@layerzerolabs/test-devtools-evm-foundry`'s `TestHelperOz5` + `EndpointV2Mock`, and assert
balances after `verifyPackets()` across two mock endpoints.

## Deploy (mainnet only — see docs/INTEGRATION.md for full order and rationale)

```bash
# 1. Hub chain (mints fixed supply)
forge script script/DeployHub.s.sol:DeployHub --rpc-url ethereum --broadcast --verify -vvvv

# 2. Every additional chain
forge script script/DeploySpoke.s.sol:DeploySpoke --rpc-url arbitrum --broadcast --verify -vvvv
# ...repeat for optimism, base, bnb, polygon...

# 3. Update config/chains.json with each deployed `oft` address, then wire every chain
forge script script/WireOApp.s.sol:WireOApp --rpc-url ethereum --broadcast -vvvv
# ...repeat --rpc-url for every other chain...

# 4. Hand off to DAIO governance (per chain, LAST step)
forge script script/TransferToDAIO.s.sol:TransferToDAIO --rpc-url ethereum --broadcast -vvvv
```

No testnets are referenced anywhere in this package by design — all RPC endpoints in
`foundry.toml` point at mainnet networks only, per project convention.

## Ecosystem integration

See `docs/INTEGRATION.md` for how this connects to AgenticPlace, mindX (and its API),
BANKON, rage.pythai.net, and the PARSEC x402/Algorand payment rail via
`parsec` / `parsec-wallet`.

## License

Apache-2.0, (c) BANKON — per the cypherpunk2048 standard. LayerZero's own OFT/OApp
contracts (imported as a dependency, not vendored here) are separately licensed — check
`lib/devtools` and `lib/LayerZero-v2` for their respective licenses (MIT for the devtools
OFT/OApp packages).
