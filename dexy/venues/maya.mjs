// maya.mjs — Maya Protocol adapter. Identical mechanics to THORChain (friendly
// fork): memo-based direct-to-user payout, same inbound-address rotation risk.

import { getJson } from '../fetch.mjs';

const NODE = process.env.MAYANODE_URL || 'https://mayanode.mayachain.info';
const TO_ASSET = 'BTC.BTC';
const QUOTE_TTL_S = 60;

const node = (path, fixture) => getJson(`${NODE}${path}`, { ttl: 15_000, fixture });

export async function quoteMaya(fromAsset, fromAmount, destBtcAddress, maxSlipBps) {
  const { data: q } = await node(
    `/mayachain/quote/swap?from_asset=${encodeURIComponent(fromAsset)}` +
      `&to_asset=${TO_ASSET}&amount=${fromAmount}` +
      `&destination=${destBtcAddress}&tolerance_bps=${maxSlipBps}`,
    'maya-quote');
  if (q.error) throw new Error(`maya quote: ${q.error}`);

  const chain = fromAsset.split('.')[0];
  const { data: inbounds } = await node('/mayachain/inbound_addresses', 'maya-inbound');
  const inbound = inbounds.find(x => x.chain === chain);
  if (!inbound || inbound.halted) throw new Error(`maya: ${chain} unavailable/halted`);

  const expiresAt = Math.floor(Date.now() / 1000) + QUOTE_TTL_S;
  return {
    venue: 'maya',
    fromAsset,
    fromAmount,
    expectedBtcSats: BigInt(q.expected_amount_out),
    slipBps: Number(q.fees?.slippage_bps ?? q.slippage_bps ?? 0),
    expiresAt,
    deposit: {
      sourceChain: chain,
      depositAddress: q.inbound_address ?? inbound.address,
      memo: q.memo,
      amount: fromAmount,
      expiresAt,
    },
    raw: q,
  };
}
