// aggregator.mjs — accumulation planner: quote every venue, rank by effective
// BTC-per-unit price, then greedily split the order across venues until
// targetBtcSats is covered — re-quoting each tranche so slippage stays under
// maxSlipBps. Venues pay the user's BTC address DIRECTLY; DEXY never signs,
// never custodies. The user executes each leg from their own wallet.

import { quoteThorchain } from './venues/thorchain.mjs';
import { quoteMaya } from './venues/maya.mjs';
import { quoteChainflip } from './venues/chainflip.mjs';
import { quoteMetamask } from './venues/metamask.mjs';
import { toThorUnits, thorAssetString } from './lib.mjs';

async function quoteAll(src, spendNative, destBtcAddress, maxSlipBps) {
  const thorAmt = toThorUnits(spendNative, src.decimals);
  const asset = thorAssetString(src);
  const settled = await Promise.allSettled([
    quoteThorchain(asset, thorAmt, destBtcAddress, maxSlipBps),
    quoteMaya(asset, thorAmt, destBtcAddress, maxSlipBps),
    quoteChainflip(src, spendNative, maxSlipBps),
    quoteMetamask(src, spendNative, destBtcAddress, maxSlipBps), // meta-quote oracle
  ]);
  return settled
    .filter(r => r.status === 'fulfilled')
    .map(r => r.value)
    .filter(q => q.slipBps <= maxSlipBps && q.expectedBtcSats > 0n);
}

export { quoteAll };

/**
 * Plan how to accumulate `targetBtcSats` at the user's own address.
 * `maxSpendNative` caps total spend (circuit breaker).
 * Strategy: probe with the full size; if best venue's slippage blows the cap,
 * halve tranche size and distribute across remaining venues.
 */
export async function planAccumulation(order, maxSpendNative) {
  const legs = [];
  let acquired = 0n;
  let spent = 0n;
  let tranche = maxSpendNative; // start optimistic: single-venue fill
  const usedVenues = new Set();
  const MIN_TRANCHE = maxSpendNative / 16n;

  while (acquired < order.targetBtcSats && spent < maxSpendNative && tranche >= MIN_TRANCHE) {
    const budget = maxSpendNative - spent;
    const size = tranche > budget ? budget : tranche;

    const quotes = (
      await quoteAll(order.sourceAsset, size, order.destBtcAddress, order.maxSlipBps)
    ).filter(q => !usedVenues.has(q.venue));

    if (quotes.length === 0) {
      tranche /= 2n; // shrink and retry across all venues again
      usedVenues.clear();
      continue;
    }

    // Best net BTC output wins this tranche. MetaMask quotes are an oracle
    // only (deposit === null, aggregation of the same underlying venues) —
    // they inform pricing but are never selected for execution.
    quotes.sort((a, b) => (b.expectedBtcSats > a.expectedBtcSats ? 1 : -1));
    const mm = quotes.find(q => q.venue === 'metamask');
    const executable = quotes.filter(q => q.venue !== 'metamask');
    if (executable.length === 0) { tranche /= 2n; usedVenues.clear(); continue; }
    const best = executable[0];
    if (mm && mm.expectedBtcSats > (best.expectedBtcSats * 102n) / 100n) {
      console.warn(
        `metamask aggregation beats best direct venue by >2% (provider: ` +
        `${JSON.stringify(mm.raw?.provider)}) — routing table may be stale`);
    }

    // Trim the final leg so we don't overshoot the target by more than ~1%.
    const remaining = order.targetBtcSats - acquired;
    if (best.expectedBtcSats > (remaining * 101n) / 100n) {
      const scaled = (size * remaining) / best.expectedBtcSats;
      const requote = await quoteAll(order.sourceAsset, scaled, order.destBtcAddress, order.maxSlipBps);
      const rq = requote.find(q => q.venue === best.venue);
      if (rq) {
        legs.push({ quote: rq });
        acquired += rq.expectedBtcSats;
        spent += scaled;
        break;
      }
    }

    legs.push({ quote: best });
    acquired += best.expectedBtcSats;
    spent += size;
    usedVenues.add(best.venue); // spread depth: next tranche goes elsewhere first
  }

  return {
    order,
    legs,
    totalExpectedBtcSats: acquired,
    shortfallSats: acquired >= order.targetBtcSats ? 0n : order.targetBtcSats - acquired,
  };
}
