// Funds receipt — "confirmed sale" means FUNDS RECEIVED, not a signed receipt.
// The x402 payment settles as an Algorand transaction; we poll the Algorand
// indexer until the payment to the treasury address is FINAL, then release.
//
// x402 receipt object = claim. Indexer confirmation = truth. We gate on truth.

import { SaleOrder } from "./types.js";

const INDEXER = process.env.ALGO_INDEXER_URL ?? "https://mainnet-idx.algonode.cloud";
const TREASURY_ALGO_ADDRESS = process.env.TREASURY_ALGO_ADDRESS!;
const MIN_ROUNDS = Number(process.env.ALGO_MIN_CONFIRMATIONS ?? 1); // Algorand has instant finality; >1 = paranoia margin

export interface ExpectedPayment {
  txId?: string;          // if the x402 receipt names the txn, verify it directly
  assetId: number;        // 0 = ALGO, else ASA id (e.g. USDC ASA 31566704)
  minAmount: bigint;      // base units owed for this order
  noteOrderId: string;    // orderId expected in the txn note field
}

async function idx(path: string): Promise<any> {
  const r = await fetch(`${INDEXER}${path}`);
  if (!r.ok) throw new Error(`indexer ${r.status}: ${path}`);
  return r.json();
}

function txnMatches(t: any, exp: ExpectedPayment): boolean {
  const isAsa = exp.assetId !== 0;
  const pay = isAsa ? t["asset-transfer-transaction"] : t["payment-transaction"];
  if (!pay) return false;
  if (pay.receiver !== TREASURY_ALGO_ADDRESS) return false;
  if (isAsa && t["asset-transfer-transaction"]["asset-id"] !== exp.assetId) return false;
  if (BigInt(pay.amount) < exp.minAmount) return false;
  const note = t.note ? Buffer.from(t.note, "base64").toString("utf8") : "";
  return note.includes(exp.noteOrderId);
}

/**
 * Resolve true once funds are confirmed received. Throws on timeout.
 * Poll interval 5s; Algorand rounds are ~2.8s so this is near-real-time.
 */
export async function awaitFundsReceipt(
  order: SaleOrder,
  expected: ExpectedPayment,
  timeoutMs = 15 * 60_000
): Promise<{ txId: string; confirmedRound: number }> {
  const deadline = Date.now() + timeoutMs;

  while (Date.now() < deadline) {
    const status = await idx("/health").catch(() => null);
    const currentRound = status?.round ?? Infinity;

    let txns: any[] = [];
    if (expected.txId) {
      const r = await idx(`/v2/transactions/${expected.txId}`).catch(() => null);
      if (r?.transaction) txns = [r.transaction];
    } else {
      const q =
        `/v2/accounts/${TREASURY_ALGO_ADDRESS}/transactions?limit=50` +
        (expected.assetId ? `&asset-id=${expected.assetId}` : "&tx-type=pay");
      txns = (await idx(q)).transactions ?? [];
    }

    const hit = txns.find(
      (t) =>
        txnMatches(t, expected) &&
        t["confirmed-round"] &&
        currentRound - t["confirmed-round"] >= MIN_ROUNDS - 1
    );
    if (hit) return { txId: hit.id, confirmedRound: hit["confirmed-round"] };

    await new Promise((r) => setTimeout(r, 5_000));
  }
  throw new Error(`order ${order.orderId}: funds not received within timeout — sale NOT confirmed`);
}
