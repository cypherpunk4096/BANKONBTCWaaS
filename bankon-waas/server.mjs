// server.mjs — BANKON Wallet-as-a-Service API (non-custodial).
//
// Hard rule enforced below: the API REFUSES any request that carries private
// material. The node imports descriptors WATCH-ONLY (disable_private_keys), so
// it is structurally incapable of spending. Signing happens on the client.
//
import express from 'express';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { rpc } from './rpc.mjs';
import { anchorHash, verifyAnchor } from './anchor.mjs';
import { collectOnce, stats, DATABASE_URL } from './node-collector.mjs';
import { addWallet, listWallets, getWallet } from './registry.mjs';
import { apiAuth, rateLimit } from '../shared/security.mjs';

const __dir = dirname(fileURLToPath(import.meta.url));
const app = express();
app.set('trust proxy', true);
app.use(express.json({ limit: '64kb' }));
app.use(express.static(join(__dir, 'public')));
// Serve the repo docs (read-only) so the in-UI FAQ can link to the detailed guides.
app.use('/docs', express.static(join(__dir, '..', 'docs'), { extensions: ['md'] }));
app.use(rateLimit());                 // generous; backstops abuse
app.use('/api', apiAuth());           // off unless BANKON_API_TOKEN is set

const PORT = process.env.BANKON_PORT || 8088;
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

const ok = (res, data) => res.json({ ok: true, ...data });
const fail = (res, e) => res.status(502).json({ ok: false, error: String(e.message || e) });

// Register a wallet from its PUBLIC descriptor bundle (watch-only import).
app.post('/api/wallet', async (req, res) => {
  try {
    const { type, fingerprint, xpub, external, internal, label, owner } = req.body;
    if (!external || !internal) return res.status(400).json({ error: 'missing descriptor (external/internal)' });
    const name = label || `bankon_${fingerprint}_${type}`;

    // Watch-only descriptor wallet: disable_private_keys=true, blank=true, descriptors=true.
    await rpc('createwallet', [name, true, true, '', false, true, true]).catch(e => {
      if (!/already exists/i.test(e.message)) throw e;
    });

    // Ask Core to canonicalise + checksum each descriptor (public operation).
    const withChecksum = async d => (await rpc('getdescriptorinfo', [d])).descriptor;
    const ext = await withChecksum(external);
    const int = await withChecksum(internal);

    await rpc('importdescriptors', [[
      { desc: ext, timestamp: 'now', active: true, internal: false, range: [0, 100] },
      { desc: int, timestamp: 'now', active: true, internal: true,  range: [0, 100] },
    ]], name);

    const address = await rpc('getnewaddress', ['', type === 'taproot' ? 'bech32m' : type === 'legacy' ? 'legacy' : 'bech32'], name);

    // Record public metadata in the registry (multi-user via optional owner).
    const record = addWallet({
      id: name, type, fingerprint, xpub, external, internal,
      owner: owner || null, label: label || null,
      firstAddress: address, createdAt: new Date().toISOString(),
    });
    ok(res, { wallet: name, walletId: name, watchOnly: true, firstAddress: address, record });
  } catch (e) { fail(res, e); }
});

// Register an N-of-M multisig watch-only wallet from cosigner PUBLIC keys.
// keys: [{ xpub, fingerprint, path }]  (path e.g. "48'/0'/0'/2'"). Each cosigner
// signs the PSBT with the offline client; combine the partials via combinepsbt.
app.post('/api/wallet/multisig', async (req, res) => {
  try {
    const { threshold, keys, label, owner } = req.body;
    if (!Array.isArray(keys) || keys.length < 2) return res.status(400).json({ error: 'need >= 2 cosigner keys' });
    if (!threshold || threshold < 1 || threshold > keys.length) return res.status(400).json({ error: 'invalid threshold' });
    for (const k of keys) if (!k.xpub || !k.fingerprint || !k.path) return res.status(400).json({ error: 'each key needs {xpub, fingerprint, path}' });

    const branch = b => keys.map(k => `[${k.fingerprint}/${k.path}]${k.xpub}/${b}/*`).join(',');
    const external = `wsh(sortedmulti(${threshold},${branch(0)}))`;
    const internal = `wsh(sortedmulti(${threshold},${branch(1)}))`;
    const name = label || `bankon_ms_${threshold}of${keys.length}_${keys[0].fingerprint}`;

    await rpc('createwallet', [name, true, true, '', false, true, true]).catch(e => { if (!/already exists/i.test(e.message)) throw e; });
    const wc = async d => (await rpc('getdescriptorinfo', [d])).descriptor;
    const ext = await wc(external), int = await wc(internal);
    await rpc('importdescriptors', [[
      { desc: ext, timestamp: 'now', active: true, internal: false, range: [0, 100] },
      { desc: int, timestamp: 'now', active: true, internal: true,  range: [0, 100] },
    ]], name);
    const address = await rpc('getnewaddress', ['', 'bech32'], name);

    const record = addWallet({
      id: name, type: 'multisig', threshold, cosigners: keys.length,
      external, internal, owner: owner || null, label: label || null,
      firstAddress: address, createdAt: new Date().toISOString(),
    });
    ok(res, { wallet: name, walletId: name, watchOnly: true, threshold, cosigners: keys.length, firstAddress: address, record });
  } catch (e) { fail(res, e); }
});

