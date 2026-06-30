// server.mjs — BANKON ALGO Wallet-as-a-Service API (non-custodial). Twin of bankon-waas/server.mjs.
//
// Same invariant as the Bitcoin WaaS: the API REFUSES any request carrying private material. Keys
// (Algorand 25-word mnemonic → ed25519) are minted CLIENT-SIDE; the node only ever sees public
// addresses (watch-only) and already-signed transactions. Account-based chain → no descriptors;
// "register" just tracks a public address.
import express from 'express';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { algod, ALGOD_URL } from './algod.mjs';
import { addWallet, listWallets, getWallet } from './registry.mjs';

const __dir = dirname(fileURLToPath(import.meta.url));
const app = express();
app.set('trust proxy', true);
app.use(express.json({ limit: '256kb' }));
app.use(express.static(join(__dir, 'public')));

const PORT = process.env.BANKON_ALGO_PORT || 4444;     // ALGO twin port (counts from 4444; Console reserved 4446)
const PRIVATE_FIELDS = ['mnemonic', 'sk', 'secretkey', 'seed', 'privkey', 'passphrase'];

function rejectPrivate(req, res, next) {
  const flat = JSON.stringify(req.body || {}).toLowerCase();
  for (const f of PRIVATE_FIELDS) {
    if (flat.includes(`"${f}"`))
      return res.status(400).json({ error: `BANKON ALGO is non-custodial: never send "${f}". Keep it on your device.` });
  }
  next();
}
app.use(rejectPrivate);

const ok = (res, data) => res.json({ ok: true, ...data });
const fail = (res, e) => res.status(502).json({ ok: false, error: String(e.message || e) });
const ADDR_RE = /^[A-Z2-7]{58}$/;                       // Algorand base32 address

// Register a wallet from its PUBLIC address (watch-only tracking).
app.post('/api/wallet', async (req, res) => {
  try {
    const { address, label, owner } = req.body;
    if (!address || !ADDR_RE.test(address)) return res.status(400).json({ error: 'need a valid 58-char Algorand address' });
    let amount = null;
    try { amount = (await algod(`/v2/accounts/${address}`)).amount; } catch {}
    const rec = addWallet({
      id: address, chain: 'algorand', address, owner: owner || null, label: label || null,
      microAlgos: amount, firstAddress: address, createdAt: new Date().toISOString(),
    });
    ok(res, { wallet: rec });
  } catch (e) { fail(res, e); }
});

app.get('/api/wallets', (req, res) => ok(res, { wallets: listWallets(req.query.owner) }));
app.get('/api/wallets/:id', (req, res) => {
  const w = getWallet(req.params.id);
  return w ? ok(res, { wallet: w }) : res.status(404).json({ ok: false, error: 'not found' });
});

// Balance / account state.
app.get('/api/wallet/:addr/balance', async (req, res) => {
  try {
    const a = await algod(`/v2/accounts/${req.params.addr}`);
    ok(res, { address: req.params.addr, microAlgos: a.amount, algo: a.amount / 1e6,
      round: a.round, status: a.status, assets: (a.assets || []).length, minBalance: a['min-balance'] ?? null });
  } catch (e) { fail(res, e); }
});
// Algorand: the receive address IS the account address (no derivation).
app.get('/api/wallet/:addr/receive', (req, res) => ok(res, { address: req.params.addr }));

// Suggested params for the client to BUILD an unsigned txn (fee, first/last valid, genesis).
app.get('/api/params', async (req, res) => {
  try { ok(res, { params: await algod('/v2/transactions/params') }); } catch (e) { fail(res, e); }
});

// Broadcast a CLIENT-SIGNED transaction (base64 msgpack → raw to algod). Server never signs.
app.post('/api/broadcast', async (req, res) => {
  try {
    const { stxBase64 } = req.body;
    if (!stxBase64) return res.status(400).json({ error: 'need stxBase64 (a client-signed transaction)' });
    const raw = Buffer.from(stxBase64, 'base64');
    const r = await algod('/v2/transactions', { method: 'POST', body: raw, raw: true });
    ok(res, { txId: r.txId || r.txid || r });
  } catch (e) { fail(res, e); }
});

