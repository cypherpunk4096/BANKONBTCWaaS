# Accept Bitcoin + atomic swap (Bitcoin leg) — incremental release

Answers the questions this release is built around, then documents the additive module.

## Does Bitcoin Core show a price? No.

Core has **no price, fiat, USD, or market RPC** — verified: `bitcoin-cli help` has nothing
price-related. Core validates the chain and manages wallets; the only "rate" it knows is the
**fee rate** (`estimatesmartfee`, sat/vB) — the cost of blockspace, not the price of an asset.
BANKON keeps this honest: the ₿TC.oracle and ⟲ SPINTRADE are **chain-native** (fees, blocktime,
issuance) — no external feed, no fiat.

## Where does Bitcoin "proper" exchange? Atomic swaps, not an EVM DEX.

Native Bitcoin (L1) has no on-chain order-book DEX. It trades trustlessly via **cross-chain atomic
swaps**: a **Hash Time-Locked Contract (HTLC)** on each chain sharing **one secret preimage**.
Claiming the counter-asset reveals the preimage; that same preimage claims the BTC. If either side
stalls, each party **refunds after a timelock**. No custodian, no bridge.

- **BTC** = the native asset, swapped trustlessly (this module).
- **wBTC** = the **custodial wrapped** form (an ERC-20 minted 1:1 against custodied BTC) that trades
  on EVM **DEXs**. Different trust model: wBTC is an IOU on another chain; BTC is BTC.
- **CEX buy → DEX sell, when price is met** is an arbitrage flow *between* those venues; the atomic
  swap is the trustless settlement primitive for the BTC side of any such trade.

## How Bitcoin Core does atomic swaps — and the BANKON equivalent

Core supplies every primitive: PSBTs (`createpsbt`, `descriptorprocesspsbt`, `finalizepsbt`),
custom scripts (`decodescript`, descriptors), the timelock opcodes **`OP_CHECKLOCKTIMEVERIFY`**
(active) and **`OP_CHECKSEQUENCEVERIFY`**, and hashlocks (`OP_SHA256`/`OP_EQUAL`). The HTLC:

```
OP_SHA256 <sha256(preimage)> OP_EQUAL
OP_IF   <claimPubKey>                              # claim path — knows the preimage
OP_ELSE <locktime> OP_CHECKLOCKTIMEVERIFY OP_DROP <refundPubKey>   # refund after timeout
OP_ENDIF OP_CHECKSIG
```

`bankon-waas/swap.mjs` builds exactly this from Core primitives — **non-custodial** (the server
never holds a key; claim/refund are signed client-side, PSBT), consistent with every other BANKON
invariant. The P2WSH funding address is derived by Core's own `decodescript` (authoritative).

### Endpoints (additive; mounted in `server.mjs`)
- `POST /api/swap/htlc/new` `{claimPubkey, refundPubkey, hashHex|preimageHex, locktime}` →
  `{witnessScript, address, descriptor, hashHex, locktime, watching}`. Imports the address
  **watch-only** (timestamp "now", no rescan) so the node can **accept Bitcoin** into it.
- `GET /api/swap/htlc/funding?address=…` → cheap `listunspent` on the watch-only wallet:
  `{funded, amountBtc, utxos:[{txid,vout,confirmations}], tipHeight}`. This is the **accept-Bitcoin**
  check — no heavy `scantxoutset`.
- `POST /api/swap/htlc/preimage` `{preimageHex}` → `{hashHex}` (verify a secret against an HTLC).

**Scope honesty:** this is the trustless **BTC leg**. The counter-asset leg (the other chain, or a
CEX buy / DEX sell once price is met) is the counterparty's half — BANKON builds, funds-detects, and
helps claim/refund the Bitcoin side.

## Accept Bitcoin, including receive from QR

- `GET /api/wallet/:name/receive` → fresh address.
- `GET /api/wallet/:name/payment-request?amount=&label=&message=` → **BIP21** `bitcoin:` URI.
- `GET /api/wallet/:name/qr?…&format=svg|txt|uri` → **scannable QR** of the BIP21 URI, rendered by
  system `qrencode` (SVG default; `txt` = ANSI for terminals; `uri` = the string). Any wallet scans
  the SVG to pay. Combined with the VERIFIED flow (`/api/wallet/:name/verify?txid=`), a received
  payment is confirmed by your own node and recorded to the ICE `.history` for minting/proof.

Requirement: `qrencode` (`apt install qrencode`); the endpoint degrades with a clear message if absent.
