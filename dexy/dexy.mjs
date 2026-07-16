// dexy.mjs — CEX→DEX transfer projector (external market data, clearly labeled).
//
// 1. Snapshot how much actual Bitcoin sits in CEX proof-of-reserve wallets
//    (DeFiLlama /protocol data: chainTvls.Bitcoin = assets on the BTC chain).
// 2. Measure current native-BTC depth on the DEX side (THORNode + Chainflip).
// 3. Project a tranche schedule to move a given BTC amount from CEX custody
//    into self-custody without blowing slippage/absorption limits:
//    CEX withdrawal → YOUR BTC address (your keys, WaaS BTC Standard) →
//    BTC becomes a source asset for LP/swap legs on the DEX venues.
//
// The projection is pure math over live depth — it never moves funds itself.
// Chain-native pairs stay on /api/pairs; everything here carries `source:`.

import { getJson } from './fetch.mjs';
import { thorchainBtcDepthUsd } from './venues/thorchain.mjs';
import { chainflipBtcDepthUsd } from './venues/chainflip.mjs';
import { mapMetamaskLiquidity } from './venues/metamask.mjs';

const LLAMA = 'https://api.llama.fi';

export const CEX_SLUGS = {
  Binance: 'binance-cex',
  OKX: 'okx',
  Bybit: 'bybit',
  Bitfinex: 'bitfinex',
  'Crypto.com': 'crypto-com',
  KuCoin: 'kucoin',
  Bitget: 'bitget',
  Gate: 'gate',
};

/** Actual Bitcoin (BTC-chain assets) per CEX, from proof-of-reserve wallets. */
export async function fetchCexBtcHoldings() {
  const out = [];
  let anyStale = false;
  for (const [cex, slug] of Object.entries(CEX_SLUGS)) {
    try {
      const { data: p, stale } = await getJson(`${LLAMA}/protocol/${slug}`,
        { ttl: 600_000, fixture: `llama-${slug}` });
      anyStale ||= stale;
      const series = p.chainTvls?.Bitcoin?.tvl;
      const latest = Array.isArray(series) ? series.at(-1)?.totalLiquidityUSD : undefined;
      if (latest)
        out.push({ cex, slug, btcChainUsd: latest, url: `https://defillama.com/cex/${slug}` });
    } catch { /* CEX without published BTC wallets (or no fixture offline) — skip */ }
  }
  return { holdings: out.sort((a, b) => b.btcChainUsd - a.btcChainUsd), stale: anyStale };
}

/** Native BTC-side depth currently on the DEX venues. */
export async function fetchDexBtcDepth() {
  const settled = await Promise.allSettled([thorchainBtcDepthUsd(), chainflipBtcDepthUsd()]);
  const depths = settled
    .filter(r => r.status === 'fulfilled' && r.value)
    .map(r => ({ venue: r.value.venue, btcSideUsd: r.value.btcSideUsd, stale: !!r.value.stale }));
  return depths;
}

/**
 * Project moving `moveUsd` of BTC from CEX custody into the DEX venues.
 * absorptionPct: max fraction of a venue's BTC-side depth to add per day
 * (default 10%/day — beyond this you're moving the market, not entering it).
 */
export function projectTransfer(moveUsd, depths, absorptionPct = 0.10, slipBpsPerTranche = 30) {
  const dexDepthUsd = depths.reduce((s, d) => s + d.btcSideUsd, 0);
  if (!(dexDepthUsd > 0)) throw new Error('no DEX depth available to project against');
  const dailyAbsorptionUsd = dexDepthUsd * absorptionPct;
  const trancheUsd = Math.min(dailyAbsorptionUsd / 4, moveUsd); // 4 tranches/day
  const days = Math.max(1, Math.ceil(moveUsd / dailyAbsorptionUsd));
  const estSlipCostUsd = (moveUsd * slipBpsPerTranche) / 10_000;

  const schedule = [];
  let remaining = moveUsd;
  for (let day = 1; day <= days && remaining > 0; day++) {
    for (const d of depths) {
      if (remaining <= 0) break;
      const venueShare = d.btcSideUsd / dexDepthUsd;
      const usd = Math.min(remaining, dailyAbsorptionUsd * venueShare);
      schedule.push({ day, venue: d.venue, usd: Math.round(usd) });
      remaining -= usd;
    }
  }

  const notes = [
    `DEX BTC-side depth is $${(dexDepthUsd / 1e6).toFixed(1)}M — the DEX side can absorb only a sliver of CEX custody per day.`,
    'Withdrawal path: CEX withdrawal → YOUR BTC address (your keys — verify with /api/dexy/custody/verify) → deposit as LP or swap-source on venues.',
    'Each tranche should be executed as separate UTXOs; respect THORChain BTC dust minimum (~10k sats) and outbound fee.',
  ];
  if (moveUsd > dexDepthUsd)
    notes.unshift(
      `REQUESTED TRANSFER EXCEEDS TOTAL DEX DEPTH (${(moveUsd / dexDepthUsd).toFixed(1)}x) — projection spans ${days} days at ${absorptionPct * 100}%/day absorption.`
    );

  return { totalBtcUsd: moveUsd, dexDepthUsd, dailyAbsorptionUsd, trancheUsd, tranchesPerDay: 4, days, estSlipCostUsd, schedule, notes };
}

/** One-call report: CEX holdings + DEX depth + projection + MetaMask pair map. */
export async function dexyReport(moveUsd, absorptionPct, slipBps) {
  const [cex, depths, metamaskPairs] = await Promise.all([
    fetchCexBtcHoldings(),
    fetchDexBtcDepth(),
    mapMetamaskLiquidity().catch(() => []),
  ]);
  return {
    source: 'defillama proof-of-reserve + thornode + chainflip (external market data)',
    holdings: cex.holdings,
    depths,
    metamaskPairs,
    projection: projectTransfer(moveUsd, depths, absorptionPct, slipBps),
    stale: cex.stale || depths.some(d => d.stale),
  };
}
