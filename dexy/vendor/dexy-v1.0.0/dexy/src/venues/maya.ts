// Maya Protocol adapter — identical mechanics to THORChain (friendly fork).
// Same memo-based direct-to-buyer payout, same inbound-address rotation risk.

import { Quote, DepositInstruction } from "../types.js";

const NODE = "https://mayanode.mayachain.info";
const TO_ASSET = "BTC.BTC";
const QUOTE_TTL_S = 60;

async function getJson(path: string): Promise<any> {
  const r = await fetch(`${NODE}${path}`);
  if (!r.ok) throw new Error(`mayanode ${r.status}: ${path}`);
  return r.json();
}

export async function quoteMaya(
  fromAsset: string,       // e.g. "ETH.USDC-0X..." (1e8 base units, like THORChain)
  fromAmount: bigint,
  buyerBtcAddress: string,
  maxSlipBps: number
): Promise<Quote> {
  const q = await getJson(
    `/mayachain/quote/swap?from_asset=${encodeURIComponent(fromAsset)}` +
      `&to_asset=${TO_ASSET}&amount=${fromAmount}` +
      `&destination=${buyerBtcAddress}&tolerance_bps=${maxSlipBps}`
  );
  if (q.error) throw new Error(`maya quote: ${q.error}`);

  const chain = fromAsset.split(".")[0];
  const inbound = (await getJson("/mayachain/inbound_addresses")).find(
    (x: any) => x.chain === chain
  );
  if (!inbound || inbound.halted) throw new Error(`maya: ${chain} unavailable/halted`);

  const expiresAt = Math.floor(Date.now() / 1000) + QUOTE_TTL_S;
  const deposit: DepositInstruction = {
    sourceChain: chain,
    depositAddress: q.inbound_address ?? inbound.address,
    memo: q.memo,
    amount: fromAmount,
    expiresAt,
  };

  return {
    venue: "maya",
    fromAsset,
    fromAmount,
    expectedBtcSats: BigInt(q.expected_amount_out),
    slipBps: Number(q.fees?.slippage_bps ?? q.slippage_bps ?? 0),
    expiresAt,
    deposit,
    raw: q,
  };
}
