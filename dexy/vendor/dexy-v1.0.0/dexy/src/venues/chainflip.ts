// Chainflip adapter — uses @chainflip/sdk. A swap = open a deposit channel
// with destAddress = buyer's BTC address, then fund the channel from treasury.
// Currently the deepest-volume native-BTC venue (~$450M/30d).

import { SwapSDK, Chains, Assets, type Quote as CfQuote } from "@chainflip/sdk/swap";
import { Quote, DepositInstruction, SourceAsset } from "../types.js";

const sdk = new SwapSDK({ network: "mainnet" });

function cfAsset(src: SourceAsset): { chain: (typeof Chains)[keyof typeof Chains]; asset: (typeof Assets)[keyof typeof Assets] } {
  // Map treasury asset -> Chainflip chain/asset enums. Extend as needed.
  const key = `${src.chain}:${src.symbol}`.toUpperCase();
  const map: Record<string, { chain: any; asset: any }> = {
    "ETH:USDC": { chain: Chains.Ethereum, asset: Assets.USDC },
    "ETH:ETH": { chain: Chains.Ethereum, asset: Assets.ETH },
    "SOL:SOL": { chain: Chains.Solana, asset: Assets.SOL },
  };
  if (!map[key]) throw new Error(`chainflip: unsupported source ${key}`);
  return map[key];
}

export async function quoteChainflip(
  src: SourceAsset,
  fromAmount: bigint, // native base units (e.g. USDC 1e6)
  maxSlipBps: number
): Promise<Quote> {
  const { chain, asset } = cfAsset(src);
  const { quotes } = await sdk.getQuoteV2({
    srcChain: chain,
    srcAsset: asset,
    destChain: Chains.Bitcoin,
    destAsset: Assets.BTC,
    amount: fromAmount.toString(),
  });
  const q = quotes.find((x) => x.type === "REGULAR") ?? quotes[0];
  if (!q) throw new Error("chainflip: no quote");

  // SDK recommends a tolerance; a lowLiquidityWarning disqualifies the venue.
  const slipBps = q.lowLiquidityWarning
    ? maxSlipBps + 1
    : Math.round(q.recommendedSlippageTolerancePercent * 100);

  return {
    venue: "chainflip",
    fromAsset: `${src.chain}.${src.symbol}`,
    fromAmount,
    expectedBtcSats: BigInt(q.egressAmount), // BTC egress in sats
    slipBps,
    expiresAt: Math.floor(Date.now() / 1000) + 60,
    deposit: null, // channel opened only when the leg is actually executed
    raw: q,
  };
}

/** Open the deposit channel at execution time; payout goes straight to buyer. */
export async function openChainflipChannel(
  src: SourceAsset,
  fromAmount: bigint,
  buyerBtcAddress: string,
  quoteRaw: unknown
): Promise<DepositInstruction & { channelId: string }> {
  const quote = quoteRaw as CfQuote;
  const resp = await sdk.requestDepositAddressV2({
    quote,
    destAddress: buyerBtcAddress,
    fillOrKillParams: {
      slippageTolerancePercent: quote.recommendedSlippageTolerancePercent,
      refundAddress: process.env.TREASURY_REFUND_ADDRESS!, // refund, never a bad fill
      retryDurationBlocks: 100,
    },
  });
  return {
    sourceChain: src.chain,
    depositAddress: resp.depositAddress,
    amount: fromAmount,
    expiresAt: Math.floor(
      (resp.estimatedDepositChannelExpiryTime ?? Date.now() + 30 * 60_000) / 1000
    ),
    channelId: resp.depositChannelId,
  };
}

export async function chainflipStatus(channelId: string) {
  return sdk.getStatusV2({ id: channelId });
}
