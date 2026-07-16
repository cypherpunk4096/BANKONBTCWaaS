# CREATE2 Salts — Theory, Security, and the PYTHAI/BANKON Naming Convention
### Canonical salt discipline for same-address multichain deployment
*Apache-2.0 · cypherpunk2048 · companion to `troll_golden_gateway.md`*

---

## 1. What a salt is

CREATE2 (EIP-1014, https://eips.ethereum.org/EIPS/eip-1014) computes a contract address as:

```
address = keccak256( 0xff ++ deployer ++ salt ++ keccak256(initcode) )[12:]
```

Four inputs, four consequences:

| Input | If it changes | Discipline |
|---|---|---|
| `0xff` | never | constant by spec |
| `deployer` | new address | always use the canonical CREATE2 proxy `0x4e59b44847b379578588920cA78FbF26c0B4956C` |
| `salt` (bytes32) | new address | **this doc** — the salt is the *name* of a contract identity |
| `keccak256(initcode)` | new address | initcode = creation bytecode ++ ABI-encoded constructor args; pin the toolchain (§5) |

The salt is the only input you choose freely. It is therefore the **namespace of your deterministic universe**: one salt = one contract identity, at one address, on every EVM chain, forever. Treat salts like domain names, not like nonces.

---

## 2. The deterministic deployment stack

| Deployer | Address | Notes |
|---|---|---|
| **Arachnid deterministic-deployment-proxy** (canonical) | `0x4e59b44847b379578588920cA78FbF26c0B4956C` | Present on effectively every EVM chain; Foundry's default for `new Contract{salt: …}()` broadcast. https://github.com/Arachnid/deterministic-deployment-proxy |
| ERC-2470 Singleton Factory | `0xce0042B868300000d44A59004Da54A005ffdcf9f` | https://eips.ethereum.org/EIPS/eip-2470 |
| CreateX | `0xba5Ed099633D3B313e4D5F7bdc1305d3c28ba5Ed` | Salt-protection modes incl. msg.sender-guarded salts. https://github.com/pcaversaccio/createx |

**PYTHAI standard: Arachnid proxy, raw keccak256 string salts.** The Arachnid proxy is permissionless — anyone can submit your `(salt, initcode)` on any chain. For cypherpunk2048 contracts (zero constructor args, immutable, no owner) this is a *feature*: a stranger deploying your exact bytecode at your exact address just did your ops work for free, and the result is byte-identical. It becomes a hazard only when constructors grant privileges to `msg.sender` or read deploy-time environment — which your standard forbids anyway. If a contract ever *must* carry deployer privilege at construction, use CreateX msg.sender-guarded salts instead; do not use the raw Arachnid proxy for it.

---

## 3. The PYTHAI/BANKON salt naming convention

**Canonical format** (hashed with `keccak256` to produce the bytes32 salt):

```
<domain-anchor>/<component>/<version>
```

Three fields, lowercase, `/`-separated, ASCII only, no whitespace. This is deliberately URL-path-shaped: salts are addresses in a namespace you already own.

### Field 1 — domain anchor: an identity you control

The anchor is a DNS or ENS name **owned by the deploying organization**, exactly as registered. This gives every salt human-auditable provenance: anyone reading `keccak256("pythai.net/tollkeeper/v1")` on-chain can verify who claims the identity by checking who controls pythai.net.

| Anchor | Scope | Examples |
|---|---|---|
| `bankon.eth` | Treasury, payments, tolls, pegged-asset finance — everything whose value flows touch the bankon.eth treasury | `bankon.eth/spring/v1` · `bankon.eth/drawbridge/v1` · `bankon.eth/pai/v1` |
| `pythai.net` | Protocol infrastructure — oracles, registries, governance plumbing, gate/bridge machinery | `pythai.net/tollkeeper/v1` · `pythai.net/four_bucks_oracle/v1` · `pythai.net/toll_dial/v1` · `pythai.net/collateral_factory/v1` |
| `agenticplace.pythai.net` | Agent-marketplace / ERC-8004 surface contracts | `agenticplace.pythai.net/chain_registry/v1` |
| `rage.pythai.net` | Publication/attestation surface contracts | `rage.pythai.net/attestor/v1` |

Rule of thumb: **money contracts anchor on bankon.eth; machinery contracts anchor on pythai.net.** One override exists — the **namespace exclusion list**: certain code names are barred from public salt preimages (currently: `troll`). An excluded contract deploys under a **registered alias** instead. TROLL, the guardian and tollkeeper of the bridge, therefore takes its deterministic identity under the pythai.net anchor as:

```solidity
bytes32 constant SALT = keccak256("pythai.net/tollkeeper/v1");
```

### Field 2 — component: the contract's flat snake_case name, or a registered alias

Default: the component matches your flat snake_case file layout exactly — `four_bucks_oracle.sol` → `four_bucks_oracle`. One source file, one component name, one salt lineage.

**Alias rule.** A component may deviate from the filename only through a row in the registry's alias table (§6). Two legitimate reasons:

1. **Namespace exclusion** — the code name is barred from public preimages. `troll.sol` → alias `tollkeeper`, named from function (what it does: keeps the toll), not from character.
2. **Function codes** — terse, function-derived codes are permitted *if registered*: `db` for drawbridge is valid **only** with an alias-table row `db → drawbridge.sol`. Unregistered abbreviations are forbidden. Note the trade-off honestly: the salt string is hashed to bytes32 either way, so brevity buys zero gas and zero bytes — its only effect is on the human reading the registry. `drawbridge` is self-evident; `db` reads as "database" to a stranger in five years. Prefer full words; use codes only where a name must be obscured or a fixed code system already exists.

Alias table (current):

| alias (salt component) | source file | reason |
|---|---|---|
| `tollkeeper` | `troll.sol` | namespace exclusion (`troll` barred); named from function |

No camelCase, no whitespace, no unregistered renaming between file and salt — the alias table is the single source of truth for every deviation.

### Field 3 — version: monotonic, never reused, never deleted

`v1`, `v2`, `v3`… Each version is a **new immutable address family across all chains**. You bump the version when — and only when — any of these change:

1. Source code (any byte)
2. Constructor arguments (for zero-arg contracts: n/a, which is why zero-arg is preferred)
3. Compiler settings that alter bytecode (solc version, optimizer runs, evm_version, metadata)
4. The compiled-in per-chain config table (e.g., adding a chain to TROLL's chainid switch)

A version is *retired*, never *recycled*. `pythai.net/tollkeeper/v1` refers to one exact initcode hash for the rest of time, even after v2 ships. This is what makes salts function as an audit trail.

### Anti-patterns (forbidden)

- ❌ `keccak256("PYTHAI.TROLL.v1")` — no identity anchor; uppercase; dot-separated ambiguity with the domain field (this was the v0 style; superseded by this doc)
- ❌ Raw incrementing salts (`bytes32(uint256(1))`) — meaningless, unauditable, collision-prone across teams
- ❌ Timestamps, block numbers, git hashes in salts — breaks reproducibility and cross-chain identity
- ❌ Secrets or private data in the preimage — salts are public the moment you deploy; assume the preimage will be brute-forced from your naming convention (it will, trivially — that's the point)
- ❌ Reusing a salt string with different initcode "because the old deployment failed" — if initcode differs, the address differs anyway; if it doesn't, you've created ambiguity in your own records. New attempt with changed code = new version.
- ❌ ENS anchors you don't control, or anchors whose ownership could lapse — renew bankon.eth well ahead of expiry; the salt's social meaning depends on it

---

## 4. Computing and verifying salts

```bash
# The bytes32 salt itself
cast keccak "pythai.net/tollkeeper/v1"

# Predict the address (identical on every chain)
cast create2 \
  --deployer 0x4e59b44847b379578588920cA78FbF26c0B4956C \
  --salt $(cast keccak "pythai.net/tollkeeper/v1") \
  --init-code-hash $(cast keccak "$(forge inspect Troll bytecode)")

# Optional vanity mining (leading zeros ≈ cheaper calldata when the address is an arg)
cast create2 --starts-with 0000 --deployer 0x4e59...956C --init-code-hash <hash>
```

```solidity
// In Foundry scripts/tests
address predicted = vm.computeCreate2Address(
    keccak256("pythai.net/tollkeeper/v1"),
    keccak256(type(Troll).creationCode),
    0x4e59b44847b379578588920cA78FbF26c0B4956C
);
```

Vanity note: mined vanity salts (random bytes producing a pretty address) are permitted **only** as a suffix extension of the convention — `pythai.net/tollkeeper/v1/<mined-hex>` — so the human-readable lineage survives. Never replace the named preimage with raw mined bytes.

---

## 5. Reproducibility: the initcode side of the equation

The salt convention is worthless if initcode drifts between compiles. Pin everything in `foundry.toml`:

```toml
[profile.default]
solc_version   = "0.8.26"      # exact, never a range
optimizer      = true
optimizer_runs = 200           # part of bytecode identity
evm_version    = "cancun"      # part of bytecode identity
bytecode_hash  = "none"        # strip IPFS metadata hash — CRITICAL
cbor_metadata  = false         # strip CBOR metadata entirely
```

`bytecode_hash = "none"` + `cbor_metadata = false` matter most: by default solc appends a metadata hash that changes with *source file paths, comments, and whitespace* — meaning a cosmetic edit would silently change your cross-chain address. Stripping metadata makes initcode a pure function of semantic code + settings. (Foundry config reference: https://book.getfoundry.sh/reference/config/solidity-compiler)

Commit the exact initcode hash next to the salt in the registry (§6) the moment v1 is frozen.

---

## 6. The salt registry — publish or it didn't happen

Every deployed salt gets a row in `SALT_REGISTRY.md` at the repo root (and mirrored to rage.pythai.net via mindX publication):

```markdown
| salt preimage              | bytes32 (cast keccak)  | initcode hash | address | chains | status |
|----------------------------|------------------------|---------------|---------|--------|--------|
| pythai.net/tollkeeper/v1        | 0x… (fill from cast)   | 0x…           | 0x…     | 1,42161,8453,10,137,43114 | live |
| bankon.eth/pai/v1          | 0x…                    | 0x…           | 0x…     | same   | live |
| bankon.eth/drawbridge/v1   | 0x…                    | 0x…           | 0x…     | same   | live |
| pythai.net/toll_dial/v1    | 0x…                    | 0x…           | 0x…     | 1      | live |
```

Statuses: `reserved` (named, not yet deployed) → `live` → `retired` (superseded by vN+1; still valid history, never deleted). DAIO attests the registry as part of its advisory role — the registry hash is exactly the kind of artifact BONAFIDE-style attestation exists for. Never hard-code the hash values from memory or from this doc; every bytes32 in the registry must come from a fresh `cast keccak` run in CI.

---

## 7. Security checklist (per salt, before broadcast)

1. **Zero constructor args?** If not, and the constructor reads `msg.sender` or grants privilege → do *not* use the permissionless Arachnid proxy; anyone front-running your deployment on a new chain would own the privilege. Use CreateX sender-guarded salts or redesign to zero-arg (preferred, per cypherpunk2048).
2. **Preimage contains no secrets** — assume public.
3. **Initcode hash matches across your CI and a clean-room build** (two machines, same hash) before the salt is marked `reserved`.
4. **Anchor domain/ENS ownership verified and renewal calendared** — bankon.eth expiry is now a protocol dependency.
5. **Version bump audit**: diff against the previous version's frozen source; confirm the bump reason is recorded in the registry row.
6. **Cross-chain address assertion in the deploy script**: `require(deployed == predicted)` on every chain; abort the loop on first mismatch.
7. **Same-initcode squatting check**: if the address is already deployed on a target chain, verify the on-chain runtime bytecode matches yours byte-for-byte before treating it as your deployment (benign for zero-arg immutables, but verify — `cast code <addr> --rpc-url …` diffed against `forge inspect Troll deployedBytecode`).

---

## 8. Applied: the current PYTHAI salt set

With this convention, the TROLL deploy script and tests now read:

```solidity
bytes32 constant SALT = keccak256("pythai.net/tollkeeper/v1");
```

and the full v1 family is:

| Contract | Salt preimage | Anchor rationale |
|---|---|---|
| Troll (guardian & tollkeeper, PAI↔USDC gate) | `pythai.net/tollkeeper/v1` | `troll` excluded from namespace; alias named from function |
| PAIm (multichain PAI token) | `bankon.eth/pai/v1` | pegged-asset finance |
| Drawbridge (LZ V2 transport) | `bankon.eth/drawbridge/v1` | moves treasury-backed value |
| TollDial (fee timelock) | `pythai.net/toll_dial/v1` | governance machinery |
| CollateralFactory (FOUR BUCKS onboarding) | `pythai.net/collateral_factory/v1` | governance machinery |
| FourBucksOracle (Pyth + fallback) | `pythai.net/four_bucks_oracle/v1` | infrastructure |

One convention, one registry, one address per identity — on every chain the salt was born knowing.
