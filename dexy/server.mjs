// server.mjs — DEXY: sovereign-custody BTC liquidity mover (₿ANKON module).
//
// CEX→DEX projection + native-BTC DEX accumulation (THORChain/Maya/Chainflip)
// where the destination is ALWAYS an address whose keys the user holds (WaaS
// BTC Standard) — never wrapped BTC, never a server-side key. DEXY plans and
// quotes; the USER executes from their own wallet.
//
// Pairs policy: /api/pairs stays chain-native (no external feed, no fiat) —
// served by importing pairsRouter() from bankon-waas unmodified. External
// market data lives under /api/dexy/* and every response carries `source:`.
//
// DEXY WaaS twin port :8091 (BTC :8088 · PAIRS :8089 · Console :8090 · ALGO :4444 · ETH :4448)

import express from 'express';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { pairsRouter } from '../bankon-waas/pairs.mjs';
import { swapRouter } from '../bankon-waas/swap.mjs';
import { apiAuth, rateLimit } from '../shared/security.mjs';
import { ok, fail, badRequest } from './lib.mjs';
import { fetchCexBtcHoldings, fetchDexBtcDepth, projectTransfer, dexyReport } from './dexy.mjs';
import { planAccumulation, quoteAll } from './aggregator.mjs';
import { thorchainStatus } from './venues/thorchain.mjs';
import { verifyDestination, requireSovereign, strictCustody, listOwnWallets } from './custody.mjs';
import { quoteToll } from './facilitator.mjs';

const __dir = dirname(fileURLToPath(import.meta.url));
const app = express();
app.set('trust proxy', true);
app.use(express.json({ limit: '64kb' }));
app.use(express.static(join(__dir, 'public')));
app.use('/docs', express.static(join(__dir, 'docs'), { extensions: ['md'] }));
app.use(rateLimit());                 // generous; backstops abuse
app.use('/api', apiAuth());           // off unless BANKON_API_TOKEN is set

const PORT = process.env.BANKON_DEXY_PORT || 8091;
const ARRBY_URL = process.env.ARRBY_URL || 'http://127.0.0.1:8787';
const ABSORPTION_PCT = Number(process.env.DEXY_ABSORPTION_PCT || 0.10);
const MAX_SLIP_BPS = Number(process.env.DEXY_MAX_SLIP_BPS || 100);
const PRIVATE_FIELDS = ['mnemonic', 'xprv', 'seed', 'privkey', 'wif', 'passphrase'];

// Defensive non-custodial guard: reject anything that looks like a secret.
function rejectPrivate(req, res, next) {
  const flat = JSON.stringify(req.body || {}).toLowerCase();
  for (const f of PRIVATE_FIELDS) {
    if (flat.includes(`"${f}"`)) {
      return res.status(400).json({ error: `BANKON is non-custodial: never send "${f}". Keep it on your device.` });
    }
  }
  next();
}
app.use(rejectPrivate);

// ── Reused BANKON primitives (modular inclusion, nothing overwritten) ────────
app.use(pairsRouter());   // GET /api/pairs — chain-native, SPINTRADE-compatible
app.use(swapRouter());    // /api/swap/htlc/* — trustless BTC HTLC leg (watch-only)

// ── CEX custody snapshot (proof-of-reserve) ─────────────────────────────────
app.get('/api/dexy/cex', async (_req, res) => {
  try {
    const { holdings, stale } = await fetchCexBtcHoldings();
    ok(res, { source: 'defillama proof-of-reserve (external market data)', holdings, stale, asOf: new Date().toISOString() });
  } catch (e) { fail(res, e); }
});

// ── Native-BTC DEX depth + venue health ─────────────────────────────────────
app.get('/api/dexy/venues', async (_req, res) => {
  try {
    const depths = await fetchDexBtcDepth();
    const known = ['thorchain', 'chainflip'];
    const health = known.map(v => ({
      venue: v,
      reachable: depths.some(d => d.venue === v),
      stale: depths.find(d => d.venue === v)?.stale ?? null,
    }));
    ok(res, { source: 'thornode + chainflip (external market data)', depths, health, asOf: new Date().toISOString() });
  } catch (e) { fail(res, e); }
});

