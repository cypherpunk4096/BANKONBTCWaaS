/**
 * server.js — the ARRBY backend. One process, three jobs:
 *
 *   1. Run a ScannerManager with one ArbySignal per configured chain
 *      (real cross-chain monitoring — see scanner-manager.js's header for
 *      exactly what "cross-chain" does and doesn't mean here).
 *   2. Run LiquidationScanner passes over a watchlist, per chain.
 *   3. Broadcast all of it over WebSocket to any connected frontend
 *      (the ARRBY console's Cross-Chain Monitor panel), and expose a REST
 *      snapshot for anything that just wants a poll instead of a socket
 *      (mindX, a cron job, curl).
 *
 * Run:
 *   cd backend && npm install
 *   cp .env.example .env && cp watch-config.example.json watch-config.json
 *   # edit watch-config.json with your RPCs/tokens/routers
 *   npm start
 *
 * This process never holds a signing key by default — it only reads
 * (getAmountsOut, getUserAccountData, DefiLlama-equivalent quotes). Signing
 * and execution stay in signal_runner.js / the browser wallet — same
 * separation-of-detection-from-action principle as v1.3.
 */
const express = require('express');
const { WebSocketServer } = require('ws');
const { ethers } = require('ethers');
const { ScannerManager } = require('./scanner-manager.js');
const { LiquidationScanner } = require('./liquidation-scanner.js');
const chains = require('../../config/chains.json');

const PORT = process.env.PORT || 8787;
const app = express();
app.use(express.json());

// ---- 1. load per-chain scan config ----
// watch-config.json shape: { "<chainKey>": { rpcUrl, tokenA, tokenB, decA, decB,
//   routers:[{label,addr}], notional, thresholdBps, confirmations, intervalSec,
//   watchlist:["0x…borrower", ...], warnHealthFactor } }
let watchConfig = {};
try {
  watchConfig = process.env.WATCH_CONFIG_JSON ? JSON.parse(process.env.WATCH_CONFIG_JSON) : require('../watch-config.json');
} catch (e) {
  console.warn('[server] no watch-config.json / WATCH_CONFIG_JSON — starting with zero chains. POST /chains to add one at runtime.');
}

const manager = new ScannerManager();
const liquidationScanners = new Map();
let lastLiquidations = {};

function wireChain(chainKey, cfg) {
  const chainMeta = { chainId: cfg.chainId || (chains[chainKey] || {}).chainId, rpcUrl: cfg.rpcUrl };
  manager.addChain(chainKey, chainMeta, cfg);

  const aavePool = (chains[chainKey] || {}).aavePool;
  if (aavePool && cfg.watchlist?.length) {
    const provider = new ethers.JsonRpcProvider(cfg.rpcUrl);
    liquidationScanners.set(chainKey, new LiquidationScanner(provider, aavePool, cfg.watchlist, cfg.warnHealthFactor || 1.05));
  }
}

for (const [chainKey, cfg] of Object.entries(watchConfig)) wireChain(chainKey, cfg);
manager.startAll();

async function runLiquidationPass() {
  const out = {};
  for (const [chainKey, scanner] of liquidationScanners.entries()) {
    try { out[chainKey] = await scanner.scan(); }
    catch (e) { out[chainKey] = { error: e.message }; }
  }
  lastLiquidations = out;
  broadcast({ type: 'liquidations', data: out });
}
setInterval(runLiquidationPass, 60000);

// ---- 2. WebSocket broadcast ----
const server = app.listen(PORT, () => console.log(`ARRBY backend listening on :${PORT}`));
const wss = new WebSocketServer({ server, path: '/ws' });

function broadcast(msg) {
  const payload = JSON.stringify(msg);
  for (const client of wss.clients) {
    if (client.readyState === 1) client.send(payload);
  }
}

runLiquidationPass(); // now safe: broadcast() and wss both exist

manager.on('tick', row => broadcast({ type: 'tick', data: row }));
manager.on('leaderboard', rows => broadcast({ type: 'leaderboard', data: rows }));
manager.on('signal', s => broadcast({ type: 'signal', data: s }));
manager.on('clear', c => broadcast({ type: 'clear', data: c }));
manager.on('error', e => broadcast({ type: 'error', data: e }));

wss.on('connection', (ws) => {
  ws.send(JSON.stringify({ type: 'leaderboard', data: manager.leaderboard() }));
  ws.send(JSON.stringify({ type: 'liquidations', data: lastLiquidations }));
});

// ---- 3. REST surface ----
app.get('/health', (_req, res) => res.json({ ok: true, chains: [...manager.scanners.keys()] }));
app.get('/leaderboard', (_req, res) => res.json(manager.leaderboard()));
app.get('/liquidations', (_req, res) => res.json(lastLiquidations));
app.get('/chains', (_req, res) => res.json([...manager.scanners.keys()]));

// Reads on-chain treasury state for any chain whose watch-config includes an
// `arrbyAddress` and `tokenA`. Pure read; no keys. Optional feature — chains
// without arrbyAddress simply don't appear.
const ARRBY_TREASURY_ABI = [
  'function cumulativeProfit(address) view returns (uint256)',
  'function treasury() view returns (address)',
  'function treasuryBps() view returns (uint16)'
];
const ERC20_BAL = ['function balanceOf(address) view returns (uint256)'];
app.get('/treasury', async (_req, res) => {
  const out = {};
  for (const [chainKey, cfg] of Object.entries(watchConfig)) {
    if (!cfg.arrbyAddress || !cfg.tokenA) continue;
    try {
      const provider = new ethers.JsonRpcProvider(cfg.rpcUrl);
      const c = new ethers.Contract(cfg.arrbyAddress, ARRBY_TREASURY_ABI, provider);
      const t = new ethers.Contract(cfg.tokenA, ERC20_BAL, provider);
      const [ben, bps, cum, bal] = await Promise.all([
        c.treasury(), c.treasuryBps(), c.cumulativeProfit(cfg.tokenA), t.balanceOf(cfg.arrbyAddress)
      ]);
      out[chainKey] = {
        arrbyAddress: cfg.arrbyAddress, asset: cfg.tokenA,
        treasury: ben, treasuryBps: Number(bps),
        cumulativeProfit: cum.toString(), contractBalance: bal.toString()
      };
    } catch (e) { out[chainKey] = { error: e.shortMessage || e.message }; }
  }
  res.json(out);
});

app.post('/chains', (req, res) => {
  const { chainKey, ...cfg } = req.body || {};
  if (!chainKey || !cfg.rpcUrl || !cfg.tokenA || !cfg.tokenB || !cfg.routers) {
    return res.status(400).json({ error: 'need chainKey, rpcUrl, tokenA, tokenB, routers[]' });
  }
  wireChain(chainKey, cfg);
  manager.scanners.get(chainKey).signal.start();
  res.json({ ok: true, chainKey });
});

app.delete('/chains/:chainKey', (req, res) => {
  manager.removeChain(req.params.chainKey);
  liquidationScanners.delete(req.params.chainKey);
  res.json({ ok: true });
});

process.on('SIGINT', () => { manager.stopAll(); process.exit(0); });
