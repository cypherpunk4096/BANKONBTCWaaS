// Core types — all BTC amounts in satoshis (bigint), source amounts in base units.

export type VenueId = "thorchain" | "maya" | "chainflip" | "metamask";

export interface Quote {
  venue: VenueId;
  fromAsset: string;          // venue notation, e.g. "ETH.USDC-0X..." or "Usdc"
  fromAmount: bigint;         // base units of source asset
  expectedBtcSats: bigint;    // net of all venue fees (outbound fee included)
  slipBps: number;
  expiresAt: number;          // unix seconds
  // Everything needed to actually execute this leg:
  deposit: DepositInstruction | null; // null until finalized (chainflip needs a channel)
  raw: unknown;
}

export interface DepositInstruction {
  sourceChain: string;        // "ETH", "AVAX", "BTC", ...
  depositAddress: string;     // venue inbound / deposit-channel address
  memo?: string;              // THORChain/Maya memo; unused for Chainflip
  amount: bigint;             // base units to send
  expiresAt: number;          // do NOT send after this (inbound addrs rotate!)
}

export interface SaleOrder {
  orderId: string;
  buyerBtcAddress: string;    // final destination — venues pay buyer directly
  targetBtcSats: bigint;      // BTC owed to buyer
  sourceAsset: SourceAsset;   // what the treasury holds and will spend
  maxSlipBps: number;         // per-leg slippage ceiling
  x402Receipt: unknown;       // proof of confirmed payment (Algorand x402)
}

export interface SourceAsset {
  chain: string;              // "ETH"
  symbol: string;             // "USDC"
  contract?: string;          // ERC20 address if applicable
  decimals: number;
}

export interface PlanLeg {
  quote: Quote;
}

export interface AccumulationPlan {
  order: SaleOrder;
  legs: PlanLeg[];
  totalExpectedBtcSats: bigint;
  shortfallSats: bigint;      // >0 if venues can't cover target within maxSlipBps
}

// Inject your own signer for the source chain (treasury wallet).
export interface SourceChainSigner {
  send(instr: DepositInstruction): Promise<{ txHash: string }>;
}

export interface LegResult {
  venue: VenueId;
  inboundTxHash: string;
  status: "pending" | "confirmed" | "refunded" | "failed";
}