// ── CEX→DEX transfer projection (pure math over live depth) ─────────────────
app.get('/api/dexy/project', async (req, res) => {
  try {
    const moveUsd = Number(req.query.moveUsd);
    if (!(moveUsd > 0)) return badRequest(res, 'moveUsd required (> 0)');
    const absorptionPct = Number(req.query.absorptionPct || ABSORPTION_PCT);
    const slipBps = Number(req.query.slipBps || 30);
    const report = await dexyReport(moveUsd, absorptionPct, slipBps);
    ok(res, report);
  } catch (e) { fail(res, e); }
});

// ── Per-venue quotes → native BTC at the USER'S address ─────────────────────
app.get('/api/dexy/quote', async (req, res) => {
  try {
    const { chain, symbol, contract, btcAddress } = req.query;
    const decimals = Number(req.query.decimals);
    if (!chain || !symbol || !Number.isInteger(decimals)) return badRequest(res, 'chain, symbol, decimals required');
    let amount; try { amount = BigInt(req.query.amount); } catch { return badRequest(res, 'amount must be an integer in base units'); }
    if (amount <= 0n) return badRequest(res, 'amount must be > 0');
    if (!btcAddress) return badRequest(res, 'btcAddress required — DEXY only quotes into YOUR self-custody');

    const strict = req.query.strict === '1' || strictCustody();
    let destination;
    try { destination = await requireSovereign(btcAddress, { strict }); }
    catch (e) { return badRequest(res, String(e.message || e)); }

    const src = { chain: String(chain), symbol: String(symbol), contract: contract || undefined, decimals };
    const maxSlipBps = Number(req.query.maxSlipBps || MAX_SLIP_BPS);
    const quotes = await quoteAll(src, amount, btcAddress, maxSlipBps);
    ok(res, {
      source: 'thorchain + maya + chainflip + metamask-oracle (external venues)',
      destination: { address: btcAddress, ...destination },
      quotes,
      sovereignNote: 'venues pay YOUR address directly — DEXY never custodies, never signs',
    });
  } catch (e) { fail(res, e); }
});

// ── Multi-leg accumulation plan (greedy split under maxSlipBps) ──────────────
app.post('/api/dexy/plan', async (req, res) => {
  try {
    const { sourceAsset, btcAddress } = req.body || {};
    if (!btcAddress) return badRequest(res, 'btcAddress required — DEXY only plans into YOUR self-custody');
    if (!sourceAsset?.chain || !sourceAsset?.symbol || !Number.isInteger(sourceAsset?.decimals))
      return badRequest(res, 'sourceAsset {chain, symbol, decimals[, contract]} required');
    let targetBtcSats, maxSpendNative;
    try {
      targetBtcSats = BigInt(req.body.targetBtcSats);
      maxSpendNative = BigInt(req.body.maxSpendNative);
    } catch { return badRequest(res, 'targetBtcSats and maxSpendNative must be integers (sats / base units)'); }
    if (targetBtcSats <= 0n || maxSpendNative <= 0n) return badRequest(res, 'targetBtcSats and maxSpendNative must be > 0');

    const strict = req.body.strict === true || strictCustody();
    let destination;
    try { destination = await requireSovereign(btcAddress, { wallet: req.body.wallet || null, strict }); }
    catch (e) { return badRequest(res, String(e.message || e)); }

    const order = {
      orderId: `dexy-${Date.now().toString(36)}`,
      destBtcAddress: btcAddress,
      targetBtcSats,
      sourceAsset,
      maxSlipBps: Number(req.body.maxSlipBps || MAX_SLIP_BPS),
    };
    const plan = await planAccumulation(order, maxSpendNative);
    ok(res, {
      destination: { address: btcAddress, ...destination },
      plan,
      execute: 'send each leg from YOUR wallet before deposit.expiresAt — DEXY never signs',
      sovereignNote: 'native BTC.BTC only — no wrapped-BTC output path exists',
    });
  } catch (e) { fail(res, e); }
});

