// server.mjs — BANKON.ETH WaaS API (non-custodial), EVM-generic. Twin of bankon-waas/server.mjs.
//
// Same invariant as the BTC/ALGO twins: the API REFUSES any request carrying private material.
// Keys (BIP39 mnemonic → secp256k1 → EVM address) are minted CLIENT-SIDE (ethers.js in the browser);
// the node only sees public addresses (watch-only) and already-signed raw transactions. Works
// against ANY EVM chain — Ethereum, L2s, testnets — identified at runtime via eth_chainId.
import express from 'express';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { ethrpc, ETH_RPC_URL, CHAINS } from './ethrpc.mjs';
import { addWallet, listWallets, getWallet } from './registry.mjs';

const __dir = dirname(fileURLToPath(import.meta.url));
const app = express();
app.set('trust proxy', true);
app.use(express.json({ limit: '256kb' }));
app.use(express.static(join(__dir, 'public')));

const PORT = process.env.BANKON_ETH_PORT || 4448;      // EVM twin port (BTC :8088 · ALGO :4444 · ETH :4448)
const PRIVATE_FIELDS = ['mnemonic', 'privatekey', 'privkey', 'seed', 'passphrase', 'keystore'];

function rejectPrivate(req, res, next) {
  const flat = JSON.stringify(req.body || {}).toLowerCase();
  for (const f of PRIVATE_FIELDS) {
    if (flat.includes(`"${f}"`))
      return res.status(400).json({ error: `BANKON.ETH is non-custodial: never send "${f}". Keep it on your device.` });
  }
  next();
}
app.use(rejectPrivate);

const ok = (res, data) => res.json({ ok: true, ...data });
const fail = (res, e) => res.status(502).json({ ok: false, error: String(e.message || e) });
const ADDR_RE = /^0x[0-9a-fA-F]{40}$/;
const hexToNum = h => (h ? parseInt(h, 16) : 0);
const weiToEth = hex => (hex ? Number(BigInt(hex)) / 1e18 : 0);

// Register a wallet from its PUBLIC EVM address (watch-only tracking).
app.post('/api/wallet', async (req, res) => {
  try {
    const { address, label, owner } = req.body;
    if (!address || !ADDR_RE.test(address)) return res.status(400).json({ error: 'need a valid 0x… EVM address (40 hex)' });
    let chainId = null, balance = null;
    try { chainId = await ethrpc('eth_chainId'); balance = await ethrpc('eth_getBalance', [address, 'latest']); } catch {}
    const rec = addWallet({
      id: address.toLowerCase(), chain: 'evm', address, chainId, chainName: CHAINS[chainId] || null,
      wei: balance, owner: owner || null, label: label || null,
      firstAddress: address, createdAt: new Date().toISOString(),
    });
    ok(res, { wallet: rec });
  } catch (e) { fail(res, e); }
});

app.get('/api/wallets', (req, res) => ok(res, { wallets: listWallets(req.query.owner) }));
app.get('/api/wallets/:id', (req, res) => {
  const w = getWallet(req.params.id.toLowerCase());
  return w ? ok(res, { wallet: w }) : res.status(404).json({ ok: false, error: 'not found' });
});

// Balance + nonce.
app.get('/api/wallet/:addr/balance', async (req, res) => {
  try {
    const [wei, nonce] = await Promise.all([
      ethrpc('eth_getBalance', [req.params.addr, 'latest']),
      ethrpc('eth_getTransactionCount', [req.params.addr, 'latest']),
    ]);
    ok(res, { address: req.params.addr, wei, eth: weiToEth(wei), nonce: hexToNum(nonce) });
  } catch (e) { fail(res, e); }
});
app.get('/api/wallet/:addr/receive', (req, res) => ok(res, { address: req.params.addr })); // EVM: receive = address

// Context for the client to BUILD a tx: chainId, nonce, gas price (legacy + EIP-1559 base fee).
app.get('/api/tx-context/:addr', async (req, res) => {
  try {
    const [chainId, nonce, gasPrice, block] = await Promise.all([
      ethrpc('eth_chainId'),
      ethrpc('eth_getTransactionCount', [req.params.addr, 'pending']),
      ethrpc('eth_gasPrice').catch(() => null),
      ethrpc('eth_getBlockByNumber', ['latest', false]).catch(() => ({})),
    ]);
    ok(res, { chainId, chainName: CHAINS[chainId] || null, nonce, gasPrice, baseFeePerGas: block?.baseFeePerGas || null });
  } catch (e) { fail(res, e); }
});

// Broadcast a CLIENT-SIGNED raw transaction (0x… hex). Server never signs.
app.post('/api/broadcast', async (req, res) => {
  try {
    const { rawTx } = req.body;
    if (!rawTx || !/^0x[0-9a-fA-F]+$/.test(rawTx)) return res.status(400).json({ error: 'need rawTx (0x… client-signed)' });
    const txHash = await ethrpc('eth_sendRawTransaction', [rawTx]);
    ok(res, { txHash });
  } catch (e) { fail(res, e); }
});

