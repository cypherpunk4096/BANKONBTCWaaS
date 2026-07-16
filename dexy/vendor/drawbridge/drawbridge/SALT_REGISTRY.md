# SALT_REGISTRY.md

Canonical CREATE2 salt registry for the PYTHAI / BANKON Drawbridge system.
Convention: `<domain-anchor>/<component>/<version>` → `keccak256(preimage)` → bytes32 salt.
Full rules in [`docs/04_create2_salt_conventions.md`](docs/04_create2_salt_conventions.md).

Deployer (all rows): Arachnid deterministic-deployment-proxy
`0x4e59b44847b379578588920cA78FbF26c0B4956C`.

**Never hard-code the bytes32/initcode/address values from memory.** Fill each cell from a
fresh CI run: `make salts` for the bytes32 column; `forge inspect <C> bytecode | cast keccak`
for initcode hash; `cast create2 --deployer … --salt … --init-code-hash …` for the address.

| salt preimage | component → file | bytes32 (`make salts`) | initcode hash | address (same all chains) | chains | status |
|---|---|---|---|---|---|---|
| `pythai.net/tollkeeper/v1` | Troll → `troll.sol` (alias: `troll` excluded) | _fill_ | _fill_ | _fill_ | 1,42161,8453,10,137,43114 | reserved |
| `bankon.eth/royalt/v1` | RoyalT → `royalt.sol` | _fill_ | _fill_ | _fill_ | same | reserved |
| `bankon.eth/drawbridge/v2` | DrawbridgeV2 → `drawbridge.sol` | _fill_ | _fill_ | _fill_ | same | reserved |
| `bankon.eth/pai/v1` | PAIm → `pai_multichain.sol` | _fill_ | _fill_ | _fill_ | same | reserved |
| `bankon.eth/spring/v1` | Spring → `spring.sol` | _fill_ | _fill_ | _fill_ | per-chain | reserved |
| `pythai.net/toll_dial/v1` | TollDial → `toll_dial.sol` | _fill_ | _fill_ | _fill_ | 1 | reserved |
| `pythai.net/collateral_factory/v1` | CollateralFactory → `collateral_factory.sol` | _fill_ | _fill_ | _fill_ | 1 | reserved |
| `pythai.net/four_bucks_vat/v1` | FourBucksVat → `vat.sol` | _fill_ | _fill_ | _fill_ | 1 | reserved |
| `pythai.net/four_bucks_oracle/v1` | FourBucksOracle → `four_bucks_oracle.sol` | _fill_ | _fill_ | _fill_ | 1 | reserved |
| `pythai.net/abacus/v1` | Abacus → `abacus.sol` | _fill_ | _fill_ | _fill_ | 1 | reserved |
| `pythai.net/breaker/v1` | DeterministicBreaker → `breaker.sol` | _fill_ | _fill_ | _fill_ | 1 | reserved |

Canonical PAI (Circle Arc, USDC-backed) uses `bankon.eth/pai/v1` initcode differing from PAIm
(different constructor), so it is a distinct address family — deploy from `pai.sol` on Arc only.

## Alias table (single source of truth for filename↔component deviations)

| alias (salt component) | source file | reason |
|---|---|---|
| `tollkeeper` | `troll.sol` | namespace exclusion (`troll` barred from public preimages); named from function |

## Lifecycle

`reserved` (named, not yet deployed) → `live` (deployed + address asserted on every chain)
→ `retired` (superseded by vN+1; row kept forever as history, never deleted).

DAIO attests this registry as part of its advisory role; the registry hash is published to
rage.pythai.net via mindX.
