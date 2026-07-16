// ₿ PAIRS — the ₿itcoin blockchain expressed as chain-native trading pairs. PROPOSAL MODULE:
// purely additive, overwrites nothing. Two ways to run it, both leaving server.mjs untouched:
//
//   standalone:  node pairs.mjs                    → http://127.0.0.1:8089/api/pairs
//   mounted:     app.use(pairsRouter());           → one ADDITIVE line in server.mjs (opt-in)
//
// Policy: NO external feed, NO fiat — every quote derives from the local node's RPC alone.
// The mempool is the order book (blockspace asks), the last block is the last fill.
// Exact integer arithmetic (BigInt) rendered to 18 decimals — same accuracy standard as
// bankon-qt/services/precision.py.
import express from 'express';
import { rpc } from './rpc.mjs';

const E18 = 10n ** 18n;

function e18(x) {                       // BigInt scaled 1e18 → "N.dddddddddddddddddd"
  const neg = x < 0n; if (neg) x = -x;
  const ip = x / E18, fp = (x % E18).toString().padStart(18, '0');
  return `${neg ? '-' : ''}${ip.toString().replace(/\B(?=(\d{3})+(?!\d))/g, ',')}.${fp}`;
}
const satToBtc18 = sats => e18(BigInt(sats) * E18 / 100000000n);

function workFromBits(bitsHex) {        // exact expected hashes = 2^256 / (target+1)
  const bits = parseInt(bitsHex, 16);
  const exp = bits >>> 24, mant = BigInt(bits & 0xffffff);
  const target = exp > 3 ? mant << BigInt(8 * (exp - 3)) : mant >> BigInt(8 * (3 - exp));
  return target > 0n ? (1n << 256n) / (target + 1n) : 0n;
}

export async function computePairs() {
  const tipHash = await rpc('getbestblockhash');
  const [hdr, st, mp] = await Promise.all([
    rpc('getblockheader', [tipHash]), rpc('getblockstats', [tipHash]), rpc('getmempoolinfo'),
  ]);
  const asks = {};                       // blockspace ASK ladder (sat/vB by confirmation depth)
  for (const [k, blocks] of [['nextBlock', 1], ['3blk', 3], ['6blk', 6], ['1day', 144]]) {
    try {
      const f = (await rpc('estimatesmartfee', [blocks]))?.feerate;
      asks[k] = f ? Math.max(1, Math.round(f * 1e5)) : null;
    } catch { asks[k] = null; }
  }
  const fees = BigInt(st.totalfee ?? 0), subsidy = BigInt(st.subsidy ?? 0);
  const work = workFromBits(hdr.bits);
  return {
    venue: 'the ₿itcoin blockchain — chain-native, no external feed, no fiat',
    caip2: 'bip122:000000000019d6689c085ae165831e93',
    asOfBlock: st.height, asOfHash: tipHash,
    pairs: [
      { pair: 'SAT/vB', name: 'blockspace market',
        book: { asks, floor_minRelay: Math.max(1, Math.round((mp.mempoolminfee ?? 0) * 1e5)) },
        lastFill: { block: st.height, feerate_percentiles_p10_p90: st.feerate_percentiles,
                    avg: st.avgfeerate, unit: 'sat/vB' } },
      { pair: '₿TC/BLOCK', name: 'block market — reward per block (exact)',
        subsidy: satToBtc18(subsidy), fees: satToBtc18(fees),
        last: satToBtc18(subsidy + fees), unit: '₿TC' },
      { pair: 'SAT/HASH', name: 'security market — what the last block paid per expected hash',
        last: work > 0n ? e18(fees * E18 / work) : null,
        expectedHashes: work.toString(), unit: 'sat (18 dp)' },
      { pair: 'SATPAY', name: 'payment price — what an on-chain payment costs RIGHT NOW in sats '
                              + '(typical 140 vB tx: 1-in/2-out P2WPKH, at the next-block ask)',
        last: asks.nextBlock ? asks.nextBlock * 140 : null,
        ladder: Object.fromEntries(Object.entries(asks).map(([k, v]) => [k, v ? v * 140 : null])),
        typicalVb: 140, unit: 'sat' },
      { pair: '₿TC/DAY', name: 'issuance flow — subsidy × 144 blocks',
        last: satToBtc18(subsidy * 144n), unit: '₿TC' },
      { pair: 'vB/BLOCK', name: 'blockspace supply — consensus ceiling per block',
        last: 1000000, lastBlockUsed: Math.round((st.total_weight ?? 0) / 4), unit: 'vB' },
    ],
  };
}

export function pairsRouter() {
  const r = express.Router();
  r.get('/api/pairs', async (_req, res) => {
    try { res.json({ ok: true, ...(await computePairs()) }); }
    catch (e) { res.status(503).json({ ok: false, error: String(e.message || e) }); }
  });
  return r;
}

// standalone: node pairs.mjs  (port via BANKON_PAIRS_PORT, default 8089 — loopback only)
import { fileURLToPath } from 'node:url';
if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const app = express();
  app.use(pairsRouter());
  const port = Number(process.env.BANKON_PAIRS_PORT) || 8089;
  app.listen(port, '127.0.0.1', () =>
    console.log(`₿ PAIRS (proposal module) → http://127.0.0.1:${port}/api/pairs`));
}
