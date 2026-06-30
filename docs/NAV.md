# BANKON — Documentation Navigation

Master index of every BANKON document. Paths are relative to `docs/` (root files are `../`).
New here? Start with **[../README.md](../README.md)** then **[../USAGE.md](../USAGE.md)**.

## Start here
| Doc | Hook |
|-----|------|
| [../README.md](../README.md) | What BANKON is; one-command start; license summary |
| [../USAGE.md](../USAGE.md) | Complete startup guide — web UI · Qt · CLI |
| [getting-started.md](getting-started.md) | Install, `bankon up`, the URLs, one-click Core install |
| [README.md](README.md) | Docs landing + 30-second version |

## Core guides
| Doc | Hook |
|-----|------|
| [architecture.md](architecture.md) | Multi-node design, non-custodial model, data flow |
| [wallets.md](wallets.md) | Wallet types, BTC Standard, create → sign → send, multisig |
| [api.md](api.md) | WaaS + Console HTTP API reference |
| [console.md](console.md) | The diagnostics Console (tabs, levels, RPC catalog, Network topology map, node actions) |
| [ragebtc.md](ragebtc.md) | **RAGEbtc** — rageRPC accelerated/self-protecting RPC controller + RAGE handoff to pgvectorscale |
| [security.md](security.md) | Non-custodial guarantees, auth, backups, offline client |
| [operations.md](operations.md) | Launcher, systemd, monitoring, multi-node, pruning |
| [testing.md](testing.md) | Sign / e2e / multisig / anchor test suites |
| [waas-accuracy.md](waas-accuracy.md) | Accurate WaaS positioning — proven vs built vs gated vs future |
| [wallet-categories.md](wallet-categories.md) | All Bitcoin Core wallet RPCs, categorized |
| [bitcoin-core-rpc.md](bitcoin-core-rpc.md) | Local Bitcoin Core RPC command reference |

## BANKON QT (desktop app)
| Doc | Hook |
|-----|------|
| [QTbankonQT.md](QTbankonQT.md) | **Complete QT reference** — architecture, the 9 tabs, data flow, review of the design guides |
| [roadmap.md](roadmap.md) | QT roadmap + todo (native now → allchain → 3D globe → health surface) |
| [qt/Master Architect Guide](qt/Qt%206%20Desktop%20Wallet%20with%20Bitcoin%20Core%20Anchor%20for%20BANKON%20Chain-Agnostic%20WaaS_%20Master%20Architect%20Guide.md) | *Aspirational:* Qt 6 chain-agnostic wallet (RPC+ZMQ, OP_RETURN anchor, ChainAdapter) |
| [qt/Engineering Reference](qt/bankon_qt_engineering_reference.md) | *Aspirational:* Qt 6 house style, QML tooling, LGPL matrix |
| [qt/3D Globe Reference](qt/Scientifically%20Accurate%203D%20Globe%20for%20Bitcoin%20Network%20Visualization%20in%20Qt%206_%20Architecture%20and%20Implementation%20Reference.md) | *Aspirational:* scientifically-accurate WGS84 3D globe |
| [qt/Clean-house allchain Globe](qt/bankon_qt_clean_house_allchain_globe.md) | *Aspirational:* clean-house multi-chain globe spanning `allchainz` |

## Planning
| Doc | Hook |
|-----|------|
| [STRATEGY.md](STRATEGY.md) | **Strategy** — north-star (ship Bitcoin WaaS first), critical path + gates, long-term phases |
| [ROADMAP.md](ROADMAP.md) | Whole-BANKON phased build plan + status |
| [TODO.md](TODO.md) | Whole-BANKON prioritized, actionable task list |
| [roadmap.md](roadmap.md) | QT-specific roadmap + todo |

## Policy & operations
| Doc | Hook |
|-----|------|
| [../POLICY.md](../POLICY.md) | Licensing & encryption policy (GPLv3 + MIT, user sovereignty) |
| [../PRUNING.md](../PRUNING.md) | Prune size-vs-security analysis |

## Components
| Doc | Hook |
|-----|------|
| [../bankon-waas/README.md](../bankon-waas/README.md) | WaaS service notes |
| [../bankon-waas/db/README.md](../bankon-waas/db/README.md) | Node-intelligence DB — Postgres + pgvectorscale collector (geo/version/uptime) |

## Reference & upstream
| Doc | Hook |
|-----|------|
| [inspiration-bitnodes.md](inspiration-bitnodes.md) | Bitnodes review — ideas BANKON borrows |
| [reference/bitnodes/README.md](reference/bitnodes/README.md) | Local Bitnodes source reference (MIT) |
| [upstream/README.md](upstream/README.md) | Local copies of key Bitcoin Core docs |
| [upstream/descriptors.md](upstream/descriptors.md) · [upstream/psbt.md](upstream/psbt.md) · [upstream/multisig-tutorial.md](upstream/multisig-tutorial.md) | Descriptors · PSBT · multisig |
| [upstream/offline-signing-tutorial.md](upstream/offline-signing-tutorial.md) · [upstream/managing-wallets.md](upstream/managing-wallets.md) | Offline signing · wallet management |
| [upstream/JSON-RPC-interface.md](upstream/JSON-RPC-interface.md) · [upstream/bitcoin-conf.md](upstream/bitcoin-conf.md) | JSON-RPC interface · bitcoin.conf |
| [upstream/assumeutxo.md](upstream/assumeutxo.md) · [upstream/reduce-memory.md](upstream/reduce-memory.md) | assumeUTXO · reduce memory |
