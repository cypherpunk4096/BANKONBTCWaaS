# Coinbase transactions & coinbase messages

Every Bitcoin block's **first transaction is the coinbase** — the one the miner creates to pay
itself the block reward (subsidy + fees). It's special: it has no real inputs (it *mints* new coins
rather than spending existing ones), and its single input carries a free-form **data field** of
2–100 bytes instead of pointing at a previous output. That field is the **coinbase message**.

BANKON's txindex **deep-dive** (Indexes tab → Scientific view) decodes it: for any coinbase
transaction it shows "⛏ coinbase message" with the bytes rendered to ASCII, non-printable bytes shown
as `·`. See [indexer.md](indexer.md).

---

## What's in those bytes

The coinbase field mixes **consensus-required binary data** with **optional free text**, which is why
a decode is mostly `·` with readable fragments poking through:

| Content | Required? | What it is |
|---------|-----------|------------|
| **Block height** | ✅ since BIP34 (2013) | the block's own height, encoded as a script number at the very start |
| **Extra nonce** | practical necessity | additional mining search space — the 32-bit header nonce is exhausted fast, so miners vary bytes here too |
| **Pool identifier** | optional (convention) | who mined it — tags like `Mined by …`, `/ViaBTC/`, `/F2Pool/`, `/SlushPool/`, `/AntPool/` |
| **Merged-mining commitments** | optional | hashes for chains mined simultaneously (Namecoin, RSK…) |
| **Arbitrary message** | optional | anything the miner wants to write |

A live example from this node (block 956,854, 93-byte coinbase) decoded to:

```
····     Mined by Secpool    O·E··%····mm····[!>·|MQ·2·a··(·|#J·v?9M.···O·…
```

— the readable **"Mined by Secpool"** is the pool tag; the leading `····` is the BIP34 height; the
rest is extra-nonce / commitment bytes.

## The witness commitment (a related, separate thing)

For SegWit blocks the coinbase transaction also carries a **witness commitment** — but that lives in
one of the coinbase's **outputs** (an `OP_RETURN` beginning `aa21a9ed…`), *not* in the coinbase
message field. It commits to the merkle root of all the block's witness data. In the deep-dive you'll
see it as a `nulldata`/`OP_RETURN` output on the coinbase tx.

## The famous first one

The very first coinbase ever — Satoshi's **genesis block** — contained:

> *The Times 03/Jan/2009 Chancellor on brink of second bailout for banks*

a real newspaper headline: both a timestamp proof (the block couldn't predate that day's paper) and a
pointed comment on the banking system Bitcoin was built to route around. That set the tradition — the
coinbase is the one place in a block where the miner can leave a note to the world.

---

## Coinbase facts worth knowing

- **Maturity:** coinbase outputs can't be spent until **100 confirmations** (the "coinbase maturity"
  rule), so a reorg can't strand coins that were already spent onward.
- **No prevouts, no fee to compute:** because a coinbase creates coins, it has no input value to sum —
  the deep-dive shows `fee: coinbase` rather than a number, and its inputs table flags
  "⛏ coinbase (new coins)".
- **The reward = subsidy + fees:** the coinbase output value equals the block subsidy (halving every
  210,000 blocks) plus the total fees of every other transaction in the block.
- **One per block, always first:** `block.tx[0]` is always the coinbase; every other transaction spends
  existing coins.

---

## See it in BANKON

Indexes tab → **txindex lookup** → pick or paste a block's first transaction → switch **view** to
**Scientific**. The coinbase message, the decoded pool tag, the reward output, and the raw hex are all
shown. Any coinbase is a good example — the newest block's is one click away in the recent-transaction
list.

*See also: [indexer.md](indexer.md) · [bitcoin-core-rpc.md](bitcoin-core-rpc.md).*
