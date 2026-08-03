// Settlement: fires ONLY on a verified x402 (Algorand) payment receipt.
// Every leg's destination is the buyer's BTC address — the venues themselves
// deliver the BTC, so this service holds source-asset treasury only, never BTC.

import { planAccumulation } from "./aggregator.js";
import { openChainflipChannel } from "./venues/chainflip.js";
import { thorchainStatus } from "./venues/thorchain.js";
import { awaitFundsReceipt, ExpectedPayment } from "./receipt.js";
import { LegResult, SaleOrder, SourceChainSigner } from "./types.js";

export async function settleConfirmedSale(
  order: SaleOrder,
  expected: ExpectedPayment,
  signer: SourceChainSigner,
  maxSpendNative: bigint
): Promise<LegResult[]> {
  // 1. "Confirmed sale" = RECEIPT OF FUNDS. We block here until the Algorand
  //    indexer shows the payment confirmed in the treasury account. The x402
  //    receipt object may name the txId, but on-chain state is the authority.
  const receipt = await awaitFundsReceipt(order, expected);
  console.log(
    `[${order.orderId}] funds received: algo tx ${receipt.txId} @ round ${receipt.confirmedRound}`
  );

  // 2. Plan across venues.
  const plan = await planAccumulation(order, maxSpendNative);
  if (plan.shortfallSats > 0n) {
    throw new Error(
      `order ${order.orderId}: venues can only source ${plan.totalExpectedBtcSats} of ` +
        `${order.targetBtcSats} sats within slippage cap — manual review required`
    );
  }

  // 3. Execute legs sequentially (simpler failure handling; parallelize later).
  const results: LegResult[] = [];
  for (const leg of plan.legs) {
    const q = leg.quote;

    // Chainflip deposit channels are opened at execution time, not quote time.
    const deposit =
      q.venue === "chainflip"
        ? await openChainflipChannel(order.sourceAsset, q.fromAmount, order.buyerBtcAddress, q.raw)
        : q.deposit!;

    // Refuse stale instructions — vault addresses rotate.
    if (Math.floor(Date.now() / 1000) >= deposit.expiresAt) {
      throw new Error(`${q.venue}: deposit instruction expired, re-plan required`);
    }

    const { txHash } = await signer.send(deposit);
    console.log(`[${order.orderId}] ${q.venue} leg funded: ${txHash} (memo: ${deposit.memo ?? "n/a"})`);
    results.push({ venue: q.venue, inboundTxHash: txHash, status: "pending" });
  }
  return results;
}

/** Poll leg status until terminal. Alert on refunds (slippage limit hit). */
export async function monitorLegs(results: LegResult[], intervalMs = 30_000): Promise<void> {
  const pending = new Set(results.filter((r) => r.status === "pending"));
  while (pending.size > 0) {
    for (const leg of pending) {
      try {
        if (leg.venue === "thorchain" || leg.venue === "maya") {
          leg.status = await thorchainStatus(leg.inboundTxHash);
        }
        // chainflip: track via chainflipStatus(channelId) — wire channel id through LegResult
        if (leg.status !== "pending") pending.delete(leg);
        if (leg.status === "refunded") {
          console.error(`REFUND on ${leg.venue} (${leg.inboundTxHash}) — buyer is short, re-run leg`);
        }
      } catch (e) {
        console.warn(`status poll failed for ${leg.venue}: ${e}`);
      }
    }
    if (pending.size > 0) await new Promise((r) => setTimeout(r, intervalMs));
  }
}