// ── Venue swap status ────────────────────────────────────────────────────────
app.get('/api/dexy/status', async (req, res) => {
  try {
    const { venue, txid } = req.query;
    if (venue !== 'thorchain') return badRequest(res, 'status supported for venue=thorchain (maya/chainflip: Phase 2)');
    if (!txid) return badRequest(res, 'txid required');
    ok(res, { venue, txid, status: await thorchainStatus(String(txid)) });
  } catch (e) { fail(res, e); }
});

// ── Sovereign destination helpers ────────────────────────────────────────────
app.get('/api/dexy/custody/wallets', (_req, res) => {
  try { ok(res, { wallets: listOwnWallets() }); } catch (e) { fail(res, e); }
});

// ── Sovereign destination verification ───────────────────────────────────────
app.get('/api/dexy/custody/verify', async (req, res) => {
  try {
    const { address, wallet } = req.query;
    if (!address) return badRequest(res, 'address required');
    ok(res, { address, strictMode: strictCustody(), ...(await verifyDestination(String(address), { wallet: wallet || null })) });
  } catch (e) { fail(res, e); }
});

// ── BANKON toll quote (golden ratio of gas → bankon.eth treasury) ────────────
// Off-chain mirror of contracts/BankonToll.sol. Quote the toll before a client
// sends any BANKON facilitation/bridge/mint tx. Pass ?gasFeeWei= or ?gasUnits=&gasPriceWei=.
app.get('/api/dexy/facilitator/quote', (req, res) => {
  try {
    const { gasFeeWei, gasUnits, gasPriceWei } = req.query;
    if (gasFeeWei == null && (gasUnits == null || gasPriceWei == null))
      return badRequest(res, 'pass gasFeeWei, or both gasUnits and gasPriceWei');
    ok(res, {
      source: 'BankonToll (on-chain contract mirror)',
      contract: 'dexy/contracts/BankonToll.sol',
      ...quoteToll({ gasFeeWei, gasUnits, gasPriceWei }),
    });
  } catch (e) { badRequest(res, String(e.message || e)); }
});

// ── ARRBY (EVM DEX→DEX) reverse proxy — quote-only server-side ───────────────
app.use('/api/dexy/arrby', async (req, res) => {
  try {
    const r = await fetch(`${ARRBY_URL}${req.url === '/' ? '' : req.url}`, {
      method: req.method,
      headers: { 'content-type': 'application/json' },
      body: ['GET', 'HEAD'].includes(req.method) ? undefined : JSON.stringify(req.body || {}),
      signal: AbortSignal.timeout(8000),
    });
    res.status(r.status).type(r.headers.get('content-type') || 'application/json').send(await r.text());
  } catch {
    res.status(503).json({
      ok: false,
      error: 'ARRBY backend down',
      hint: `start it: cd ${join(__dir, 'vendor/arrby/arrby/backend')} && npm install && npm start (ARRBY_URL=${ARRBY_URL})`,
    });
  }
});
app.get('/arrby', (_req, res) => res.sendFile(join(__dir, 'public', 'arrby.html')));

app.get('/api/health', (_req, res) => ok(res, {
  service: 'dexy', port: Number(PORT), strictCustody: strictCustody(),
  custody: 'sovereign — YOUR keys (WaaS BTC Standard), never wrapped BTC, never server-side keys',
}));

app.listen(PORT, '127.0.0.1', () =>
  console.log(`⟲ DEXY (sovereign BTC liquidity) → http://127.0.0.1:${PORT}  ·  pairs ✓  htlc ✓  arrby → ${ARRBY_URL}`));
