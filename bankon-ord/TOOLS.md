# The Ordinals Instrument Catalogue
### a curated collection of tools for the science of ordinals — see [SCIENCE.md](SCIENCE.md)

A working practitioner's toolbox. The **sovereign path** (run your own, trust no one) is listed first;
the wider ecosystem follows, distilled from the community `awesome-ordinals` lists.

> Sources: [neu-fi/awesome-ordinals](https://github.com/neu-fi/awesome-ordinals) (the canonical list,
> ~190★) · [openblockchains/awesome-ordinals](https://github.com/openblockchains/awesome-ordinals)
> (BTC/LTC/DOGE) · [ordex-io/awesome-ordinals](https://github.com/ordex-io/awesome-ordinals) ·
> [crypt0biwan/awesome-ordinals](https://github.com/crypt0biwan/awesome-ordinals). Third-party services
> are **custodial or hosted** unless you self-host — for real self-custody use the sovereign tools.

---

## 0 · Sovereign / self-hosted (trust your own node)
| Tool | Where | What |
|---|---|---|
| **bankon-ord** | this module | the ord CLI, wallet-isolated + gated, mainnet/testnet |
| **bankonvault/ord** | https://github.com/bankonvault/ord | our fork of ordinals/ord — "rare and exotic sats" |
| **ordinals/ord** | https://github.com/ordinals | the reference index + explorer + wallet (Rust, CC0) |
| **wallet-utils** | https://github.com/Professor-Codephreak/wallet-utils | unisat ordinal wallet utilities |
| **Umbrel Ord Indexer** | https://apps.umbrel.com/app/ordinals | one-click self-hosted ord index |
| **BRC-20 Indexer** | https://github.com/Next-DAO/brc20_indexer | open-source BRC-20 indexer you can run |

## 0.5 · satoshigen — the ordinals org (our curated collection)
[**github.com/satoshigen**](https://github.com/satoshigen) is the BANKON ordinals organization: a
curated fork-collection of the best open-source ordinals tooling, kept as our sovereign toolbox.

| Repo | URL | What |
|---|---|---|
| ord | https://github.com/satoshigen/ord | the reference index/explorer/wallet — "rare and exotic sats" |
| ordit-sdk | https://github.com/satoshigen/ordit-sdk | Ordinals-aware TypeScript library (powerful, batteries-included) |
| sats | https://github.com/satoshigen/sats | zero-dependency TS lib to operate on sats per the Ordinals Handbook |
| js-1sat-ord | https://github.com/satoshigen/js-1sat-ord | 1Sat Ordinals JS library |
| ord-utils / ord-connect / ordconnect | https://github.com/satoshigen/ord-connect | wallet-connect kits + ordinal utilities (React) |
| msigner | https://github.com/satoshigen/msigner | **PSBT signer for atomic inscription swaps** — marketplace-grade, secure |
| wallet-utils | https://github.com/satoshigen/wallet-utils | unisat ordinal wallet utilities |
| local-wallet | https://github.com/satoshigen/local-wallet | a local ordinal wallet |
| extension | https://github.com/satoshigen/extension | open-source browser-extension wallet for Bitcoin NFTs |
| ordinals-explorer | https://github.com/satoshigen/ordinals-explorer | explorer UI on the Hiro API |
| deezy-place | https://github.com/satoshigen/deezy-place | decentralized ordinals marketplace |
| ordinals-academy | https://github.com/satoshigen/ordinals-academy | free Bitcoin Ordinals education |
| rust-bitcoincore-rpc | https://github.com/satoshigen/rust-bitcoincore-rpc | Rust Core JSON-RPC client |
| awesome-ordinals | https://github.com/satoshigen/awesome-ordinals | the ecosystem list (our copy) |
| unisat-web3-demo · unisat-docs | https://github.com/satoshigen/unisat-web3-demo | UniSat web3 examples + docs |

> `msigner` and `ordit-sdk` are the standouts for building sovereign ordinals flows on top of
> `bankon-ord` — PSBT-level control and a real TS SDK, both isolation-friendly.

## 1 · Indexers & developer APIs
| Tool | URL |
|---|---|
| Hiro Ordinals API | https://docs.hiro.so/ordinals |
| Best In Slot API | https://docs.bestinslot.xyz/ |
| 1Sat Ordinals API | https://docs.1satordinals.com/public-apis |
| OrdAPI (metadata) | https://ordapi.xyz/ |
| Ordiscan API | https://ordiscan.com/docs/api |
| Ordinal Hub API | https://api.ordinalhub.com/docs |
| Magic Eden API | https://docs.magiceden.io/reference/ordinals-overview |
| Sats Names API | https://docs.sats.id/buildooors/indexer-and-api |

## 2 · Explorers (observatories)
| Tool | URL |
|---|---|
| Ordinals.com (official) | https://ordinals.com |
| Ordiscan | https://ordiscan.com |
| Ord.io | https://ord.io |
| OrdinalHub | https://www.ordinalhub.com/ |
| Ordeets | https://ordeets.com |

## 3 · Wallets (keep these ORDINAL-only — never mix with plain BTC)
| Tool | URL |
|---|---|
| OrdinalSafe | https://ordinalsafe.xyz |
| UniSat | https://unisat.io/download |
| Xverse | https://xverse.app |
| Hiro | https://wallet.hiro.so |
| Nosft | https://nosft.xyz |

## 4 · Inscription / creator tools
| Tool | URL |
|---|---|
| UniSat Inscribe | https://unisat.io/inscribe |
| OrdinalsBot | https://ordinalsbot.com/ |
| Gamma | https://gamma.io/ordinals |
| InscribeNOW | https://inscribenow.io/ |
| Ordimint | https://ordimint.com/ |

## 5 · Rare-sat instruments (the taxonomy in practice)
| Tool | URL |
|---|---|
| Magisat | https://magisat.io |
| Sating | https://sating.io |
| SatScribe | https://satscribe.xyz |
| Rare Ordinal Directory | https://rareordinal.directory |

## 6 · BRC-20 / token analytics
| Tool | URL |
|---|---|
| BRC-20 Stats | https://brc-20.io/ |
| 20scan | https://20scan.com/indexer |
| OrdSpy | https://ordspy.com |

## 7 · Marketplaces & aggregators (non-custodial where noted)
| Tool | URL |
|---|---|
| Magic Eden (ordinals) | https://magiceden.io/ordinals |
| OpenOrdex (DEX) | https://openordex.org/ |
| Ordinals Market | https://ordinals.market |
| Best in Slot (aggregator) | https://bestinslot.xyz |
| Ord Exchange (aggregator) | https://ord.exchange/ |

## 8 · Layer-2 / DeFi (adjacent)
| Tool | URL |
|---|---|
| Stacks | https://www.stacks.co/ |
| Rootstock | https://rootstock.io |
| Liquidium | https://liquidium.fi/ |

---

### Practitioner's note
Every hosted service above sees what you show it. For anything touching keys, stay on the **sovereign
path** (§0): your own `ord` against your own node, ordinal wallets isolated, actions gated — which is
exactly what `bankon-ord` enforces. Observe with the ecosystem; **hold with your own instrument.**
