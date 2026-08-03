// THORChain adapter — quotes via THORNode, payout goes DIRECTLY to buyer's BTC
// address via the swap memo, so this service never custodies BTC.
//
// SAFETY: THORChain suffered a $10M vault-churn address-poisoning exploit
// (May 2026). ALWAYS pull inbound_addresses fresh from >=2 independent
// THORNode endpoints and cross-check before sending funds. Never cache.

import { Quote, DepositInstruction } from "../types.js";

const NODES = [
  "https://thornode.ninerealms.com",
  "https://thornode.thorchain.liquify.com",
];

const TO_ASSET = "BTC.BTC";
const QUOTE_TTL_S = 60; // treat quotes/inbound addresses as stale after this

async function getJson(path: string, base = NODES[0]): Promise<any> {
  const r = await fetch(`${base}${path}`);
  if (!r.ok) throw new Error(`thornode ${r.status}: ${path}`);
  return r.json();
}

/** Cross-check inbound address from two independent nodes. Refuse on mismatch. */
async function verifiedInbound(chain: string) {
  const [a, b] = await Promise.all(NODES.map((n) => getJson("/thorchain/inbound_addresses", n)));
  const ia = a.find((x: any) => x.chain === chain);
  const ib = b.find((x: any) => x.chain === chain);
  if (!ia || !ib) throw new Error(`thorchain: no inbound for ${chain}`);
  if (ia.halted || ia.global_trading_paused || ia.chain_trading_paused)
    throw new Error(`thorchain: ${chain} trading halted`);
  if (ia.address !== ib.address || (ia.router ?? "") !== (ib.router ?? ""))
    throw new Error("thorchain: inbound address mismatch across nodes — possible poisoning, ABORT");
  return ia;
}

export async function quoteThorchain(
  fromAsset: string,        // e.g. "ETH.USDC-0XA0B86991C6218B36C1D19D4A2E9EB0CE3606EB48"
  fromAmount: bigint,       // in THORChain 1e8 base units
  buyerBtcAddress: string,
  maxSlipBps: number
): Promise<Quote> {
  const q = await getJson(
    `/thorchain/quote/swap?from_asset=${encodeURIComponent(fromAsset)}` +
      `&to_asset=${TO_ASSET}&amount=${fromAmount}` +
      `&destination=${buyerBtcAddress}&tolerance_bps=${maxSlipBps}`
  );
  if (q.error) throw new Error(`thorchain quote: ${q.error}`);

  const chain = fromAsset.split(".")[0];
  const inbound = await verifiedInbound(chain);
  const expiresAt = Math.floor(Date.now() / 1000) + QUOTE_TTL_S;

  const deposit: DepositInstruction = {
    sourceChain: chain,
    depositAddress: q.inbound_address ?? inbound.address,
    memo: q.memo, // e.g. "=:BTC.BTC:<buyer>:<limit>"  — limit enforces min output, else refund
    amount: fromAmount,
    expiresAt,
  };

  return {
    venue: "thorchain",
    fromAsset,
    fromAmount,
    expectedBtcSats: BigInt(q.expected_amount_out), // 1e8 = sats for BTC
    slipBps: Number(q.fees?.slippage_bps ?? q.slippage_bps ?? 0),
    expiresAt,
    deposit,
    raw: q,
  };
}

/** Poll swap status by inbound tx hash. */
export async function thorchainStatus(inboundTxHash: string): Promise<"pending" | "confirmed" | "refunded"> {
  const s = await getJson(`/thorchain/tx/status/${inboundTxHash}`);
  const out = s?.out_txs?.[0];
  if (!out) return "pending";
  return out.memo?.startsWith("REFUND") ? "refunded" : "confirmed";
}
