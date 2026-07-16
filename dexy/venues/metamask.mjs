// metamask.mjs — MetaMask is an AGGREGATOR, not a liquidity source: its
// Swaps/Bridge stack routes BTC pairs through ~18 providers (Li.Fi, Socket,
// Relay, Mayan, Squid, Across, ...) that ultimately settle on the same venues
// DEXY integrates directly. Two roles:
//  1. quoteMetamask()        — meta-quote oracle: sanity-check our own routing.
//  2. mapMetamaskLiquidity() — which underlying provider really holds the BTC
//     liquidity behind each pair. Feeds the routing table.
//
// CAVEAT: bridge.api.cx.metamask.io is MetaMask's own bridge-controller
// endpoint, NOT a documented public API — everything here is defensive and
// this venue is QUOTE/MAP-ONLY, never selected for execution.

import { getJson } from '../fetch.mjs';

const BRIDGE_API = process.env.METAMASK_BRIDGE_API || 'https://bridge.api.cx.metamask.io';

// CAIP-2 / bridge-controller chain identifiers. Bitcoin mainnet genesis-hash CAIP.
export const MM_CHAIN_IDS = {
  BTC: 'bip122:000000000019d6689c085ae165831e93',
  ETH: '1',
  SOL: 'solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp',
  BASE: '8453',
  BSC: '56',
};

const ZERO = '0x0000000000000000000000000000000000000000';

const quoteUrl = p => `${BRIDGE_API}/getQuote?` + new URLSearchParams(p).toString();

function bestRoute(resp) {
  const routes = Array.isArray(resp) ? resp : resp?.quotes ?? [];
  routes.sort((a, b) =>
    Number(BigInt(b.quote?.destTokenAmount ?? 0) - BigInt(a.quote?.destTokenAmount ?? 0)));
  return routes;
}

/** Meta-quote: what would MetaMask's aggregation pay for the same leg? */
export async function quoteMetamask(src, fromAmount, destBtcAddress, maxSlipBps) {
  const srcChainId = MM_CHAIN_IDS[src.chain];
  if (!srcChainId) throw new Error(`metamask: unmapped source chain ${src.chain}`);

  const { data } = await getJson(quoteUrl({
    srcChainId,
    destChainId: MM_CHAIN_IDS.BTC,
    srcTokenAddress: src.contract ?? ZERO,
    destTokenAddress: ZERO, // native BTC
    srcTokenAmount: fromAmount.toString(),
    destWalletAddress: destBtcAddress,
    slippage: (maxSlipBps / 100).toString(),
    insufficientBal: 'false',
  }), { ttl: 15_000, fixture: 'metamask-quote' });

  const routes = bestRoute(data);
  if (!routes.length) throw new Error('metamask: no route for pair');
  const best = routes[0];

  return {
    venue: 'metamask',
    fromAsset: `${src.chain}.${src.symbol}`,
    fromAmount,
    expectedBtcSats: BigInt(best.quote?.destTokenAmount ?? 0),
    slipBps: maxSlipBps, // API enforces the tolerance; treat as at-cap
    expiresAt: Math.floor(Date.now() / 1000) + 30,
    deposit: null,       // QUOTE-ONLY: execute on the winning underlying venue instead
    oracleOnly: true,
    raw: { provider: best.quote?.bridgeId ?? best.quote?.bridges?.[0], route: best },
  };
}

const DEFAULT_PROBES = [
  { src: { chain: 'ETH', symbol: 'ETH', decimals: 18 }, amount: 10n ** 18n },              // 1 ETH
  { src: { chain: 'ETH', symbol: 'USDC', contract: '0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48', decimals: 6 }, amount: 5_000n * 10n ** 6n },
  { src: { chain: 'SOL', symbol: 'SOL', decimals: 9 }, amount: 25n * 10n ** 9n },          // 25 SOL
  { src: { chain: 'BASE', symbol: 'USDC', contract: '0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913', decimals: 6 }, amount: 5_000n * 10n ** 6n },
];

/** For each BTC source pair: which underlying provider wins at a probe size. */
export async function mapMetamaskLiquidity(
  probes = DEFAULT_PROBES,
  probeBtcDest = process.env.PROBE_BTC_ADDRESS || 'bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh'
) {
  const out = [];
  for (const { src, amount } of probes) {
    const pair = `${src.chain}.${src.symbol} -> BTC.BTC`;
    try {
      const { data } = await getJson(quoteUrl({
        srcChainId: MM_CHAIN_IDS[src.chain],
        destChainId: MM_CHAIN_IDS.BTC,
        srcTokenAddress: src.contract ?? ZERO,
        destTokenAddress: ZERO,
        srcTokenAmount: amount.toString(),
        destWalletAddress: probeBtcDest,
        slippage: '1',
        insufficientBal: 'true', // probe only — no balance check
      }), { ttl: 60_000, fixture: 'metamask-map' });
      const routes = bestRoute(data);
      if (!routes.length) continue;
      const best = routes[0].quote ?? {};
      const provider = [best.bridgeId, ...(best.bridges ?? []), ...(best.steps?.map(s => s.protocol?.name) ?? [])]
        .filter(Boolean)
        .join('(') + (best.steps?.length ? ')' : '');
      out.push({
        pair, probeAmount: amount.toString(),
        bestProvider: provider || 'unknown',
        destBtcSats: String(best.destTokenAmount ?? '0'),
        routesAvailable: routes.length,
      });
    } catch {
      out.push({ pair, probeAmount: amount.toString(), bestProvider: 'NO ROUTE / API CHANGED', destBtcSats: '0', routesAvailable: 0 });
    }
  }
  return out;
}