// Registry: list wallets (optionally by owner) and fetch one. Public metadata only.
app.get('/api/wallets', (req, res) => ok(res, { wallets: listWallets(req.query.owner) }));
app.get('/api/wallets/:id', (req, res) => {
  const w = getWallet(req.params.id);
  return w ? ok(res, { wallet: w }) : res.status(404).json({ ok: false, error: 'not found' });
});

// Fee estimation → sat/vB tiers (fast/medium/slow) + mempool floor.
app.get('/api/fees', async (req, res) => {
  const toSatVb = btcPerKvb => (btcPerKvb ? Math.max(1, Math.round(btcPerKvb * 1e5)) : null);
  const targets = { fast: 1, medium: 3, slow: 6 };
  // Parallel so the endpoint returns within one timeout window even mid-IBD.
  const entries = await Promise.all(Object.entries(targets).map(async ([tier, blocks]) => {
    try { return [tier, toSatVb((await rpc('estimatesmartfee', [blocks]))?.feerate)]; }
    catch { return [tier, null]; }
  }));
  const fees = Object.fromEntries(entries);
  try { fees.minRelay = toSatVb((await rpc('getmempoolinfo'))?.mempoolminfee); } catch {}
  const haveData = ['fast', 'medium', 'slow'].some(k => fees[k]);
  ok(res, { fees, unit: 'sat/vB', note: haveData ? null : 'no estimate yet (node still syncing) — defaults applied client-side' });
});

app.get('/api/wallet/:name/balance', async (req, res) => {
  try { ok(res, { balances: await rpc('getbalances', [], req.params.name) }); }
  catch (e) { fail(res, e); }
});

app.get('/api/wallet/:name/receive', async (req, res) => {
  try { ok(res, { address: await rpc('getnewaddress', [''], req.params.name) }); }
  catch (e) { fail(res, e); }
});

// BIP21 payment request — a shareable "bitcoin:" URI carrying amount / label / message. Bitcoin
// Core does NOT produce this (it only hands you a bare address); formatting a payment request is a
// wallet-layer job. A fresh receive address is used unless ?address= is supplied — and with an
// explicit address this needs no node, so it works standalone / air-gapped.
function bip21(address, { amount, label, message } = {}) {
  if (!address || typeof address !== 'string') throw new Error('address required');
  const p = new URLSearchParams();
  if (amount != null && amount !== '') {
    const a = Number(amount);
    if (!Number.isFinite(a) || a < 0) throw new Error('amount must be a non-negative number of BTC');
    p.set('amount', a.toFixed(8).replace(/\.?0+$/, ''));   // BTC, trailing zeros trimmed
  }
  if (label) p.set('label', String(label).slice(0, 200));
  if (message) p.set('message', String(message).slice(0, 500));
  const qs = p.toString().replace(/\+/g, '%20');           // BIP21 prefers %20 over +
  return `bitcoin:${address}${qs ? '?' + qs : ''}`;
}
app.get('/api/wallet/:name/payment-request', async (req, res) => {
  try {
    const { amount, label, message } = req.query;
    let address = req.query.address;
    if (!address) address = await rpc('getnewaddress', [label ? String(label) : ''], req.params.name);
    const uri = bip21(address, { amount, label, message });
    ok(res, { address, uri, bip21: uri,
      request: { amount: amount ?? null, label: label ?? null, message: message ?? null } });
  } catch (e) { fail(res, e); }
});

app.get('/api/wallet/:name/history', async (req, res) => {
  const n = Math.min(Number(req.query.n) || 20, 100);
  try { ok(res, { txs: await rpc('listtransactions', ['*', n, 0], req.params.name) }); }
  catch (e) { fail(res, e); }
});

