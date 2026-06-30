# Upstream Bitcoin Core docs (local copies)

BANKON-relevant documentation copied verbatim from the Bitcoin Core source tree
(`/home/luvai/bitcoin/doc/`, v31). **© Bitcoin Core developers, MIT-licensed** — included
here for offline reference. Authoritative source: https://github.com/bitcoin/bitcoin/tree/master/doc

| File | Why it matters to BANKON |
|------|--------------------------|
| [managing-wallets.md](managing-wallets.md) | create/load/backup/migrate descriptor wallets |
| [descriptors.md](descriptors.md) | output descriptors — how BANKON registers watch-only wallets |
| [psbt.md](psbt.md) | PSBT workflow — the basis of non-custodial signing |
| [multisig-tutorial.md](multisig-tutorial.md) | N-of-M `sortedmulti` — BANKON's multisig path |
| [offline-signing-tutorial.md](offline-signing-tutorial.md) | air-gapped signing — mirrors `offline-client.html` |
| [JSON-RPC-interface.md](JSON-RPC-interface.md) | the RPC interface the Console/WaaS consume |
| [assumeutxo.md](assumeutxo.md) | fast pruned-node bootstrap (Phase 1 enhancement) |
| [reduce-memory.md](reduce-memory.md) | dbcache/memory tuning for the node |
| [bitcoin-conf.md](bitcoin-conf.md) | bitcoin.conf reference |

Local command reference: [../bitcoin-core-rpc.md](../bitcoin-core-rpc.md) ·
wallet RPCs categorized: [../wallet-categories.md](../wallet-categories.md)
