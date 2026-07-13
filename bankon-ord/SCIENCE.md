# Ordinals: A Science
### — a field manual for the observation and cataloguing of individual satoshis —

> *"They were here all along. Twenty-one quadrillion of them, each already numbered at the moment of
> its minting, drifting in first-in-first-out procession through the mempool like particles through a
> cloud chamber. We did not invent them. We built the instruments to **see** them."*
> — field notes, cypherpunk2048

Ordinal theory is not a token, a sidechain, or a change to Bitcoin. It is an **observational science**:
a rigorous claim that each satoshi carries an intrinsic, deterministic **identity**, and a set of
instruments (indexers, explorers) for reading that identity off the chain. Below is the discipline,
stated as laws, a taxonomy, and a lab protocol.

## SciFi — Scientific Finance
**SciFi is Scientific Finance.** The pun is the thesis: the sci-fi aesthetic is not decoration, it is a
*method* — treat money like a science, where **every claim is measured, not asserted.**

> **DeFi provides *decentralization*. SciFi provides *accuracy*. You want both.**

The two are complementary, not rival. **DeFi = Decentralized Finance** removes the central party.
**SciFi = Scientific Finance** removes the unverified claim — it supplies the reproducible accuracy
DeFi's decentralization is worthless without. Decentralized *and* wrong is still wrong; the aim is
decentralized *and* verifiable.

Where DeFi still leans on trust dressed as code — oracles you must believe, bridges you must hope
hold, balances you cannot independently check — Scientific Finance insists that everything be
**observed on-chain against your own instrument.** Ordinals are the purest specimen of this: a sat's
identity and an inscription's existence are *deterministic facts* you can reproduce from the ledger
with no third party. This is [cypherpunk2048](https://github.com/cypherpunk2048) made literal —
*mathematics replaces authority, verification replaces trust* — and it is what makes a decentralized
system actually **accurate**.

The BANKON stack is the laboratory: your own node (the ground truth), `bankon-vault` (keys you can
prove only you hold, sign-don't-export), and `bankon-ord` (specimens isolated, artifacts verified).
Accuracy is not a feature here — it is the whole science.

### The program — rebuild the primitives *from accuracy*
Every headline crypto primitive was built trust-first and broke trust-first. Scientific Finance
rebuilds each one **accuracy-first** — grounded in a fact you can reproduce from the chain:

| Primitive | Trust-first (fragile) | **Accuracy-first (SciFi)** |
|---|---|---|
| **Oracles** | believe a signed feed | measurements committed on-chain, verified against your own node; the datum *is* the proof |
| **NFTs** | a token pointing at a URL that can rot | **ordinals/inscriptions** — the artifact lives *on* the sat, immutable, no off-chain dependency |
| **Ordinals** | trust an indexer's answer | replay the ledger yourself (`ord` on your node) — identity is a deterministic fact (Laws I–III) |
| **Bridges** | trust a custodian/multisig you can't see | prove reserves and transfers by on-chain verification, sign-don't-export, no key ever leaves the vault |

The through-line: **remove the party you have to believe.** Oracles become measurements, NFTs become
inscribed matter, ordinals become reproducible observation, bridges become verifiable custody. That is
Scientific Finance — DeFi's ambitions, delivered with a scientist's insistence on reproducibility.

## The Laws

**Law I — Ordination.** Every satoshi is assigned an integer the instant it is mined, in the order it
is created. There are ~2.1×10¹⁵ of them; the numbering is total and immutable. A sat's ordinal *is*
its name.

**Law II — Conservation of order (FIFO).** Satoshis flow through a transaction **first-in, first-out**:
the first sat of the first input becomes the first sat of the first output, and so on. Identity is
conserved across every transfer — the science is possible *because* Bitcoin's own accounting is
deterministic. No oracle, no trusted party: the ledger is the laboratory.

**Law III — Inscription (artifact formation).** Arbitrary content committed to a transaction's
**witness** binds permanently to an individual sat, creating an **immutable, on-chain artifact**. The
sat becomes a carrier; the content becomes a Bitcoin-native object with no off-chain dependency.

**Law IV — Non-awareness of the substrate.** Bitcoin Core cannot see ordinals — it counts value, not
identity. This is the science's central hazard: a naïve spend scatters a catalogued sat as ordinary
change and **annihilates** the artifact. Hence the prime directive of any practitioner:
**isolate the specimen** (see `isolation.py`).

## The Taxonomy of Rare Sats
A stellar-classification scheme, keyed to events in Bitcoin's own clock:

| Class | Trigger | Population (approx.) |
|---|---|---|
| **common** | any non-first sat of a block | ~2.1×10¹⁵ (the field) |
| **uncommon** | the **first sat of each block** | ~880,000 |
| **rare** | first sat of each **difficulty adjustment** period | ~430 |
| **epic** | first sat of each **halving epoch** | 3 (so far) |
| **legendary** | first sat of each **cycle** (6 halvings) | 0 (none minted yet) |
| **mythic** | the **first sat of the genesis block** | 1 — the singularity |

The **mythic** sat is the science's origin point — a single specimen, Satoshi's own, sitting at
ordinal 0. Everything else is measured outward from it.

## The Instruments
Observation requires apparatus. The canonical instrument is the **`ord` indexer** — it replays the
entire chain and reconstructs the ordinal → sat → inscription mapping. Explorers (ordinals.com,
Ordiscan) are its observatories; APIs (Hiro, Best-In-Slot) are its data feeds. See
[`TOOLS.md`](TOOLS.md) for the full instrument catalogue.

**`bankon-ord`** is the *sovereign* instrument: your own `ord` (preferring the
[bankonvault/ord](https://github.com/bankonvault/ord) fork) against your own node, on **testnet or
mainnet**, with the isolation guardrails enforced in code — so the practitioner can never accidentally
destroy the specimen under study.

## Lab Protocol (do this in order)
1. **Calibrate on testnet.** `bankon-ord preflight --net testnet` — confirm the instrument (ord),
   the node (Core ≥ 28, `txindex=1`), and the index.
2. **Prepare a clean specimen chamber.** `bankon-ord create-wallet --net testnet --wallet ord-test`
   — a *dedicated ordinal wallet*. Never share it with cardinal (plain-BTC) funds.
3. **Observe.** `inscriptions` / `outputs` / `find` — read without perturbing.
4. **Form an artifact.** `inscribe … --file specimen.png` (dry-run first; `--yes` to commit). The gate
   shows you exactly what will be broadcast and refuses on a mixed or over-funded wallet.
5. **Reproduce on mainnet** only once the testnet run is clean.

> The rarest thing in this science is not a mythic sat. It is an inscription that survives the
> carelessness of its keeper. Isolate the specimen. Observe before you touch. `bankon-ord` is built to
> keep that discipline for you.
