# The Immutable Token Logo Registry — Blueprint

## A template for logo truth that the blockchain itself can prove

### Thesis

A token's logo is its face, and today that face is hearsay. Every wallet, exchange, and explorer renders whatever image its chosen cache last ingested, and none of them can prove the image is right. This blueprint specifies a registry in which a logo's authenticity is not asserted by any company, repository, or API, but is verifiable from chain state alone — by anyone, forever, with nothing but an RPC endpoint and a hash function. The PYTHAI Logo Registry is the reference implementation; the pattern itself is general, and this document is written so that the design can be audited, reproduced, and trusted independently of its authors.

### The three commitments

The registry's definition of truth is the intersection of three commitments made by three independent parties, each recorded immutably.

The first is the submitter's commitment: a keccak256 hash of the exact image bytes, written on-chain in the submission transaction alongside the paid fee. From this moment the image cannot be silently substituted — any bytes that fail to hash to the commitment are, by definition, not the submitted logo.

The second is the agent's commitment: the mindX validator assesses the submission, pins the verified bytes permanently to Arweave together with a signed assessment manifest, and writes the permanent CID on-chain through assess(). The contract refuses an empty CID, and no path to canonical status exists that bypasses this step, so permanent storage is a precondition enforced by the EVM rather than a promise kept by an operator. Arweave's endowment model means the bytes outlive every participant: the logo remains retrievable even if the registry's authors, servers, and domains all vanish.

The third is the collective's commitment: the DAIO, voting Fides-weighted through the BONAFIDE suite, ratifies or rejects every assessed graphic — first submissions and evolutions alike — with Senatus executing the verdict through the RatificationAdapter, which records the proposal id, the Arweave permalink of the manifest the voters actually saw, and a digest of the tally. No logo becomes canonical on any single party's word, including the agent's own.

### The verification procedure

Anyone can prove a wallet is showing the true logo without trusting the registry's operators, its website, or any intermediary. The procedure requires four reads and one hash.

First, call canonicalLogo(chainId, token) on the registry contract and confirm verified is true, noting the returned logoHash, logoCID, and revision. Second, fetch the bytes at the Arweave CID through any gateway — or run your own, since Arweave data is content-addressed and gateway-independent. Third, compute keccak256 over the fetched bytes and confirm it equals the on-chain logoHash; this single comparison binds the permanent storage to the chain commitment. Fourth, if provenance beyond the current state is wanted, walk the event log: LogoSubmitted carries the original commitment and fee payment, LogoAssessed carries the agent's pinning, Ratified on the adapter carries the governance verdict with its proposal reference, and every ERC-4906 MetadataUpdate marks a state transition. The complete biography of the logo — who committed it, who assessed it, who accepted it, what it cost, and every revision it has passed through — is reconstructible from public logs with no off-chain database consulted.

A wallet integrating at this trust level renders only images whose fetched bytes hash to the on-chain commitment, and falls back to the identicon for anything else. The EIP-747 helper and the revision-pinned CDN paths exist for convenience, but the CDN is verifiable rather than trusted: its content can be checked against the same commitment at any time.

### Immutability boundaries, stated precisely

The registry is immutable where immutability serves truth and governed where flexibility serves survival, and the boundary is explicit in the bytecode. There is no proxy and no upgrade path; the code that verifies today verifies forever. The treasury address, all three fees, the SPINTRADE desk, and the Senatus role are immutable constructor parameters. The image history is append-only: a revision increments a counter and a revoked status is recorded, but no event, hash, or CID is ever erased, and the Arweave bytes cannot be erased by anyone. The single governed mutable is the validator signer, rotatable exclusively by Senatus, because key compromise must be survivable without redeploying the layer — and rotation is itself an immutable, logged event. Nothing else moves. Nobody, including the DAIO, can edit the record; the DAIO can only add verdicts to it.

### The template, abstracted

Any project can instantiate this pattern for any class of off-chain artifact that needs on-chain truth — logos are simply the first and most universal case. The template is: a paid, hash-committed submission that mints a transferable proof-of-registration NFT; an agent assessment step that enforces permanent content-addressed storage as a chain-level precondition; a collective ratification step through a governance executor that cannot be bypassed and cannot itself assess; append-only history with ERC-4906 freshness signaling; and a verification procedure expressible in one sentence — fetch the permanent bytes, hash them, compare with the chain. Deployment follows the cypherpunk2048 standard: Foundry for the full test surface including adversarial listeners and fuzzed keying, mainnet for the single canonical instance, CREATE-predicted addresses for the circular registry-adapter wiring, and no administrative surface left behind.

### Why this ends the hearsay era

Every existing logo source answers "what image does our cache hold?" This registry answers a different question: "what image did the issuer commit, the agent verify, permanent storage preserve, and the collective accept?" — and it answers it with a proof rather than a response body. Once wallets can ask the second question, the first becomes obsolete, and the direction of trust inverts: the caches synchronize to the chain instead of the chain deferring to the caches. The definitive source of truth for a token's face becomes the same place the token itself lives, which is where it always belonged.

---

*Reference implementation: LogoRegistry.sol, RatificationAdapter.sol, SpintradeDesk.sol — PYTHAI, Apache-2.0. Live surfaces: agenticplace.pythai.net · mindx.pythai.net · bankon.pythai.net · rage.pythai.net.*
