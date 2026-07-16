// thorchain.mjs — THORChain adapter. Payout goes DIRECTLY to the user's BTC
// address via the swap memo, so DEXY never custodies BTC.
//
// SAFETY: THORChain suffered a $10M vault-churn address-poisoning exploit
// (May 2026). ALWAYS pull inbound_addresses fresh from >=2 independent
// THORNode endpoints and cross-check before surfacing a deposit address.

import { getJson } from '../fetch.mjs';

const NODES = [
  process.env.THORNODE_URL || 'https://thornode.ninerealms.com',
  process.env.THORNODE_URL_2 || 'https://thornode.thorchain.liquify.com',
];
const TO_ASSET = 'BTC.BTC';       // native BTC only — wrapped assets are not a destination
const QUOTE_TTL_S = 60;           // quotes/inbound addresses are stale after this

const node = (path, base, fixture) => getJson(`${base}${path}`, { ttl: 15_000, fixture });

/** Cross-check inbound address from two independent nodes. Refuse on mismatch. */
async function verifiedInbound(chain) {
  const [a, b] = await Promise.all(
    NODES.map(n => node('/thorchain/inbound_addresses', n, 'thornode-inbound')));
  const ia = a.data.find(x => x.chain === chain);
  const ib = b.data.find(x => x.chain === chain);
  if (!ia || !ib) throw new Error(`thorchain: no inbound for ${chain}`);
  if (ia.halted || ia.global_trading_paused || ia.chain_trading_paused)
    throw new Error(`thorchain: ${chain} trading halted`);
  if (ia.address !== ib.address || (ia.router ?? '') !== (ib.router ?? ''))
    throw new Error('thorchain: inbound address mismatch across nodes — possible poisoning, ABORT');
  return ia;
}

export async function quoteThorchain(fromAsset, fromAmount, destBtcAddress, maxSlipBps) {
  const { data: q } = await node(
    `/thorchain/quote/swap?from_asset=${encodeURIComponent(fromAsset)}` +
      `&to_asset=${TO_ASSET}&amount=${fromAmount}` +
      `&destination=${destBtcAddress}&tolerance_bps=${maxSlipBps}`,
    NODES[0], 'thor-quote');
  if (q.error) throw new Error(`thorchain quote: ${q.error}`);

  const chain = fromAsset.split('.')[0];
  const inbound = await verifiedInbound(chain);
  const expiresAt = Math.floor(Date.now() / 1000) + QUOTE_TTL_S;

  return {
    venue: 'thorchain',
    fromAsset,
    fromAmount,
    expectedBtcSats: BigInt(q.expected_amount_out),  // 1e8 = sats for BTC
    slipBps: Number(q.fees?.slippage_bps ?? q.slippage_bps ?? 0),
    expiresAt,
    deposit: {
      sourceChain: chain,
      depositAddress: q.inbound_address ?? inbound.address,
      memo: q.memo,  // "=:BTC.BTC:<dest>:<limit>" — limit enforces min output, else refund
      amount: fromAmount,
      expiresAt,
    },
    raw: q,
  };
}

/** Poll swap status by inbound tx hash → pending | confirmed | refunded. */
export async function thorchainStatus(inboundTxHash) {
  const { data: s } = await node(`/thorchain/tx/status/${inboundTxHash}`, NODES[0], 'thor-status');
  const out = s?.out_txs?.[0];
  if (!out) return 'pending';
  return out.memo?.startsWith('REFUND') ? 'refunded' : 'confirmed';
}

/** BTC.BTC pool depth in USD (balance_asset 1e8, priced via asset_tor_price). */
export async function thorchainBtcDepthUsd() {
  const { data: pools, stale } = await node('/thorchain/pools', NODES[0], 'thornode-pools');
  const btc = pools.find(p => p.asset === TO_ASSET);
  if (!btc) return null;
  const btcUnits = Number(btc.balance_asset) / 1e8;
  const usdPerBtc = Number(btc.asset_tor_price ?? 0) / 1e8;
  return { venue: 'thorchain', btcSideUsd: btcUnits * usdPerBtc, stale };
}