// Build an UNSIGNED PSBT for the client to sign offline. Server never signs.
app.post('/api/wallet/:name/send', async (req, res) => {
  try {
    const { to, amountBtc, feeRate } = req.body;
    if (!to || !amountBtc) return res.status(400).json({ error: 'need {to, amountBtc}' });
    const opts = feeRate ? { fee_rate: feeRate } : {};
    const r = await rpc('walletcreatefundedpsbt', [[], [{ [to]: amountBtc }], 0, opts], req.params.name);
    ok(res, { psbt: r.psbt, fee: r.fee, note: 'Sign this PSBT on your device, then POST the signed tx hex to /api/broadcast.' });
  } catch (e) { fail(res, e); }
});

app.post('/api/broadcast', async (req, res) => {
  try {
    const { hex } = req.body;
    if (!hex) return res.status(400).json({ error: 'need {hex} of a fully-signed tx' });
    ok(res, { txid: await rpc('sendrawtransaction', [hex]) });
  } catch (e) { fail(res, e); }
});

// --- Canonical OP_RETURN anchor (Bitcoin-timestamped proof-of-existence) ---
// Anchor a 32-byte hash (or hash arbitrary data) to the chain. Needs a funded anchor
// wallet (BANKON_ANCHOR_WALLET); on mainnet that means full sync + funds.
app.post('/api/anchor', async (req, res) => {
  try {
    const { hash, data, feeRate, wallet } = req.body || {};
    const input = hash != null ? hash : data;
    if (input == null) return res.status(400).json({ error: 'provide "hash" (64-hex) or "data" to anchor' });
    ok(res, await anchorHash(input, { wallet, feeRate }));
  } catch (e) { fail(res, e); }
});

// Verify an anchor: does the tx's OP_RETURN equal the hash of the supplied data/hash?
app.post('/api/verify', async (req, res) => {
  try {
    const { txid, data, hash } = req.body || {};
    if (!txid) return res.status(400).json({ error: 'provide "txid"' });
    const input = hash != null ? hash : data;
    if (input == null) return res.status(400).json({ error: 'provide "data" or "hash" to check against' });
    ok(res, await verifyAnchor(txid, input));
  } catch (e) { fail(res, e); }
});

app.get('/api/health', async (req, res) => {
  try {
    const info = await rpc('getblockchaininfo');
    ok(res, { chain: info.chain, blocks: info.blocks, progress: info.verificationprogress, ibd: info.initialblockdownload });
  } catch (e) { fail(res, e); }
});

// --- Node intelligence: collect live network into Postgres (pgvector + pgvectorscale) ---
// Collects from THIS single Bitcoin Core instance (addrman + peers), enriches with GeoIP,
// tracks uptime/version, embeds for vector search. Needs DATABASE_URL (your pgvectorscale DB).
let _collecting = false;
app.post('/api/nodes/collect', async (req, res) => {
  if (_collecting) return res.status(409).json({ ok: false, error: 'a collection pass is already running' });
  const dryRun = !DATABASE_URL || req.body?.dryRun === true;
  _collecting = true;
  try { ok(res, await collectOnce({ dryRun, limit: req.body?.limit || 5000 })); }
  catch (e) { fail(res, e); }
  finally { _collecting = false; }
});
app.get('/api/nodes/stats', async (req, res) => {
  if (!DATABASE_URL) return res.status(503).json({ ok: false, error: 'DATABASE_URL not set (no pgvectorscale DB configured)' });
  try { ok(res, await stats()); } catch (e) { fail(res, e); }
});

// Opt-in background collection loop (set BANKON_COLLECT_MS, e.g. 600000 = 10 min).
const COLLECT_MS = Number(process.env.BANKON_COLLECT_MS) || 0;
if (COLLECT_MS && DATABASE_URL) {
  const tick = async () => {
    if (_collecting) return; _collecting = true;
    try { const r = await collectOnce({}); console.log(`[collector] upserted ${r.collected} nodes (${r.geocoded} geocoded)`); }
    catch (e) { console.error('[collector]', e.message); }
    finally { _collecting = false; }
  };
  setInterval(tick, COLLECT_MS); setTimeout(tick, 5000);
  console.log(`BANKON node collector: every ${COLLECT_MS / 1000}s → ${DATABASE_URL.replace(/:[^:@/]+@/, ':***@')}`);
}

app.listen(PORT, '127.0.0.1', () =>
  console.log(`BANKON WaaS API on http://127.0.0.1:${PORT}  (non-custodial; node holds watch-only descriptors only)`));
