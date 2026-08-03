// Accumulation planner: quote every venue, rank by effective BTC-per-unit
// price, then greedily split the order across venues until targetBtcSats is
// covered — re-quoting each tranche so slippage stays under maxSlipBps.

import { quoteThorchain } from "./venues/thorchain.js";
import { quoteMaya } from "./venues/maya.js";
import { quoteChainflip } from "./venues/chainflip.js";
import { quoteMetamask } from "./venues/metamask.js";
import { AccumulationPlan, Quote, SaleOrder, SourceAsset } from "./types.js";

// THORChain/Maya use 1e8 fixed-point for all assets; convert from native decimals.
function toThorUnits(amount: bigint, decimals: number): bigint {
  return decimals === 8 ? amount : (amount * 10n ** 8n) / 10n ** BigInt(decimals);
}

function thorAssetString(src: SourceAsset): string {
  return src.contract
    ? `${src.chain}.${src.symbol}-${src.contract.toUpperCase()}`
    : `${src.chain}.${src.symbol}`;
}

async function quoteAll(
  src: SourceAsset,
  spendNative: bigint,
  buyer: string,
  maxSlipBps: number
): Promise<Quote[]> {
  const thorAmt = toThorUnits(spendNative, src.decimals);
  const asset = thorAssetString(src);
  const settled = await Promise.allSettled([
    quoteThorchain(asset, thorAmt, buyer, maxSlipBps),
    quoteMaya(asset, thorAmt, buyer, maxSlipBps),
    quoteChainflip(src, spendNative, maxSlipBps),
    quoteMetamask(src, spendNative, buyer, maxSlipBps), // meta-quote oracle
  ]);
  return settled
    .filter((r): r is PromiseFulfilledResult<Quote> => r.status === "fulfilled")
    .map((r) => r.value)
    .filter((q) => q.slipBps <= maxSlipBps && q.expectedBtcSats > 0n);
}

/**
 * Plan how to accumulate `targetBtcSats` for the buyer.
 * `maxSpendNative` caps total treasury spend (circuit breaker).
 * Strategy: probe with the full size; if best venue's slippage blows the cap,
 * halve tranche size and distribute across remaining venues.
 */
export async function planAccumulation(
  order: SaleOrder,
  maxSpendNative: bigint
): Promise<AccumulationPlan> {
  const legs: AccumulationPlan["legs"] = [];
  let acquired = 0n;
  let spent = 0n;
  let tranche = maxSpendNative; // start optimistic: single-venue fill
  const usedVenues = new Set<string>();
  const MIN_TRANCHE = maxSpendNative / 16n;

  while (acquired < order.targetBtcSats && spent < maxSpendNative && tranche >= MIN_TRANCHE) {
    const budget = maxSpendNative - spent;
    const size = tranche > budget ? budget : tranche;

    const quotes = (
      await quoteAll(order.sourceAsset, size, order.buyerBtcAddress, order.maxSlipBps)
    ).filter((q) => !usedVenues.has(q.venue));

    if (quotes.length === 0) {
      tranche /= 2n; // shrink and retry across all venues again
      usedVenues.clear();
      continue;
    }

    // Best net BTC output wins this tranche. MetaMask quotes are an oracle
    // only (deposit === null, aggregation of the same underlying venues) —
    // they inform pricing but are never selected for execution.
    quotes.sort((a, b) => (b.expectedBtcSats > a.expectedBtcSats ? 1 : -1));
    const mm = quotes.find((q) => q.venue === "metamask");
    const executable = quotes.filter((q) => q.venue !== "metamask");
    if (executable.length === 0) { tranche /= 2n; usedVenues.clear(); continue; }
    const best = executable[0];
    if (mm && mm.expectedBtcSats > (best.expectedBtcSats * 102n) / 100n) {
      console.warn(
        `metamask aggregation beats best direct venue by >2% (provider: ` +
        `${JSON.stringify((mm.raw as any)?.provider)}) — routing table may be stale`
      );
    }

    // Trim the final leg so we don't overshoot the target by more than ~1%.
    const remaining = order.targetBtcSats - acquired;
    if (best.expectedBtcSats > (remaining * 101n) / 100n) {
      const scaled = (size * remaining) / best.expectedBtcSats;
      const requote = await quoteAll(order.sourceAsset, scaled, order.buyerBtcAddress, order.maxSlipBps);
      const rq = requote.find((q) => q.venue === best.venue);
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
