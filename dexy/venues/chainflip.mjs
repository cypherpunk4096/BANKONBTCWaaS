// chainflip.mjs — Chainflip adapter over its plain HTTP quote API (the same
// backend the official SDK calls), so DEXY carries zero extra dependencies.
// Quote-only in Phase 1: a swap requires opening a deposit channel with
// destAddress = the user's BTC address, which is an execution step (Phase 2).

import { getJson } from '../fetch.mjs';

const API = process.env.CHAINFLIP_API || 'https://chainflip-swap.chainflip.io';
const QUOTE_TTL_S = 60;

// Map source asset → Chainflip chain/asset names. Extend as needed.
const CF_ASSETS = {
  'ETH:USDC': { chain: 'Ethereum', asset: 'USDC' },
  'ETH:ETH': { chain: 'Ethereum', asset: 'ETH' },
  'SOL:SOL': { chain: 'Solana', asset: 'SOL' },
};

export async function quoteChainflip(src, fromAmount, maxSlipBps) {
  const key = `${src.chain}:${src.symbol}`.toUpperCase();
  const cf = CF_ASSETS[key];
  if (!cf) throw new Error(`chainflip: unsupported source ${key}`);

  const url = `${API}/v2/quote?` + new URLSearchParams({
    srcChain: cf.chain, srcAsset: cf.asset,
    destChain: 'Bitcoin', destAsset: 'BTC',
    amount: fromAmount.toString(),
  });
  const { data } = await getJson(url, { ttl: 15_000, fixture: 'chainflip-quote' });

  // Undocumented-shape defense: accept array or {quotes}, prefer REGULAR.
  const quotes = Array.isArray(data) ? data : data?.quotes ?? [];
  const q = quotes.find(x => x.type === 'REGULAR') ?? quotes[0];
  if (!q) throw new Error('chainflip: no quote');

  // A lowLiquidityWarning disqualifies the venue for this size.
  const slipBps = q.lowLiquidityWarning
    ? maxSlipBps + 1
    : Math.round((q.recommendedSlippageTolerancePercent ?? 0) * 100);

  return {
    venue: 'chainflip',
    fromAsset: `${src.chain}.${src.symbol}`,
    fromAmount,
    expectedBtcSats: BigInt(q.egressAmount ?? 0),
    slipBps,
    expiresAt: Math.floor(Date.now() / 1000) + QUOTE_TTL_S,
    deposit: null, // channel opened only when the leg is actually executed (Phase 2)
    raw: q,
  };
}

/** Chainflip BTC-side depth in USD — DeFiLlama protocol TVL fallback. */
export async function chainflipBtcDepthUsd() {
  const { data: p, stale } = await getJson('https://api.llama.fi/protocol/chainflip',
    { ttl: 300_000, fixture: 'llama-chainflip' });
  const tvl = p.currentChainTvls?.Chainflip ?? 0;
  return { venue: 'chainflip', btcSideUsd: tvl * 0.4, stale }; // ~BTC share of pooled TVL
}