// Node health — twin of the BTC/ALGO WaaS /api/health.
app.get('/api/health', async (req, res) => {
  try {
    const [block, chainId, client] = await Promise.all([
      ethrpc('eth_blockNumber'),
      ethrpc('eth_chainId'),
      ethrpc('web3_clientVersion').catch(() => null),
    ]);
    ok(res, { state: 'running', node: ETH_RPC_URL, block: hexToNum(block),
      chainId, chain: CHAINS[chainId] || `chain ${hexToNum(chainId)}`, client });
  } catch (e) { res.json({ ok: true, state: 'down', node: ETH_RPC_URL, error: String(e.message || e) }); }
});

// ETH.oracle — the clock kept on an EVM block (twin of BTC.oracle, mapped to JSON-RPC).
app.get('/api/oracle', async (req, res) => {
  try {
    const latest = await ethrpc('eth_getBlockByNumber', ['latest', false]);
    const num = parseInt(latest.number, 16), tnow = parseInt(latest.timestamp, 16);
    let avg = 12;                                           // EVM block target ≈ 12s (post-merge)
    try {
      const span = Math.min(20, num);
      const back = await ethrpc('eth_getBlockByNumber', ['0x' + (num - span).toString(16), false]);
      if (back && span > 0) avg = (tnow - parseInt(back.timestamp, 16)) / span;
    } catch {}
    const cid = await ethrpc('eth_chainId').catch(() => null);
    res.json({ ok: true, oracle: { chain: CHAINS[cid] || 'evm', height: num,
      timeSinceLastMs: Date.now() - tnow * 1000, avgBlockTime: avg, targetBlockTime: 12 } });
  } catch (e) { res.json({ ok: false, error: String(e.message || e) }); }
});
app.get('/api/recentblocks', async (req, res) => {
  const n = Math.min(Number(req.query.n) || 12, 25);
  try {
    const top = parseInt(await ethrpc('eth_blockNumber'), 16);
    const nums = []; for (let i = 0; i < n && top - i >= 0; i++) nums.push(top - i);
    const blocks = await Promise.all(nums.map(b =>
      ethrpc('eth_getBlockByNumber', ['0x' + b.toString(16), false]).then(x => x ? {
        height: parseInt(x.number, 16), time: parseInt(x.timestamp, 16), nTx: (x.transactions || []).length,
        gasUsed: parseInt(x.gasUsed, 16), baseFee: x.baseFeePerGas ? parseInt(x.baseFeePerGas, 16) : null } : null).catch(() => null)));
    res.json({ ok: true, blocks: blocks.filter(b => b && b.time) });
  } catch (e) { res.json({ ok: false, error: String(e.message || e), blocks: [] }); }
});

// Per-block forensics (the EVM twin of getblockstats) — normalized {fields, derived, raw}.
app.get('/api/block/:num', async (req, res) => {
  try {
    const n = req.params.num, hexn = /^0x/.test(n) ? n : '0x' + parseInt(n, 10).toString(16);
    const b = await ethrpc('eth_getBlockByNumber', [hexn, true]);
    if (!b) return res.json({ ok: false, error: 'block not found' });
    const num = parseInt(b.number, 16), gu = parseInt(b.gasUsed, 16), gl = parseInt(b.gasLimit, 16);
    const base = b.baseFeePerGas ? parseInt(b.baseFeePerGas, 16) : null, txs = b.transactions || [];
    const fields = { number: num, timestamp: parseInt(b.timestamp, 16), txns: txs.length, miner: b.miner,
      gasUsed: gu, gasLimit: gl, 'baseFee (wei)': base, size: parseInt(b.size, 16) };
    const burned = base ? (base * gu) / 1e18 : null;
    const derived = { 'gas used %': (gu / gl * 100).toFixed(2) + '%', 'base fee (gwei)': base != null ? (base / 1e9).toFixed(3) : '—',
      'burned (ETH)': burned != null ? burned.toFixed(6) : '—', 'txns / block': txs.length,
      'avg gas / tx': txs.length ? Math.round(gu / txs.length) : 0, hash: String(b.hash || '').slice(0, 18) + '…' };
    res.json({ ok: true, height: num, time: parseInt(b.timestamp, 16), nTx: txs.length, fields, derived, raw: { ...b, transactions: `[${txs.length} txns]` } });
  } catch (e) { res.json({ ok: false, error: String(e.message || e) }); }
});

app.listen(PORT, '127.0.0.1', () =>
  console.log(`BANKON.ETH WaaS on http://127.0.0.1:${PORT}  · EVM node ${ETH_RPC_URL}  · non-custodial (keys client-side)`));