// Node health — twin of the BTC WaaS /api/health.
app.get('/api/health', async (req, res) => {
  try {
    await algod('/health');
    const s = await algod('/v2/status');
    ok(res, { state: 'running', node: ALGOD_URL, round: s['last-round'],
      catchupTime: s['catchup-time'], behind: s['catchpoint-acquired-blocks'] ?? null });
  } catch (e) { res.json({ ok: true, state: 'down', node: ALGOD_URL, error: String(e.message || e) }); }
});

// ALGO.oracle — the clock kept on an Algorand round (twin of BTC.oracle, mapped to algod).
app.get('/api/oracle', async (req, res) => {
  try {
    const s = await algod('/v2/status');
    const last = s['last-round'];
    let avg = 3.3;                                          // Algorand round target ≈ 3.3s
    try {
      const span = Math.min(20, last - 1);
      const a = (await algod(`/v2/blocks/${last}?format=json`)).block;
      const b = (await algod(`/v2/blocks/${last - span}?format=json`)).block;
      if (a?.ts && b?.ts && span > 0) avg = (a.ts - b.ts) / span;
    } catch {}
    res.json({ ok: true, oracle: { chain: 'algorand', height: last,
      timeSinceLastMs: (s['time-since-last-round'] || 0) / 1e6, avgBlockTime: avg, targetBlockTime: 3.3 } });
  } catch (e) { res.json({ ok: false, error: String(e.message || e) }); }
});
app.get('/api/recentblocks', async (req, res) => {
  const n = Math.min(Number(req.query.n) || 12, 25);
  try {
    const last = (await algod('/v2/status'))['last-round'];
    const rounds = []; for (let i = 0; i < n && last - i > 0; i++) rounds.push(last - i);
    const blocks = await Promise.all(rounds.map(r =>
      algod(`/v2/blocks/${r}?format=json`).then(d => { const b = d.block || d; return { height: r, time: b.ts, nTx: Array.isArray(b.txns) ? b.txns.length : 0 }; }).catch(() => null)));
    res.json({ ok: true, blocks: blocks.filter(b => b && b.time) });
  } catch (e) { res.json({ ok: false, error: String(e.message || e), blocks: [] }); }
});

// Per-round forensics (the ALGO twin of getblockstats) — normalized {fields, derived, raw}.
app.get('/api/block/:round', async (req, res) => {
  try {
    const d = await algod(`/v2/blocks/${req.params.round}?format=json`);
    const b = d.block || d;
    const txns = Array.isArray(b.txns) ? b.txns : [];
    let fees = 0; for (const t of txns) fees += (t.txn?.fee || 0);
    const fields = { round: b.rnd, timestamp: b.ts, txns: txns.length, proposer: b.prp || '—',
      'genesis-id': b.gen || '—', protocol: String(b.proto || '').slice(-12), 'rewards-rate': b.rate ?? '—',
      'fees collected (µAlgo)': fees };
    const derived = { 'txns / round': txns.length, 'avg fee (µAlgo)': txns.length ? +(fees / txns.length).toFixed(1) : 0,
      seed: String(b.seed || '').slice(0, 16) + '…', prev: String(b.prev || '').slice(0, 16) + '…' };
    res.json({ ok: true, height: b.rnd, time: b.ts, nTx: txns.length, fields, derived, raw: { ...b, txns: `[${txns.length} txns]` } });
  } catch (e) { res.json({ ok: false, error: String(e.message || e) }); }
});

app.listen(PORT, '127.0.0.1', () =>
  console.log(`BANKON ALGO WaaS on http://127.0.0.1:${PORT}  · algod ${ALGOD_URL}  · non-custodial (keys client-side)`));
