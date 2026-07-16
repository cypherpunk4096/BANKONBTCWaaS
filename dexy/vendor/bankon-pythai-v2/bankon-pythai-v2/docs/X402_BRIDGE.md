# x402 / Algorand Bridge — Trust Model

`src/gateway/X402AlgorandGateway.sol` is a bespoke bridge, not a LayerZero extension.
Algorand is not a LayerZero-supported chain, so BKPY cannot be a native OFT peer there.

## Two legs

**EVM → Algorand (`lockForAlgorand`)**
1. User calls `lockForAlgorand(algorandRecipient, amount)`, locking BKPY in the gateway
   contract and emitting `LockedForAlgorand`.
2. An off-chain facilitator (PARSEC's `parsec-wallet`, using GoPlausible's `@x402-avm`)
   observes the event and credits the equivalent value on Algorand, typically as part of an
   atomic transaction group settling an x402 HTTP-402 payment (e.g. a mindX API call or a
   rage.pythai.net content unlock).

**Algorand → EVM (`creditFromAlgorandSettlement`)**
1. A DAIO-appointed attestor set observes and confirms an Algorand-side x402 settlement
   (e.g. an ASA/USDC payment routed through the x402-avm facilitator).
2. Once `attestorThreshold` confirmations are reached, the DAIO governance contract (as
   `owner`) calls `creditFromAlgorandSettlement`, releasing locked BKPY (or, in a future
   revision, minting fresh BKPY under a DAIO-approved cap) to the EVM recipient.
3. `_algorandTxId` is marked processed to prevent replay.

## Why not a single admin key

`creditFromAlgorandSettlement` is `onlyOwner`, and per the deployment order in
`docs/INTEGRATION.md`, `owner` is the DAIO governance contract after handoff — so crediting
requires a DAIO governance action, not a single signer. The `attestorThreshold` mechanism
additionally requires a minimum number of independent attestors to have confirmed the
Algorand-side event before the DAIO action is even eligible to execute correctly.

## Production hardening (not implemented here — scope for your own audit)

- Replace the `_confirmedAttestations` counter parameter with real on-chain threshold
  signature verification (e.g. EIP-712 signed attestations aggregated and checked in
  `creditFromAlgorandSettlement`, rather than trusted as an argument).
- Consider a rate limit / per-epoch cap on `creditFromAlgorandSettlement` to bound the
  blast radius of a compromised attestor quorum.
- Have this contract, and the attestor key management process, independently audited
  before mainnet use — it is the one component in this package that is NOT a standard,
  previously-audited LayerZero contract.
