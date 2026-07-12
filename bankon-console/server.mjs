// server.mjs — BANKON Console: read-only tabbed diagnostics over the background
// Bitcoin Core node(s). Serves the UI, proxies a categorized WHITELIST of
// read-only RPCs, and exposes that catalog so the UI can express every aspect
// of CLI interaction. No spends / no stop / no config writes — safe by design.
import express from 'express';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { readFileSync, existsSync, readdirSync, writeFileSync, mkdirSync, rmSync, statSync, realpathSync } from 'node:fs';
import * as fsp from 'node:fs/promises';
import { homedir, cpus as oscpus, loadavg, totalmem, freemem } from 'node:os';
import { spawn, execFile, execSync } from 'node:child_process';
import net from 'node:net';
import { promises as dnsp } from 'node:dns';
import { apiAuth, rateLimit } from '../shared/security.mjs';
import { createChainExporter } from './chain-exporter.mjs';

// RESILIENCE: a diagnostics server must survive the node being down. A background warmer's rpc()
// (fetch) rejects with ECONNREFUSED when bitcoind is off; without this guard that stray rejection
// would crash the whole Console. Log and keep serving (stale cache + "node down" status).
process.on('unhandledRejection', (e) => console.error('[console] unhandledRejection:', (e && e.message) || e));
process.on('uncaughtException', (e) => console.error('[console] uncaughtException:', (e && e.message) || e));

const __dir = dirname(fileURLToPath(import.meta.url));
const app = express();
app.set('trust proxy', true);
app.use(express.json({ limit: '32kb' }));
app.use(express.static(join(__dir, 'public')));
app.use(rateLimit());
app.use('/api', apiAuth());           // off unless BANKON_API_TOKEN is set
const PORT = process.env.BANKON_CONSOLE_PORT || 8090;

const NODES = {
  full:   { url: 'http://127.0.0.1:8332', cookie: `${homedir()}/.bitcoin/.cookie` },
  pruned: { url: 'http://127.0.0.1:8342', cookie: process.env.BANKON_PRUNED_COOKIE
            || '/media/luvai/2c0ab2e0-1e83-4721-bde5-57afe1bcd4e2/home/tank/.bitcoin-pruned/.cookie' },
};

// Categorized read-only catalog. {m:method, d:description, heavy?, w?:wallet-scoped, ex?:example params}
const CATALOG = {
  Blockchain: [
    { m:'getblockchaininfo', d:'Chain state: height, headers, sync %, size, pruning.' },
    { m:'getblockcount', d:'Height of the most-work fully-validated chain.' },
    { m:'getbestblockhash', d:'Hash of the tip block.' },
    { m:'getblockhash', d:'Block hash at a height.', ex:[800000] },
    { m:'getblockheader', d:'Header fields for a block hash.', ex:['<blockhash>'] },
    { m:'getblock', d:'Full block (verbosity 1/2/3).', ex:['<blockhash>',1] },
    { m:'getblockstats', d:'Per-block stats: fees, size, feerate percentiles.', ex:[800000] },
    { m:'getchaintips', d:'Known chain tips (forks/orphans).' },
    { m:'getchaintxstats', d:'Tx count + rate over a window.', ex:[2016] },
    { m:'getdifficulty', d:'Current proof-of-work difficulty.' },
    { m:'getdeploymentinfo', d:'Soft-fork / BIP9 deployment status.' },
    { m:'getindexinfo', d:'Optional index sync state (txindex, etc.).' },
    { m:'gettxoutsetinfo', d:'UTXO-set audit: count, supply, set hash.', heavy:true },
    { m:'gettxout', d:'Unspent output (UTXO) by txid + vout.', ex:['<txid>',0] },
    { m:'getblockfilter', d:'Compact block filter for a block (needs blockfilterindex).', ex:['<blockhash>'] },
    { m:'getchainstates', d:'Active + (if any) snapshot chainstate detail.' },
    { m:'verifychain', d:'Re-verify the last N blocks at a level.', heavy:true, ex:[3,144] },
  ],
  Transactions: [
    { m:'getrawtransaction', d:'Raw tx by txid (true = decoded; non-wallet needs txindex).', ex:['<txid>',true] },
    { m:'decoderawtransaction', d:'Decode a raw transaction hex to JSON.', ex:['<hex>'] },
    { m:'gettxoutproof', d:'Merkle proof that txid(s) are in a block.', ex:[['<txid>']] },
    { m:'verifytxoutproof', d:'Verify a tx-out proof; returns the txids.', ex:['<proof-hex>'] },
    { m:'decodepsbt', d:'Decode a PSBT (base64) to JSON.', ex:['<psbt>'] },
    { m:'analyzepsbt', d:'Analyze a PSBT: next role, fee, completeness.', ex:['<psbt>'] },
  ],
  Mempool: [
    { m:'getmempoolinfo', d:'Mempool size, bytes, memory, min relay fee.' },
    { m:'getrawmempool', d:'All mempool txids (true = with fee/size).', ex:[true] },
    { m:'getmempoolentry', d:'Mempool details for one txid.', ex:['<txid>'] },
    { m:'getmempoolancestors', d:'In-mempool ancestors of a txid.', ex:['<txid>'] },
    { m:'getmempooldescendants', d:'In-mempool descendants of a txid.', ex:['<txid>'] },
    { m:'estimatesmartfee', d:'Fee estimate (sat/vB) for N-block target.', ex:[6] },
  ],
  Network: [
    { m:'getnetworkinfo', d:'Version, subversion, relay fee, reachable nets.' },
    { m:'getpeerinfo', d:'Per-peer detail: addr, subver, ping, bytes, height.' },
    { m:'getconnectioncount', d:'Number of connected peers.' },
    { m:'getnettotals', d:'Total bytes sent/received.' },
    { m:'getnodeaddresses', d:'Known peer addresses from addrman.', ex:[10] },
    { m:'listbanned', d:'Currently banned peers.' },
    { m:'getaddrmaninfo', d:'Address-manager bucket stats.' },
  ],
  Mining: [
    { m:'getmininginfo', d:'Difficulty, network hashrate, pooled tx.' },
    { m:'getnetworkhashps', d:'Estimated network hashes/sec.', ex:[120] },
  ],
  Wallet: [
    { m:'listwallets', d:'Currently loaded wallets.' },
    { m:'listwalletdir', d:'Wallets available on disk.' },
    { m:'getwalletinfo', d:'Balance, txcount, keypool, flags.', w:true },
    { m:'getbalances', d:'Trusted / pending / immature balances.', w:true },
    { m:'listtransactions', d:'Recent wallet transactions.', w:true, ex:['*',10] },
    { m:'listunspent', d:'Unspent outputs (UTXOs).', w:true },
    { m:'getaddressinfo', d:'Details for an address (ismine, type).', w:true, ex:['<addr>'] },
    { m:'listdescriptors', d:'Wallet descriptors (public).', w:true },
  ],
  Utility: [
    { m:'uptime', d:'Seconds the node has been running.' },
    { m:'getrpcinfo', d:'Active RPC commands + queue depth.' },
    { m:'getmemoryinfo', d:'Memory usage of the node.' },
    { m:'getzmqnotifications', d:'Active ZMQ publisher endpoints.' },
    { m:'help', d:'Help text for a command (the bitcoin-cli --help for that call).', ex:['getblock'] },
    { m:'getdescriptorinfo', d:'Canonicalize + checksum a descriptor.', ex:['<descriptor>'] },
    { m:'deriveaddresses', d:'Derive address(es) from an output descriptor.', ex:['<descriptor>'] },
    { m:'validateaddress', d:'Validate an address (no wallet needed).', ex:['<addr>'] },
    { m:'verifymessage', d:'Verify a signed message (address, signature, message).', ex:['<addr>','<sig>','<msg>'] },
    { m:'decodescript', d:'Decode a hex script.', ex:['<hex>'] },
    { m:'estimaterawfee', d:'Raw fee-estimate detail for a target.', ex:[6] },
  ],
};
const META = {}; const ALLOW = new Set();
for (const [cat, list] of Object.entries(CATALOG)) for (const e of list) { ALLOW.add(e.m); META[e.m] = { ...e, cat }; }

function authHeader(cookiePath) {
  let cred;
  try { cred = readFileSync(cookiePath, 'utf8').trim(); }
  catch { cred = `${process.env.RPC_USER || 'bitcoinrpc'}:${process.env.RPC_PASS || ''}`; }
  return 'Basic ' + Buffer.from(cred).toString('base64');
}
// Concurrency limiter — never more than RPC_MAX_INFLIGHT live calls to the node at once,
// so the Console can NEVER overflow bitcoind's work queue (the root cause of the IBD
// "http work queue depth exceeded" flood). When saturated, _acquire rejects → callers
// serve last-known cache instead of piling more doomed calls onto the node.
// Runtime-tunable RPC policy (see GET/POST /api/settings).
// rageRPC profiles — accelerated (max throughput, safe because rpcworkqueue=256 absorbs bursts and
// the circuit breaker backstops genuine overload) vs gentle.
const RAGE_PROFILES = {
  on:  { maxInflight: 32, distressMs: 3000 },   // MAX throughput — push hardest; the adaptive cap + breaker keep it safe
  off: { maxInflight: 4,  distressMs: 12000 },  // gentle
};
const RPC = {
  maxInflight: Number(process.env.BANKON_RPC_MAX_INFLIGHT) || 256,  // default tier: 256 (max); adaptive keeps it safe
  timeoutMs: Number(process.env.RPC_TIMEOUT_MS) || 45000,           // per-call timeout
  distressMs: Number(process.env.BANKON_RPC_DISTRESS_MS) || 3000,
  rage: false,                                                      // RAGE (uncapped) is opt-in via the controller
};
let _inflight = 0; const _waiters = [];
let _distressUntil = 0;                                            // circuit breaker: node overwhelmed until this time
let _tripCount = 0;                                               // consecutive-trip escalation (exponential backoff)
let _effInflight = RPC.maxInflight;                               // ADAPTIVE cap — backs off under distress, ramps up when healthy
let _okStreak = 0;
const RPC_STATS = { calls: 0, errors: 0, trips: 0, timeouts: 0, coalesced: 0, servedStale: 0 };  // observability
const RECOVER_EVERY = Number(process.env.BANKON_RPC_RECOVER_EVERY) || 8;   // AIMD: +1 cap per N OKs (was 25 — faster, still stable)
const _effCap = () => Math.min(_effInflight, RPC.maxInflight);    // never exceed the configured ceiling
function _acquire() {
  if (_inflight < _effCap()) { _inflight++; return Promise.resolve(); }
  if (_waiters.length >= 48) return Promise.reject(new Error('rpc busy (node saturated)'));
  return new Promise(resolve => _waiters.push(resolve));
}
function _release() {
  const next = _waiters.shift();
  if (next) next(); else _inflight--;
}
const rpcCircuitOpen = () => Date.now() < _distressUntil;
// Trip the breaker with EXPONENTIAL backoff + jitter (escalates on repeated trips, capped 60s) and a
// multiplicative cap cut. Jitter staggers the herd when the window expires so it doesn't re-trip at once.
function _trip() {
  RPC_STATS.trips++;
  _tripCount = Math.min(_tripCount + 1, 6);
  const base = RPC.distressMs * Math.pow(1.8, _tripCount - 1);
  _distressUntil = Date.now() + Math.min(base + base * 0.25 * Math.random(), 60000);
  _effInflight = Math.max(2, Math.floor(_effInflight / 4));       // multiplicative decrease
  _okStreak = 0;
}
// AIMD additive increase + heal the trip-escalation when the node is sustained-healthy.
function _healthyTick() {
  if (++_okStreak >= RECOVER_EVERY) {
    _okStreak = 0;
    if (_tripCount > 0) _tripCount--;                             // decay escalation as health persists
    if (_effInflight < RPC.maxInflight) _effInflight++;           // additive increase (stable)
  }
}

async function rpc(node, method, params = [], wallet = null, timeoutMs = null) {
  // CIRCUIT BREAKER: while open, don't call the node — callers serve cache. This drains the node's
  // queue and stops the flood at the source. When the window expires, the next calls are the probes
  // (half-open): a success heals via _healthyTick, another 500/timeout re-trips with escalated backoff.
  if (rpcCircuitOpen()) throw new Error('node busy (circuit breaker open)');
  await _acquire();
  RPC_STATS.calls++;
  try {
    const n = NODES[node] || NODES.full;
    const url = wallet ? `${n.url}/wallet/${encodeURIComponent(wallet)}` : n.url;
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'content-type': 'application/json', authorization: authHeader(n.cookie) },
      body: JSON.stringify({ jsonrpc: '1.0', id: 'console', method, params }),
      signal: AbortSignal.timeout(timeoutMs || RPC.timeoutMs),
    });
    const text = await res.text();
    if (res.status >= 500 || /work queue depth exceeded/i.test(text)) {
      _trip(); RPC_STATS.errors++;
      throw new Error(`node overwhelmed (${res.status}) — backing off ${Math.round((_distressUntil - Date.now()) / 1000)}s`);
    }
    let json; try { json = JSON.parse(text); } catch { RPC_STATS.errors++; throw new Error(`non-JSON (${res.status}): ${text.slice(0,160)}`); }
    if (json.error) { RPC_STATS.errors++; throw new Error(`${json.error.message} (code ${json.error.code})`); }
    _healthyTick();                                               // sustained health → grow cap, heal escalation
    return json.result;
  } catch (e) {
    // Treat a TIMEOUT as SOFT distress: the node is slow (not 500-ing) — back off the cap gently and
    // reset the streak so we stop piling on, without the full multiplicative /4 of a hard 500.
    if (e && (e.name === 'TimeoutError' || e.name === 'AbortError')) {
      RPC_STATS.timeouts++; _okStreak = 0; _effInflight = Math.max(2, Math.floor(_effInflight * 0.75));
    }
    throw e;
  } finally {
    _release();
  }
}

// ---- result cache so the dashboard always shows last-known data -------------
// During IBD the node's RPC is lock-bound; a fresh call may time out. We cache
// every successful read and serve the last-known value (flagged stale) on miss,
// and a background loop keeps the common metrics warm by catching lock gaps.
const CACHE = new Map();   // key -> { value, ts }
const COALESCE = new Map(); // key -> in-flight Promise (single-flight: N identical concurrent calls → 1 node call)
const ckey = (node, method, params, wallet) => `${node}|${method}|${JSON.stringify(params)}|${wallet || ''}`;
async function rpcCached(node, method, params = [], wallet = null, timeoutMs = null) {
  const k = ckey(node, method, params, wallet);
  // REQUEST COALESCING: if an identical call is already in flight, ride it instead of hitting the
  // node again — the dashboard has many widgets polling the same method; this collapses the flood.
  let p = COALESCE.get(k);
  if (p) RPC_STATS.coalesced++;
  else {
    p = rpc(node, method, params, wallet, timeoutMs);
    COALESCE.set(k, p);
    p.finally(() => { if (COALESCE.get(k) === p) COALESCE.delete(k); });
  }
  try {
    const value = await p;
    CACHE.set(k, { value, ts: Date.now() });
    return { value, stale: false, asOf: Date.now() };
  } catch (e) {
    const c = CACHE.get(k);
    if (c) { RPC_STATS.servedStale++; return { value: c.value, stale: true, asOf: c.ts, error: String(e.message || e) }; }
    throw e;
  }
}

// Debounced background refresh — caps node load to ONE refresh per key per BG_MIN_MS
// regardless of how fast the UIs poll cache-hit endpoints. This is what prevents the
// "http work queue depth exceeded" flood: serve-cache-first must NOT fire a node call
// on every request. The warmers + this keep the cache fresh without piling up RPC.
const BG_MIN_MS = Number(process.env.BANKON_BG_MIN_MS) || 12000;
const BG_AT = new Map();           // key -> last bg-refresh start ts
const BG_INFLIGHT = new Set();
function bgRefresh(node, method, params, wallet, k) {
  const now = Date.now();
  if (BG_INFLIGHT.has(k)) return;
  if (now - (BG_AT.get(k) || 0) < BG_MIN_MS) return;
  BG_AT.set(k, now); BG_INFLIGHT.add(k);
  rpc(node, method, params, wallet, 12000)
    .then(v => CACHE.set(k, { value: v, ts: Date.now() }))
    .catch(() => {})
    .finally(() => BG_INFLIGHT.delete(k));
}

app.get('/api/catalog', (req, res) => res.json({ catalog: CATALOG }));
app.get('/api/nodes', (req, res) => res.json({ nodes: Object.keys(NODES) }));

app.post('/api/rpc', async (req, res) => {
  const { node = 'full', method, params = [], wallet = null } = req.body || {};
  if (!ALLOW.has(method)) return res.status(403).json({ error: `'${method}' is not in the read-only whitelist` });
  const k = ckey(node, method, params, wallet), c = CACHE.get(k);
  if (c) {   // serve cached INSTANTLY (don't wait for a live call that may be doomed), refresh in bg
    res.json({ ok: true, result: c.value, stale: true, asOf: c.ts });
    bgRefresh(node, method, params, wallet, k);
    return;
  }
  try {
    const r = await rpcCached(node, method, params, wallet, Number(process.env.RPC_UI_TIMEOUT_MS) || 12000);
    res.json({ ok: true, result: r.value, stale: r.stale, asOf: r.asOf });
  } catch (e) { res.json({ ok: false, error: String(e.message || e) }); }
});

// Batched overview — fewer round-trips for the dashboard's basic view.
app.get('/api/overview', (req, res) => {
  const node = req.query.node || 'full';
  const out = {}, stale = {}; let asOf = 0;
  // NON-BLOCKING: serve whatever's cached INSTANTLY and trigger a debounced background refresh.
  // Never awaits the node, so it can't hang during the IBD RPC choke (was up to 9×8s before).
  const grab = (k, m, p = []) => {
    const ck = ckey(node, m, p, null), c = CACHE.get(ck);
    if (c) { out[k] = c.value; stale[k] = c.ts; asOf = Math.max(asOf, c.ts); }
    else out[k] = null;
    bgRefresh(node, m, p, null, ck);
  };
  for (const [k, m] of [['chain', 'getblockchaininfo'], ['net', 'getnetworkinfo'],
    ['mempool', 'getmempoolinfo'], ['index', 'getindexinfo'], ['peers', 'getpeerinfo'],
    ['wallets', 'listwallets'], ['uptime', 'uptime'], ['nettotals', 'getnettotals'],
    ['mining', 'getmininginfo']]) { grab(k, m); }
  // Log fallback (node-RPC-independent): if the chain cache is cold during the IBD choke, derive
  // height/progress from debug.log so the browser Overview shows live sync data like the Qt does.
  if (!out.chain) {
    try {
      const line = execSync(`grep -a UpdateTip ${DEBUG_LOG} 2>/dev/null | tail -1`,
        { timeout: 4000, maxBuffer: 1 << 20 }).toString();
      const h = /height=(\d+)/.exec(line), pr = /progress=([0-9.]+)/.exec(line);
      if (h) out.chain = { blocks: +h[1], headers: +h[1], verificationprogress: pr ? +pr[1] : null,
        initialblockdownload: (pr ? +pr[1] : 0) < 0.9999, _fromLog: true };
    } catch {}
  }
  res.json({ ok: true, node, result: out, stale, asOf });
});

// ---- node recognition / control / live boot log -----------------------------
const BTC_BIN = process.env.BANKON_BTC_BIN || `${homedir()}/bitcoin-31.0/bin`;
const BTC_DATADIR = process.env.BANKON_BTC_DATADIR || `${homedir()}/.bitcoin`;
const NODE_CONTROL = process.env.BANKON_NODE_CONTROL !== '0';   // default ON (localhost)
const DEBUG_LOG = `${BTC_DATADIR}/debug.log`;

// Fast health probe — recognizes a running node on the standard port and reports
// state: running | booting (warmup) | busy (validating, RPC lock) | down.
app.get('/api/health', async (req, res) => {
  const node = req.query.node || 'full';
  const n = NODES[node] || NODES.full;
  const port = n.url.split(':').pop();
  try {
    const blocks = await rpc(node, 'getblockcount', [], null, 6000);
    let chain = {};
    try { chain = await rpc(node, 'getblockchaininfo', [], null, 6000); } catch {}
    return res.json({ ok: true, state: 'running', port, blocks,
      headers: chain.headers ?? null, progress: chain.verificationprogress ?? null,
      ibd: chain.initialblockdownload ?? null, control: NODE_CONTROL });
  } catch (e) {
    const msg = String(e.message || e);
    let state = 'busy';
    if (/code -28|warm|loading block|verifying|rewind/i.test(msg)) state = 'booting';
    else if (/refus|ECONNREF|fetch failed|ENOTFOUND|connect/i.test(msg)) state = 'down';
    else if (/timeout|abort/i.test(msg)) state = 'busy';
    return res.json({ ok: true, state, port, error: msg, control: NODE_CONTROL });
  }
});

// Live boot/sync log — tail of debug.log (init, warmup, UpdateTip, peer events).
app.get('/api/node/log', (req, res) => {
  const lines = Math.min(Number(req.query.lines) || 80, 400);
  if (!existsSync(DEBUG_LOG)) return res.json({ ok: false, error: 'no debug.log yet' });
  execFile('tail', ['-n', String(lines), DEBUG_LOG], { maxBuffer: 8 * 1024 * 1024 }, (err, out) => {
    if (err) return res.json({ ok: false, error: String(err.message) });
    res.json({ ok: true, lines: out.split('\n').filter(Boolean) });
  });
});

// Node control — start/stop the local Bitcoin Core. Off if BANKON_NODE_CONTROL=0.
app.post('/api/node/start', (req, res) => {
  if (!NODE_CONTROL) return res.status(403).json({ ok: false, error: 'node control disabled' });
  const bin = `${BTC_BIN}/bitcoind`;
  if (!existsSync(bin)) return res.json({ ok: false, error: `bitcoind not found at ${bin}` });
  try {
    spawn(bin, [`-datadir=${BTC_DATADIR}`, '-daemon'], { detached: true, stdio: 'ignore' }).unref();
    res.json({ ok: true, started: true, note: 'bitcoind launching — watch the Node tab log' });
  } catch (e) { res.json({ ok: false, error: String(e.message) }); }
});
app.post('/api/node/stop', (req, res) => {
  if (!NODE_CONTROL) return res.status(403).json({ ok: false, error: 'node control disabled' });
  execFile(`${BTC_BIN}/bitcoin-cli`, [`-datadir=${BTC_DATADIR}`, 'stop'], (err, out) => {
    if (err) return res.json({ ok: false, error: String(err.message) });
    res.json({ ok: true, stopping: true, message: (out || '').trim() });
  });
});
// AIRGAP switch — setnetworkactive on/off. Takes the machine's Bitcoin network dark so the WaaS
// can generate wallet keys with zero P2P traffic, then re-enables. Write RPC → guarded. No user
// input is interpolated: `on` is coerced to a strict boolean before it reaches bitcoin-cli.
app.post('/api/node/netactive', async (req, res) => {
  if (!NODE_CONTROL) return res.status(403).json({ ok: false, error: 'node control disabled' });
  const on = req.body?.on === true;                        // strict: anything else means OFF was asked
  if (typeof req.body?.on !== 'boolean') return res.status(400).json({ ok: false, error: 'need {on: true|false}' });
  try {
    const state = await rpc('full', 'setnetworkactive', [on], null, 8000);   // returns the new state
    res.json({ ok: true, networkactive: typeof state === 'boolean' ? state : on, requested: on });
  } catch (e) { res.json({ ok: false, error: String(e.message || e) }); }
});
// Add / force / drop a peer (the "node chooser"). addnode is a write RPC → guarded like start/stop.
app.post('/api/node/addnode', async (req, res) => {
  if (!NODE_CONTROL) return res.status(403).json({ ok: false, error: 'node control disabled' });
  const { addr, command = 'add' } = req.body || {};
  if (!addr) return res.status(400).json({ ok: false, error: 'need {addr}' });
  if (!['add', 'remove', 'onetry'].includes(command)) return res.status(400).json({ ok: false, error: "command must be add|remove|onetry" });
  try { await rpc('full', 'addnode', [addr, command], null, 8000); res.json({ ok: true, addr, command }); }
  catch (e) { res.json({ ok: false, error: String(e.message || e) }); }
});
// Status of manually-added nodes (getaddednodeinfo) — cheap, served live.
app.get('/api/node/addednodes', async (req, res) => {
  try { res.json({ ok: true, nodes: await rpc('full', 'getaddednodeinfo', [], null, 8000) }); }
  catch (e) { res.json({ ok: false, error: String(e.message || e), nodes: [] }); }
});

// ---- node-load policy ----------------------------------------------------
// GENTLE by default (never hammers the node). The "node hammer" is a BURST that
// accumulates block-level data; it runs ONLY on demand (when the Blocks view is
// opened and the cache is stale) or continuously if BANKON_NODE_HAMMER=1.
// Short timeouts everywhere → blocked cs_main calls give up fast and free the
// RPC thread, so we never fill the work queue ("Work queue depth exceeded").
const HAMMER = process.env.BANKON_NODE_HAMMER === '1';
const T = Number(process.env.BANKON_RPC_TIMEOUT_MS) || 8000;   // short — frees threads fast

// Two guarded loops, each ≤1 concurrent RPC (so ≤2 total → never floods the queue):
//  • CHEAP: non-cs_main metrics, short timeout, fast & reliable every 25s.
//  • CHAIN: cs_main metrics, LONG timeout so a single patient call waits through a
//    validation and catches the gap (one held thread is safe; the flood was concurrency).
// CHEAP = no cs_main needed (always fast/reliable). CHAIN = touches cs_main → patient.
const CHEAP = [['getnetworkinfo',[]],['getnettotals',[]],['getindexinfo',[]]];
const CHAIN = [['getblockchaininfo',[]],['getchaintxstats',[2016]],['getmininginfo',[]],['getmempoolinfo',[]],['getpeerinfo',[]]];
const CHAIN_T = Number(process.env.BANKON_CHAIN_TIMEOUT_MS) || 90000;
let warmingCheap = false, warmingChain = false;
async function warmCheap() {
  if (warmingCheap) return; warmingCheap = true;
  try { for (const [m, p] of CHEAP) { try { await rpcCached('full', m, p, null, T); } catch {} } }
  finally { warmingCheap = false; }
}
async function warmChain() {
  if (warmingChain) return; warmingChain = true;   // patient, single-threaded → catches cs_main gaps
  try { for (const [m, p] of CHAIN) { try { await rpcCached('full', m, p, null, CHAIN_T); } catch {} } }
  finally { warmingChain = false; }
}

// BLOCK FEED — an ACCUMULATING, tip-current list of recent block headers. Each burst takes the
// freshest tip (getbestblockhash) and walks back only as far as needed to prepend blocks we don't
// already have, so the list grows and always tracks Bitcoin Core's tip. Guarded; per-burst budget.
let RECENT_BLOCKS = [], building = false, lastBurst = 0;
const NTX = new Map();   // height -> nTx, accumulated from cheap getblockheader calls (persists across requests)
const RECENT_MAX = Number(process.env.BANKON_RECENT_MAX) || 200;   // how many to retain
const RECENT_BUDGET = Number(process.env.BANKON_RECENT_BUDGET) || 60;  // max new blocks fetched per burst
async function burstBlocks() {
  if (building) return;
  building = true; lastBurst = Date.now();
  try {
    let hash; try { hash = (await rpcCached('full','getbestblockhash',[],null,CHAIN_T)).value; } catch { return; }
    if (!hash) return;
    const haveTop = RECENT_BLOCKS.length ? RECENT_BLOCKS[0].height : -1;
    const fresh = [];
    for (let i = 0; i < RECENT_BUDGET && hash; i++) {
      try {
        const b = (await rpcCached('full','getblockheader',[hash],null,CHAIN_T)).value;
        if (b.height <= haveTop) break;            // reconnected to blocks we already hold → stop
        fresh.push({ height: b.height, hash, time: b.time, nTx: b.nTx });
        hash = b.previousblockhash;
      } catch { break; }
    }
    if (fresh.length) RECENT_BLOCKS = fresh.concat(RECENT_BLOCKS).slice(0, RECENT_MAX);  // prepend + cap
  } finally { building = false; }
}
app.get('/api/recentblocks', async (req, res) => {
  // IBD-proof + always current: read the last N UpdateTip lines from debug.log (the node writes
  // hash+height+date on every block it connects — no RPC, no cs_main). Per-block nTx is then
  // enriched from a cheap getblockheader (which DOES return nTx), bounded + circuit-safe so it
  // never hammers the node during IBD; results accumulate in NTX so repeat requests are instant.
  const n = Math.max(1, Math.min(Number(req.query.n) || 50, RECENT_MAX));
  const priorNtx = new Map(RECENT_BLOCKS.map(b => [b.height, b.nTx]));   // any earlier burst results
  let out = '';
  try { out = execSync(`grep -a UpdateTip ${DEBUG_LOG} 2>/dev/null | tail -${n}`,
                       { timeout: 6000, maxBuffer: 1 << 22 }).toString(); } catch { /* log unreadable */ }
  const blocks = [];
  for (const line of out.trim().split('\n')) {
    if (!line) continue;
    const mh = /best=([0-9a-f]+)/.exec(line), mn = /height=(\d+)/.exec(line), md = /date='([^']+)'/.exec(line);
    if (!mh || !mn) continue;
    const height = +mn[1];
    const cached = NTX.has(height) ? NTX.get(height) : (priorNtx.has(height) ? priorNtx.get(height) : null);
    blocks.push({ height, hash: mh[1], time: md ? Math.floor(Date.parse(md[1]) / 1000) : null, nTx: cached });
  }
  blocks.reverse();   // newest first
  if (!blocks.length && RECENT_BLOCKS.length)          // fallback to RPC burst cache if log unreadable
    return res.json({ ok: true, blocks: RECENT_BLOCKS.slice(0, n), building, asOf: lastBurst, total: RECENT_BLOCKS.length, source: 'rpc' });
  // fill in missing tx counts for the visible window (cheap header call, capped per request)
  let budget = 24, enriched = 0;
  for (const b of blocks) {
    if (b.nTx != null || budget <= 0) continue;
    try {
      const hdr = (await rpcCached('full', 'getblockheader', [b.hash], null, CHAIN_T)).value;
      budget--;
      if (hdr && typeof hdr.nTx === 'number') { b.nTx = hdr.nTx; NTX.set(b.height, hdr.nTx); enriched++; }
    } catch { break; }   // RPC busy (IBD / circuit open) — stop and serve what we have
  }
  if (NTX.size > 5000) for (const k of [...NTX.keys()].slice(0, NTX.size - 5000)) NTX.delete(k);
  res.json({ ok: true, blocks, building: false, asOf: Date.now(), total: blocks.length,
             source: 'debug.log', enriched });
});

// BTC.oracle — "keeps the time on a Bitcoin Block". The genesis block starts the
// clock; average block time = elapsed / blocks. From that we derive a sane
// bandwidth/poll throttle (no point polling for new blocks far faster than they
// arrive). All from cached values — no extra node load.
const GENESIS_TIME = 1231006505;   // block 0, 2009-01-03 18:15:05 UTC
app.get('/api/oracle', (req, res) => {
  // read cache directly — instant, never blocks on a live call
  const ci = (CACHE.get(ckey('full', 'getblockchaininfo', [], null)) || {}).value || null;
  const cts = (CACHE.get(ckey('full', 'getchaintxstats', [2016], null)) || {}).value || null;
  const height = ci?.blocks || 0;
  const tipTime = ci?.mediantime || ci?.time || 0;
  // first block starts the clock → all-time average interval
  const avgAll = (height > 0 && tipTime > GENESIS_TIME) ? (tipTime - GENESIS_TIME) / height : null;
  // recent window average (last ~2016 blocks)
  const avgWin = (cts?.window_interval && cts?.window_block_count) ? cts.window_interval / cts.window_block_count : null;
  const target = 600;                                   // 10-min protocol target
  const basis = avgWin || avgAll || target;             // seconds/block
  // throttle: poll for new blocks at ~¼ of a block interval, clamped 15s–2min
  const recommendedPollMs = Math.round(Math.max(15000, Math.min(120000, basis * 1000 / 4)));
  res.json({ ok: true, oracle: {
    genesisTime: GENESIS_TIME, height, tipTime,
    avgBlockTimeAllTime: avgAll, avgBlockTimeWindow: avgWin, targetBlockTime: target,
    basisSeconds: basis, recommendedPollMs,
  }});
});

// Actual sync from debug.log — the node writes UpdateTip with the real height +
// verificationprogress on every block, with NO RPC/cs_main needed. So this is the
// reliable live sync source even while RPC is saturated by validation.
app.get('/api/synctip', (req, res) => {
  execFile('bash', ['-c', `grep -a UpdateTip ${DEBUG_LOG} 2>/dev/null | tail -1`],
    { timeout: 6000, maxBuffer: 1 << 20 }, (err, out) => {
      const line = (out || '').trim();
      const g = re => { const m = re.exec(line); return m ? m[1] : null; };
      res.json({ ok: !!line,
        height: g(/height=(\d+)/) ? +g(/height=(\d+)/) : null,
        progress: g(/progress=([0-9.]+)/) ? +g(/progress=([0-9.]+)/) : null,
        tx: g(/ tx=(\d+)/) ? +g(/ tx=(\d+)/) : null,        // cumulative tx count — delta = "crunching" load
        cache: g(/cache=([0-9.]+\w+)/),                     // coin cache size the node is chewing
        blockDate: g(/date='([^']+)'/), logTime: g(/^(\S+)/), raw: line });
    });
});

// Bitcoin-Core monitor — ON/OFF + connecting/feeding, derived from NON-RPC signals (TCP port +
// debug.log freshness) so it stays accurate even while the node's RPC is choked during IBD.
function portOpen(port, host = '127.0.0.1', timeout = 1200) {
  return new Promise(resolve => {
    const s = net.createConnection({ port: +port, host });
    let done = false;
    const fin = v => { if (!done) { done = true; s.destroy(); resolve(v); } };
    s.setTimeout(timeout);
    s.once('connect', () => fin(true));
    s.once('timeout', () => fin(false));
    s.once('error', () => fin(false));
  });
}
app.get('/api/coremon', async (req, res) => {
  const node = req.query.node || 'full';
  const n = NODES[node] || NODES.full;
  const port = n.url.split(':').pop();
  const up = await portOpen(port);
  let height = null, logAgeSec = null;
  try {
    const line = execSync(`grep -a UpdateTip ${DEBUG_LOG} 2>/dev/null | tail -1`,
      { timeout: 4000, maxBuffer: 1 << 20 }).toString().trim();
    const hm = /height=(\d+)/.exec(line); if (hm) height = +hm[1];
    const tm = /^(\S+)/.exec(line);
    if (tm) { const t = Date.parse(tm[1]); if (!Number.isNaN(t)) logAgeSec = Math.round((Date.now() - t) / 1000); }
  } catch {}
  // feeding/connecting = up AND the tip advanced recently (actively receiving + validating blocks)
  const feeding = up && logAgeSec != null && logAgeSec < 180;
  const state = !up ? 'off' : feeding ? 'feeding' : 'on';
  res.json({ ok: true, up, feeding, state, height, logAgeSec, port });
});

// Network activity — connection attempts / successes / failures + local addresses, parsed from
// debug.log so it works DURING the IBD RPC choke (when getpeerinfo can't complete). This is the
// node's live peer activity even when the peer table is unavailable.
// session-long peer-id → {addr, net, subver} cache. Peer ids are monotonic within a node
// session (never reused), so this safely backfills the address on a peer's later disconnect
// log line long after it left getpeerinfo. Bounded so it can't grow without limit.
const PEER_ADDR = new Map();
app.get('/api/netactivity', async (req, res) => {
  const n = Math.min(Number(req.query.n) || 40, 200);
  // peer-id → {addr, network, subver} from getpeerinfo, so we can backfill the address and
  // network on "peer connected"/"disconnecting" log lines (which carry only peer=N, no IP).
  // Circuit-breaker-safe: if RPC is choked during IBD we just skip the enrichment.
  const isoLocal = t => { try { return new Date(t * 1000).toISOString().replace('T', ' ').slice(0, 19); }
                          catch { return ''; } };
  let peerMap = {}, peerEvents = [];
  try {
    const pr = await rpcCached('full', 'getpeerinfo', [], null, 4000);
    const pi = Array.isArray(pr) ? pr : (pr && pr.value) || [];   // rpcCached wraps as {value,stale,asOf}
    for (const p of pi) {
      const rec = { addr: p.addr, network: p.network, subver: (p.subver || '').replace(/\//g, '') };
      peerMap[String(p.id)] = rec;
      if (p.addr) {                                   // remember for future disconnect backfill
        PEER_ADDR.set(String(p.id), rec);
        if (PEER_ADDR.size > 5000) PEER_ADDR.delete(PEER_ADDR.keys().next().value);
      }
      // every CURRENTLY-connected peer becomes a fully-populated "connected" row —
      // getpeerinfo has the address the log line never carries for outbound peers
      peerEvents.push({
        time: isoLocal(p.conntime), kind: p.inbound ? 'inbound' : 'connected', live: true,
        addr: p.addr || '', net: p.network || '', peer: String(p.id),
        conntype: p.connection_type || '', transport: p.transport_protocol_type || '',
        ver: String(p.version || ''), blocks: String(p.synced_blocks ?? p.startingheight ?? ''),
        subver: (p.subver || '').replace(/\//g, ''), reason: '',
        text: `live peer ${p.addr || ''} (${p.connection_type || ''})` });
    }
  } catch { /* RPC busy — live peer snapshot unavailable, fall back to log-only */ }
  try {
    const raw = execSync(
      `grep -aE 'peer connected|connect\\(\\) to|disconnecting|Added connection|accepted connection|AddLocal|Bound to|Adding fixed seeds|addresses from peers.dat|new outbound|socket (send|recv)' ${DEBUG_LOG} 2>/dev/null | tail -${n}`,
      { timeout: 5000, maxBuffer: 1 << 20 }).toString();
    const local = [];
    // classify an address into its network so the UI can tag ipv4 / ipv6 / tor / i2p / cjdns
    const netOf = a => {
      if (!a) return '';
      if (/\.onion/i.test(a)) return 'tor';
      if (/\.b32\.i2p/i.test(a)) return 'i2p';
      if (/^\[?fc/i.test(a)) return 'cjdns';
      if (/^\[/.test(a) || /(?:[0-9a-f]*:){3,}/i.test(a)) return 'ipv6';
      if (/(?:\d{1,3}\.){3}\d{1,3}/.test(a)) return 'ipv4';
      return '';
    };
    const events = raw.split('\n').filter(Boolean).map(line => {
      const time = (/^(\S+)/.exec(line) || [])[1] || '';
      const addr = (/((?:\d{1,3}\.){3}\d{1,3}(?::\d+)?|\[[0-9a-f:]+\](?::\d+)?|[a-z2-7]{16,60}\.onion(?::\d+)?)/i.exec(line) || [])[1] || '';
      const peer = (/peer=(\d+)/.exec(line) || [])[1] || '';
      let kind = 'info', reason = '';
      if (/new (outbound|inbound|manual|block-relay|feeler).*peer connected|peer connected/i.test(line)) kind = 'connected';
      else if (/connect\(\) to .* failed/i.test(line)) kind = 'failed';
      else if (/disconnecting|disconnected/i.test(line)) kind = 'disconnect';
      else if (/accepted connection/i.test(line)) kind = 'inbound';
      else if (/AddLocal|Bound to/i.test(line)) { kind = 'local'; if (addr) local.push(addr); }
      // connection direction/role: outbound-full-relay | block-relay-only | manual | inbound | feeler
      const conntype = (/New (outbound-full-relay|block-relay-only|manual|inbound|feeler)/i.exec(line) || [])[1]
        || (/\((outbound-full-relay|block-relay-only|manual|inbound|feeler)\)/i.exec(line) || [])[1] || '';
      const transport = (/transport: (v\d)/i.exec(line) || [])[1] || '';         // BIP324 v2 vs legacy v1
      const ver = (/version: (\d+)/.exec(line) || [])[1] || '';                  // protocol version (70016 = v31)
      const blocks = (/blocks=(\d+)/i.exec(line) || [])[1] || '';                // peer's tip height at connect
      // human reason for a disconnect or a failed dial
      if (kind === 'disconnect') reason = (/^\S+\s+(.*?),?\s*disconnect/i.exec(line) || [])[1] || 'disconnecting';
      else if (kind === 'failed') reason = (/failed (?:after \w+: )?(.*)$/i.exec(line) || [])[1] || 'connect failed';
      // backfill address/network/subver from the live peer table, then the session cache
      const pm = (peer && peerMap[peer]) || (peer && PEER_ADDR.get(peer)) || null;
      const finalAddr = addr || (pm && pm.addr) || '';
      const subver = (/\/[^ /]+:[\d.]+\//.exec(line) || [])[0] || (pm && pm.subver) || '';
      return { time: time.replace('T', ' ').replace('Z', ''), kind, addr: finalAddr,
               net: (pm && pm.network) || netOf(finalAddr),
               peer, conntype, transport, ver, blocks, subver, reason, text: line.slice(20, 200) };
    })
    // keep only rows that carry an address (live peers, failed dials, local binds) or a
    // disconnect (its reason is the value, and its address fills in from the session cache
    // over time). Drops the address-less "connected"/"inbound"/"info" log noise.
    .filter(e => e.addr || e.kind === 'disconnect' || e.kind === 'failed');
    // the node's own bind addresses repeat on every startup — show each unique local once
    const seenLocal = new Set();
    const deduped = events.filter(e => {
      if (e.kind !== 'local') return true;
      if (seenLocal.has(e.addr)) return false;
      seenLocal.add(e.addr); return true;
    });
    events.length = 0; events.push(...deduped);
    // merge live peers (full detail) with the log history; drop the log's redundant
    // "connected"/"inbound" lines for peers that are still live (the live row supersedes them)
    const liveIds = new Set(peerEvents.map(p => p.peer));
    const logEvents = events.filter(e =>
      !((e.kind === 'connected' || e.kind === 'inbound') && e.peer && liveIds.has(e.peer)));
    const merged = [...peerEvents, ...logEvents]
      .sort((a, b) => String(a.time).localeCompare(String(b.time)));
    const tally = { connected: 0, failed: 0, disconnect: 0, inbound: 0, feeler: 0, local: 0 };
    const nets = {}, conntypes = {}, transports = { v1: 0, v2: 0 };
    for (const e of merged) {
      if (tally[e.kind] != null) tally[e.kind]++;
      if (e.net) nets[e.net] = (nets[e.net] || 0) + 1;
      if (e.conntype) conntypes[e.conntype] = (conntypes[e.conntype] || 0) + 1;
      if (e.transport && transports[e.transport] != null) transports[e.transport]++;
    }
    res.json({ ok: true, events: merged, livePeers: peerEvents.length,
               tally, nets, conntypes, transports, local: [...new Set(local)] });
  } catch (e) { res.json({ ok: false, error: String(e.message || e), events: [], tally: {}, local: [] }); }
});

// Network health for the BTC.oracle — average peer ping (getpeerinfo.pingtime) + live network
// usage (getnettotals, rate derived between samples). Circuit-safe; returns nulls when RPC is busy.
let _netPrev = null;
app.get('/api/nethealth', async (req, res) => {
  try {
    const pr = await rpcCached('full', 'getpeerinfo', [], null, 4000);
    const pi = Array.isArray(pr) ? pr : (pr && pr.value) || [];
    const pings = pi.map(p => p.pingtime).filter(v => typeof v === 'number' && v >= 0);
    const avg = pings.length ? pings.reduce((a, b) => a + b, 0) / pings.length : null;
    const nr = await rpcCached('full', 'getnettotals', [], null, 4000);
    const nt = (nr && nr.value) || nr || {};
    let rateIn = null, rateOut = null;
    const now = Date.now();
    if (_netPrev && typeof nt.totalbytesrecv === 'number') {
      const dt = Math.max(0.001, (now - _netPrev.t) / 1000);
      rateIn = Math.max(0, nt.totalbytesrecv - _netPrev.r) / dt;
      rateOut = Math.max(0, nt.totalbytessent - _netPrev.s) / dt;
    }
    if (typeof nt.totalbytesrecv === 'number') _netPrev = { t: now, r: nt.totalbytesrecv, s: nt.totalbytessent };
    res.json({ ok: true, peers: pi.length,
      avgPingMs: avg != null ? avg * 1000 : null,
      minPingMs: pings.length ? Math.min(...pings) * 1000 : null,
      maxPingMs: pings.length ? Math.max(...pings) * 1000 : null,
      totalRecv: nt.totalbytesrecv ?? null, totalSent: nt.totalbytessent ?? null,
      rateIn, rateOut });
  } catch (e) { res.json({ ok: false, error: String(e.message || e) }); }
});

// Export the most-recent ~N GB of raw block files (blk*.dat, highest-numbered = newest blocks) — a
// "recent slice" of the chain. This IS a real thing (blocks-dir seeding), but with an honest limit:
// recent blocks ALONE cannot bootstrap a from-scratch node — validation needs the UTXO set at the
// slice's base. The rapid "point to update from" for a pruned node = this slice PAIRED with a UTXO
// snapshot (dumptxoutset/loadtxoutset, assumeUTXO) at the base height. Node-control-gated; single-flight.
let _blockExporting = false;
app.post('/api/blocks/export-recent', (req, res) => {
  if (!NODE_CONTROL) return res.status(403).json({ ok: false, error: 'node control disabled' });
  if (_blockExporting) return res.json({ ok: false, error: 'a block export is already in progress' });
  const sizeGb = Math.max(0.2, Math.min(50, Number(req.body?.sizeGb) || 2));
  const blocksDir = join(BTC_DATADIR, 'blocks');
  const dstDir = join(process.env.BANKON_EXPORT_DIR || join(homedir(), 'bankon-tools', 'exports'), 'recent-blocks');
  try {
    const files = readdirSync(blocksDir).filter(f => /^blk\d+\.dat$/.test(f)).sort((a, b) => b.localeCompare(a)); // newest first
    const budget = sizeGb * (1 << 30); let used = 0; const pick = [];
    for (const f of files) {
      const sz = statSync(join(blocksDir, f)).size;
      if (used + sz > budget && pick.length) break;
      pick.push(f); used += sz;
    }
    if (!pick.length) return res.json({ ok: false, error: 'no block files found' });
    const all = [];
    for (const f of pick) { all.push(f); const rev = f.replace('blk', 'rev'); if (existsSync(join(blocksDir, rev))) all.push(rev); }
    mkdirSync(dstDir, { recursive: true });
    _blockExporting = true;
    const t0 = Date.now();
    execFile('bash', ['-c', `cd "${blocksDir}" && cp -f ${all.map(f => `'${f}'`).join(' ')} "${dstDir}/"`],
      { timeout: 30 * 60 * 1000 }, (err) => {
        _blockExporting = false;
        if (err) return res.json({ ok: false, error: String(err.message) });
        res.json({ ok: true, dir: dstDir, files: all.length, sizeGb: +(used / (1 << 30)).toFixed(2),
          range: `${pick[pick.length - 1]} … ${pick[0]}`, elapsedSec: Math.round((Date.now() - t0) / 1000),
          note: 'Recent block files only. For a from-scratch PRUNED bootstrap, PAIR with a UTXO snapshot '
              + '(Export UTXO snapshot / loadtxoutset) at the base height — recent blocks cannot validate '
              + 'without the UTXO set. To seed a node that already has the earlier chain: drop these into its '
              + 'blocks/ and let it validate forward.' });
      });
  } catch (e) { _blockExporting = false; res.json({ ok: false, error: String(e.message) }); }
});

// ---- rageBTC chain exporter → pgvectorscale (blocks + txs + io + addresses) ------------------
// Accelerated via THIS server's rpc() (rageRPC limiter + breaker), resumable by height, dry-run when
// no DATABASE_URL. Fire-and-poll: /export starts + returns, /status is polled, /stop pauses (resumable).
const CHAIN_DB_URL = process.env.DATABASE_URL || process.env.BANKON_DATABASE_URL || null;
const CHAIN_SCHEMA = process.env.BANKON_CHAIN_SCHEMA || 'public';
const chainExporter = createChainExporter({
  rpc, databaseUrl: CHAIN_DB_URL, schema: CHAIN_SCHEMA,
  log: (m) => console.log('[chain-export]', m),
});
app.post('/api/chain/export', (req, res) => {
  if (!NODE_CONTROL) return res.status(403).json({ ok: false, error: 'node control disabled' });
  const b = req.body || {};
  try {
    const s = chainExporter.start({
      fromHeight: b.fromHeight != null ? Number(b.fromHeight) : undefined,
      toHeight: b.toHeight != null ? Number(b.toHeight) : undefined,
      resume: b.resume !== false, dryRun: b.dryRun === true || !CHAIN_DB_URL,
      batchBlocks: Math.max(1, Math.min(500, Number(b.batchBlocks) || 50)),
    });
    res.json({ ok: true, started: true, dbConfigured: !!CHAIN_DB_URL, schema: CHAIN_SCHEMA, status: s });
  } catch (e) { res.json({ ok: false, error: String(e.message || e), status: chainExporter.status() }); }
});
app.post('/api/chain/export/stop', (req, res) => {
  if (!NODE_CONTROL) return res.status(403).json({ ok: false, error: 'node control disabled' });
  res.json({ ok: true, ...chainExporter.stop(), status: chainExporter.status() });
});
app.get('/api/chain/export/status', (req, res) =>
  res.json({ ok: true, dbConfigured: !!CHAIN_DB_URL, schema: CHAIN_SCHEMA, ...chainExporter.status() }));
app.get('/api/chain/export/verify', async (req, res) => {
  const from = Number(req.query.from), to = Number(req.query.to);
  if (!Number.isFinite(from) || !Number.isFinite(to) || to < from)
    return res.json({ ok: false, error: 'need ?from=&to= (to >= from)' });
  if (to - from > 2000) return res.json({ ok: false, error: 'range too large (max 2000)' });
  try { res.json({ ok: true, ...(await chainExporter.verify({ from, to })) }); }
  catch (e) { res.json({ ok: false, error: String(e.message || e) }); }
});

// Node Monitor — whitelisted, READ-ONLY terminal commands (bitcoin-cli + debug.log/system), each a
// push button in the Console's Monitor tab. No user input is interpolated — only fixed command keys.
const _CLI = `${BTC_BIN}/bitcoin-cli -datadir="${BTC_DATADIR}"`;
const MONITOR = {
  process:      { label: '⚙ Process & ports',          cmd: `pgrep -a bitcoind; echo '— listening —'; ss -ltnp 2>/dev/null | grep -E ':8332|:8333' || echo '(ss n/a)'` },
  getinfo:      { label: 'ℹ Node info (-getinfo)',       cmd: `${_CLI} -getinfo` },
  height:       { label: '⛓ Block height',               cmd: `${_CLI} getblockcount` },
  chaininfo:    { label: '⛓ Chain / sync',               cmd: `${_CLI} getblockchaininfo | grep -E 'blocks|headers|verificationprogress|initialblockdownload|size_on_disk'` },
  synctip:      { label: '📈 Sync tip (from log)',        cmd: `grep -a UpdateTip "${DEBUG_LOG}" | tail -1` },
  recentlog:    { label: '📜 Recent log (20)',            cmd: `tail -20 "${DEBUG_LOG}"` },
  peers:        { label: '🔌 Peer count',                 cmd: `${_CLI} getconnectioncount` },
  peerinfo:     { label: '🔌 Peer list',                  cmd: `${_CLI} getpeerinfo | grep -E '"addr"|"subver"'` },
  netinfo:      { label: '🌐 Network info',               cmd: `${_CLI} getnetworkinfo | grep -E 'version|subversion|connections|relayfee'` },
  peeractivity: { label: '🔌 Connection activity (log)',  cmd: `grep -aE 'peer connected|connect\\(\\) to|disconnect' "${DEBUG_LOG}" | tail -15` },
  flood:        { label: '⚠ Work-queue flood (3s)',       cmd: `a=$(grep -ac 'work queue depth exceeded' "${DEBUG_LOG}"); sleep 3; b=$(grep -ac 'work queue depth exceeded' "${DEBUG_LOG}"); echo "+$((b-a)) flood warnings in 3s"` },
  rpcinfo:      { label: '⚡ RPC queue',                  cmd: `${_CLI} getrpcinfo` },
  errors:       { label: '🩺 Errors / warnings',          cmd: `grep -aiE 'error|warning|corrupt|fatal' "${DEBUG_LOG}" | tail -20` },
  mempool:      { label: '💧 Mempool',                    cmd: `${_CLI} getmempoolinfo` },
  uptime:       { label: '⏱ Uptime',                     cmd: `${_CLI} uptime` },
  disksize:     { label: '💾 Chain data size',            cmd: `du -sh "${BTC_DATADIR}/blocks" "${BTC_DATADIR}/chainstate" "${BTC_DATADIR}/indexes" 2>/dev/null` },
  diskfree:     { label: '💾 Disk free',                  cmd: `df -h "${BTC_DATADIR}"` },
};
app.get('/api/monitor', (req, res) =>
  res.json({ ok: true, commands: Object.fromEntries(Object.entries(MONITOR).map(([k, v]) => [k, v.label])) }));
app.post('/api/monitor', (req, res) => {
  const m = MONITOR[(req.body || {}).cmd];
  if (!m) return res.status(400).json({ ok: false, error: 'unknown command key' });
  execFile('bash', ['-c', m.cmd], { timeout: 15000, maxBuffer: 1 << 20 }, (err, stdout, stderr) => {
    const output = ((stdout || '') + (stderr ? '\n' + stderr : '')).trim();
    res.json({ ok: true, key: req.body.cmd, label: m.label, cmd: m.cmd, output: output || (err ? String(err.message) : '(no output)') });
  });
});

// ---- Fast-node ranking — rank peers by speed (ping + recv-rate + height), persist the fast ones,
// and connect to the fastest. This is how you MAX IBD: feed from many fast peers, not one slow one.
const FAST_FILE = join(__dir, 'fast-nodes.json');
const _loadFast = () => { try { return JSON.parse(readFileSync(FAST_FILE, 'utf8')); } catch { return {}; } };
const _saveFast = m => { try { writeFileSync(FAST_FILE, JSON.stringify(m, null, 2)); } catch {} };
// Score a peer for BLOCK-DOWNLOAD usefulness (higher = faster + more useful for getting blocks).
// Weights the things that make a peer good at serving blocks fast: low latency, a tip ahead of us,
// compact-block high-bandwidth relay, a block-serving connection type, and actively-inflight blocks.
function _score(p, now) {
  const ping = (p.minping ?? p.pingtime ?? 1) * 1000;                  // ms; minping = best-case latency
  const dur = Math.max(60, now - (p.conntime || now));
  const recvKBs = (p.bytesrecv || 0) / dur / 1024;                     // sustained KB/s from this peer
  const height = p.synced_blocks || p.startingheight || 0;
  const hb = p.bip152_hb_from ? 25 : 0;                                // sends us compact blocks (HB) → fast relay
  const typ = /block-relay|outbound-full-relay/.test(p.connection_type || '') ? 15 : 0;
  const serving = (p.inflight && p.inflight.length) ? 10 : 0;          // blocks in flight from it right now
  return Math.round(recvKBs * 2 + height / 1e5 + hb + typ + serving - ping / 20);
}
app.get('/api/node/peerspeed', async (req, res) => {
  try {
    const peers = await rpc('full', 'getpeerinfo', [], null, 12000);
    const now = Math.floor(Date.now() / 1000);
    const ranked = peers.map(p => ({ addr: p.addr, dir: p.inbound ? 'in' : 'out',
      subver: (p.subver || '').replace(/\//g, ''), pingMs: Math.round((p.pingtime || p.minping || 0) * 1000),
      height: p.synced_blocks || p.startingheight || 0, recvMiB: +((p.bytesrecv || 0) / 1048576).toFixed(1),
      score: _score(p, now) })).sort((a, b) => b.score - a.score);
    const fast = _loadFast();                                          // persist the fast outbound peers
    for (const p of ranked) if (p.dir === 'out' && p.pingMs > 0 && p.pingMs < 300 && p.height > 800000)
      fast[p.addr] = { addr: p.addr, pingMs: p.pingMs, height: p.height, score: p.score, seen: now };
    _saveFast(fast);
    res.json({ ok: true, peers: ranked, saved: Object.keys(fast).length });
  } catch (e) { res.json({ ok: false, error: String(e.message || e), peers: [] }); }
});
app.get('/api/node/fastnodes', (req, res) =>
  res.json({ ok: true, nodes: Object.values(_loadFast()).sort((a, b) => b.score - a.score) }));
// ---- Favourites ("promote") + "boot" — per-peer node actions ----------------
// PROMOTE = mark a peer favourite (persisted) AND `addnode add` so Core keeps a
//   persistent connection to it across restarts (your trusted, hand-picked peers).
// BOOT    = disconnectnode — drop a misbehaving/slow peer right now.
// Both dispatch DIRECTLY via bitcoin-cli so they work during IBD (breaker open).
const FAV_FILE = join(__dir, 'favourite-nodes.json');
const _loadFav = () => { try { return JSON.parse(readFileSync(FAV_FILE, 'utf8')); } catch { return {}; } };
const _saveFav = m => { try { writeFileSync(FAV_FILE, JSON.stringify(m, null, 2)); } catch {} };
const _cliDirect = (args) => new Promise(r =>                          // direct bitcoin-cli, bypasses breaker
  execFile(`${BTC_BIN}/bitcoin-cli`, [`-datadir=${BTC_DATADIR}`, ...args],
    { timeout: 12000, maxBuffer: 64 << 20 },                           // addrman dumps (getnodeaddresses 0) are multi-MB
    (err, out) => r({ err, out: (out || '').trim() })));
app.get('/api/node/favourites', (req, res) =>
  res.json({ ok: true, nodes: Object.values(_loadFav()).sort((a, b) => (b.promoted || 0) - (a.promoted || 0)) }));
app.post('/api/node/promote', async (req, res) => {
  if (!NODE_CONTROL) return res.status(403).json({ ok: false, error: 'node control disabled' });
  const addr = String(req.body?.addr || '').trim();
  if (!addr) return res.status(400).json({ ok: false, error: 'need {addr}' });
  const fav = _loadFav(); const on = req.body?.on !== false;            // on:false = un-promote
  if (on) { fav[addr] = { addr, subver: req.body?.subver || '', promoted: Math.floor(Date.now() / 1000) }; await _cliDirect(['addnode', addr, 'add']); }
  else { delete fav[addr]; await _cliDirect(['addnode', addr, 'remove']); }
  _saveFav(fav);
  res.json({ ok: true, addr, promoted: on, count: Object.keys(fav).length });
});
app.post('/api/node/boot', async (req, res) => {
  if (!NODE_CONTROL) return res.status(403).json({ ok: false, error: 'node control disabled' });
  const addr = String(req.body?.addr || '').trim();
  if (!addr) return res.status(400).json({ ok: false, error: 'need {addr}' });
  const fav = _loadFav(); if (fav[addr]) { delete fav[addr]; _saveFav(fav); await _cliDirect(['addnode', addr, 'remove']); }
  const { err, out } = await _cliDirect(['disconnectnode', addr]);     // drop the live connection now
  const gone = /not connected|not found in connected/i.test((err && err.message) || out || '');
  if (err && !gone) return res.json({ ok: false, error: out || String(err.message || err) });
  res.json({ ok: true, addr, booted: true, note: gone ? 'already disconnected' : 'disconnected' });
});
// BLACKLIST — ban (unreliable) / unban a peer. setban wants a bare IP or ip/subnet (no port).
app.post('/api/node/ban', async (req, res) => {
  if (!NODE_CONTROL) return res.status(403).json({ ok: false, error: 'node control disabled' });
  const raw = String(req.body?.addr || '').trim();
  if (!raw) return res.status(400).json({ ok: false, error: 'need {addr}' });
  const ip = raw.includes(']') ? raw.slice(1, raw.indexOf(']'))          // [ipv6]:port → ipv6
           : raw.includes('/') ? raw : raw.split(':')[0];               // keep subnets; strip :port
  const on = req.body?.on !== false;
  if (on) {
    const hours = Math.max(1, Math.min(24 * 365, Number(req.body?.hours) || 24 * 7));
    const fav = _loadFav(); if (fav[raw] || fav[ip]) { delete fav[raw]; delete fav[ip]; _saveFav(fav); }
    await _cliDirect(['addnode', raw, 'remove']).catch(() => {});
    const { err, out } = await _cliDirect(['setban', ip, 'add', String(hours * 3600)]);
    if (err && !/already banned/i.test(out || err.message || '')) return res.json({ ok: false, error: out || String(err.message || err) });
    _cliDirect(['disconnectnode', raw]).catch(() => {});                 // drop any live connection too
    return res.json({ ok: true, ip, banned: true, hours });
  }
  const { err, out } = await _cliDirect(['setban', ip, 'remove']);
  if (err && !/not.*banned|unban.*fail/i.test(out || err.message || '')) return res.json({ ok: false, error: out || String(err.message || err) });
  res.json({ ok: true, ip, banned: false });
});
app.post('/api/node/connect-fast', async (req, res) => {
  if (!NODE_CONTROL) return res.status(403).json({ ok: false, error: 'node control disabled' });
  const n = Math.max(1, Math.min(48, Number(req.body?.count) || 16));
  let targets = Object.values(_loadFast()).sort((a, b) => b.score - a.score).slice(0, n).map(x => x.addr);
  if (targets.length < n) {                                            // bootstrap: fresh IPs from DNS seeds
    const SEEDS = ['seed.bitcoin.sipa.be', 'dnsseed.bluematt.me', 'seed.bitcoinstats.com',
      'seed.bitcoin.sprovoost.nl', 'dnsseed.emzy.de', 'seed.bitcoin.wiz.biz', 'seed.btc.petertodd.net', 'seed.mainnet.achownodes.xyz'];
    for (const s of SEEDS) { try { (await dnsp.resolve4(s)).slice(0, 6).forEach(ip => targets.push(ip + ':8333')); } catch {} if (targets.length >= n * 2) break; }
  }
  targets = [...new Set(targets)].slice(0, n * 2);
  // Dispatch DIRECTLY via bitcoin-cli — bypasses the Console's circuit-breaker/limiter (which is open
  // during IBD and would throttle these). addnode is net-layer (safe). Fire-and-forget → instant.
  const cli = `${BTC_BIN}/bitcoin-cli -datadir="${BTC_DATADIR}"`;
  for (const t of targets) execFile('bash', ['-c', `${cli} addnode "${t}" add 2>/dev/null`], { timeout: 8000 }, () => {});
  res.json({ ok: true, dispatched: targets.length, note: 'connecting in background (direct addnode) — watch the peer count climb' });
});

// ---- Fast-peer PREFERENCE — an OPTIONAL background loop (on/off) that keeps the peer set biased
// toward the fastest block-serving peers, so block download stays fast without manual clicking:
//   1) re-ranks connected peers and persists/refreshes the fast list
//   2) tops up connections from the best saved fast nodes we aren't connected to (addnode add → sticky)
//   3) gently drops the single slowest non-favourite outbound peer (only above a peer-count floor, and
//      only if it's clearly slow and not currently serving blocks) to free a slot for a faster one
// All actions are net-layer (addnode/disconnectnode via direct bitcoin-cli), so they work during IBD
// when the RPC breaker is open. Favourites and inbound peers are never touched. Off by default.
const FASTPREF_FILE = join(__dir, 'fastpref.json');
let FASTPREF = (() => { try { return JSON.parse(readFileSync(FASTPREF_FILE, 'utf8')); }
                        catch { return { on: false, intervalSec: 90, floor: 12, explore: true, exploreBatch: 8, keep: 200, rotate: true }; } })();
const _saveFastpref = () => { try { writeFileSync(FASTPREF_FILE, JSON.stringify(FASTPREF, null, 2)); } catch {} };
let _fastprefTimer = null, _fastprefBusy = false, _fastprefLast = null, _knownTotal = null;
async function _fastprefTick() {
  if (!FASTPREF.on || _fastprefBusy) return;
  _fastprefBusy = true;
  const acts = { promoted: 0, toppedUp: 0, dropped: 0, explored: 0 };
  try {
    const { out } = await _cliDirect(['getpeerinfo']);
    let peers; try { peers = JSON.parse(out); } catch { return; }
    if (!Array.isArray(peers)) return;
    const now = Math.floor(Date.now() / 1000), fav = _loadFav();
    const ranked = peers.map(p => ({ p, s: _score(p, now) })).sort((a, b) => b.s - a.s);
    // 1) ROLLING INDEX: score connected peers, persist the fast ones (with a rolling last-seen)
    const fast = _loadFast();
    for (const { p, s } of ranked) {
      const ping = (p.minping ?? p.pingtime ?? 9) * 1000;
      if (!p.inbound && ping > 0 && ping < 250 && (p.synced_blocks || 0) > 800000) {
        fast[p.addr] = { addr: p.addr, pingMs: Math.round(ping), height: p.synced_blocks || 0, score: s, seen: now };
        acts.promoted++;
      }
    }
    // trim the rolling index: drop the stalest beyond `keep`, so it stays "the fastest, freshly measured"
    const keep = FASTPREF.keep || 200, entries = Object.values(fast);
    if (entries.length > keep)
      for (const e of entries.sort((a, b) => (a.seen || 0) - (b.seen || 0)).slice(0, entries.length - keep)) delete fast[e.addr];
    _saveFast(fast);
    // 2) top up from the best saved fast nodes we're not connected to
    const connected = new Set(peers.map(p => p.addr));
    const topUp = Object.values(fast).sort((a, b) => b.score - a.score).filter(x => !connected.has(x.addr)).slice(0, 4);
    for (const t of topUp) { _cliDirect(['addnode', t.addr, 'add']); acts.toppedUp++; }
    // 3) EXPLORE FROM ALL: sample fresh candidates from the whole addrman and dial them one-try to
    //    measure — a rolling probe of the network so new fast peers keep entering the index.
    if (FASTPREF.explore) {
      const c = await _refreshCensus();                                     // refresh the local all-nodes census (shared)
      if (c && c.nodes.length) {
        const seen = new Set(peers.map(p => p.addr.replace(/:\d+$/, '')));
        const cand = c.nodes.filter(a => a.address && a.port && !seen.has(a.address) && !fast[`${a.address}:${a.port}`]);
        for (let i = cand.length - 1; i > 0; i--) { const j = Math.floor(Math.random() * (i + 1)); [cand[i], cand[j]] = [cand[j], cand[i]]; }
        for (const a of cand.slice(0, FASTPREF.exploreBatch || 8)) {
          _cliDirect(['addnode', `${a.address}:${a.port}`, 'onetry']); acts.explored++;   // connect once → measured next tick
        }
      }
    }
    // 4) gentle prune — one clearly-slow, non-favourite, non-serving OUTBOUND peer, only above the floor
    if (peers.length > (FASTPREF.floor || 12) && ranked.length) {
      const { p, s } = ranked[ranked.length - 1];
      const ping = (p.minping ?? p.pingtime ?? 0) * 1000;
      if (p && !p.inbound && !fav[p.addr] && (ping > 1000 || s < 0) && !(p.inflight && p.inflight.length)) {
        _cliDirect(['disconnectnode', p.addr]); acts.dropped++;
      }
    }
    // 5) SECURITY ROTATION — even when the set is all-fast, always churn ONE peer: drop a random
    //    non-favourite outbound peer and bring in the NEXT-fastest index node we're not connected to.
    //    This ensures the peer set is never a fixed, fully-known group — an extra layer against a
    //    coordinated fast-peer / eclipse attempt. Runs no matter how many nodes are configured.
    if (FASTPREF.rotate !== false && peers.length > 3) {
      const outs = peers.filter(p => !p.inbound && !fav[p.addr] && !(p.inflight && p.inflight.length));
      if (outs.length) {
        const victim = outs[Math.floor(Math.random() * outs.length)];
        const idx = Object.values(fast).sort((a, b) => b.score - a.score);
        const repl = idx.find(x => !connected.has(x.addr) && x.addr !== victim.addr)   // the next-fastest not connected
                  || idx[Math.floor(Math.random() * Math.min(idx.length, 10) || 1)];   // else a random top-10 fast node
        _cliDirect(['disconnectnode', victim.addr]);
        if (repl && repl.addr) _cliDirect(['addnode', repl.addr, 'onetry']);
        acts.rotated = 1;
      }
    }
    _fastprefLast = { at: now, peers: peers.length, knownTotal: _knownTotal,
                      indexSize: Object.keys(fast).length, ...acts };
  } finally { _fastprefBusy = false; }
}
function _fastprefApply() {
  if (_fastprefTimer) { clearInterval(_fastprefTimer); _fastprefTimer = null; }
  if (FASTPREF.on) { _fastprefTimer = setInterval(_fastprefTick, Math.max(30, FASTPREF.intervalSec || 90) * 1000); _fastprefTick(); }
}
app.get('/api/node/fastpref', (req, res) =>
  res.json({ ok: true, ...FASTPREF, running: !!_fastprefTimer, last: _fastprefLast,
             knownTotal: _knownTotal, indexSize: Object.keys(_loadFast()).length }));
app.post('/api/node/fastpref', (req, res) => {
  if (!NODE_CONTROL) return res.status(403).json({ ok: false, error: 'node control disabled' });
  if (typeof req.body?.on === 'boolean') FASTPREF.on = req.body.on;
  if (typeof req.body?.explore === 'boolean') FASTPREF.explore = req.body.explore;
  if (typeof req.body?.rotate === 'boolean') FASTPREF.rotate = req.body.rotate;
  if (Number(req.body?.intervalSec)) FASTPREF.intervalSec = Math.max(30, Number(req.body.intervalSec));
  if (Number(req.body?.floor)) FASTPREF.floor = Math.max(8, Number(req.body.floor));
  if (Number(req.body?.exploreBatch)) FASTPREF.exploreBatch = Math.max(1, Math.min(32, Number(req.body.exploreBatch)));
  _saveFastpref(); _fastprefApply();
  res.json({ ok: true, ...FASTPREF, running: !!_fastprefTimer, last: _fastprefLast,
             knownTotal: _knownTotal, indexSize: Object.keys(_loadFast()).length });
});
_fastprefApply();   // resume the loop if it was left on across restarts

// ---- Local node census — a stored, updatable copy of the FULL all-nodes list (our addrman: tens of
// thousands of nodes this node has actually heard of). It is the local canonical copy that every
// BANKON database option draws from: pushed to pgvectorscale via the RAGE handoff (`/api/rage/handoff`)
// and collected into Postgres by the WaaS node-collector. Reported counts are ACTUAL (no reference).
const CENSUS_FILE = join(__dir, 'node-census.json');
let _census = (() => { try { return JSON.parse(readFileSync(CENSUS_FILE, 'utf8')); }
                       catch { return { updatedAt: 0, count: 0, nets: {}, nodes: [] }; } })();
async function _refreshCensus() {
  try {
    const { out } = await _cliDirect(['getnodeaddresses', '0']);        // 0 = the entire addrman
    let addrs; try { addrs = JSON.parse(out); } catch { return null; }
    if (!Array.isArray(addrs)) return null;
    const nets = {};
    const nodes = addrs.map(a => { nets[a.network] = (nets[a.network] || 0) + 1;
      return { address: a.address, port: a.port, services: a.services, network: a.network, time: a.time }; });
    _census = { updatedAt: Math.floor(Date.now() / 1000), count: nodes.length, nets, nodes };
    _knownTotal = nodes.length;
    try { writeFileSync(CENSUS_FILE, JSON.stringify(_census)); } catch {}   // persisted local copy
    return _census;
  } catch { return null; }
}
app.get('/api/nodes/census', (req, res) => {
  const { nodes, ...meta } = _census;                                    // ?full=1 to include the whole list
  res.json({ ok: true, ...meta, nodes: req.query.full === '1' ? nodes : undefined });
});
app.post('/api/nodes/census/refresh', async (req, res) => {
  const c = await _refreshCensus();
  if (!c) return res.json({ ok: false, error: 'addrman unavailable (node busy?)' });
  const { nodes, ...meta } = c; res.json({ ok: true, ...meta });
});
setInterval(_refreshCensus, 30 * 60 * 1000); setTimeout(_refreshCensus, 8000);   // auto-update + prime on boot

// Current settings — complete, live display (RPC policy, circuit-breaker state, warm/burst, control).
function settingsSnapshot() {
  return {
    rpc: { rage: RPC.rage, maxInflight: RPC.maxInflight, effInflight: _effCap(), tier: RPC.rage ? 'RAGE' : RPC.maxInflight,
           steps: [1, 2, 4, 8, 16, 32, 64, 128, 256], timeoutMs: RPC.timeoutMs, distressMs: RPC.distressMs,
           inflight: _inflight, waiting: _waiters.length, coalescing: COALESCE.size,
           circuitOpen: rpcCircuitOpen(), distressRemainingMs: Math.max(0, _distressUntil - Date.now()),
           tripCount: _tripCount, recoverEvery: RECOVER_EVERY, stats: { ...RPC_STATS } },
    cache: { bgRefreshMinMs: BG_MIN_MS, warmCheapMs: Number(process.env.BANKON_WARM_MS) || 25000, chainTimeoutMs: CHAIN_T },
    blocks: { recentMax: RECENT_MAX, recentBudget: RECENT_BUDGET },
    node: { control: NODE_CONTROL, hammer: HAMMER, datadir: BTC_DATADIR },
  };
}
// Throttle tiers — powers of 2 (1,2,4 = resilient floor · 8 = standard · 256 = max) + RAGE = no
// upper limit (hardware-bound). The adaptive cap still floors at 2 under distress, so even RAGE
// degrades safely during IBD; it only reaches the high ceiling when the node is genuinely healthy.
const THROTTLE_STEPS = [1, 2, 4, 8, 16, 32, 64, 128, 256];
const RAGE_CEIL = Number(process.env.BANKON_RAGE_CEIL) || 100000;   // "unlimited"
app.get('/api/settings', (req, res) => res.json({ ok: true, settings: settingsSnapshot() }));
app.post('/api/settings', (req, res) => {        // runtime-tune the RPC policy (interaction)
  if (!NODE_CONTROL) return res.status(403).json({ ok: false, error: 'control disabled' });
  const b = req.body || {};
  if (b.rage === true || b.rage === false) {           // RAGE toggle: uncapped ceiling vs standard(8)
    RPC.rage = b.rage;
    RPC.maxInflight = b.rage ? RAGE_CEIL : 8; RPC.distressMs = b.rage ? 3000 : 12000;
  }
  if (b.maxInflight != null) {                         // throttle controller (1..256, or RAGE above 256)
    const v = Math.max(1, +b.maxInflight);
    RPC.rage = v > 256;
    RPC.maxInflight = RPC.rage ? v : Math.min(256, v);
  }
  if (b.timeoutMs  != null)  RPC.timeoutMs   = Math.max(2000, Math.min(120000, +b.timeoutMs));
  if (b.distressMs != null)  RPC.distressMs  = Math.max(0, Math.min(120000, +b.distressMs));
  _effInflight = RPC.maxInflight; _okStreak = 0;   // a manual change resets the adaptive cap to the new ceiling
  res.json({ ok: true, settings: settingsSnapshot() });
});

// ---- rageRPC handoff → pgvectorscale (rage.pythai.net) -------------------------------------------
// Gathers live BTC node addresses (addrman, breaker/adaptive-protected) and POSTs them to the rage
// ingest endpoint for pgvectorscale. URL/path/token configurable; degrades honestly if unreachable.
const RAGE_URL = (process.env.RAGE_URL || 'https://bankon.pythai.net').replace(/\/$/, '');
const RAGE_INGEST_PATH = process.env.RAGE_INGEST_PATH || '/ragest';
const RAGE_TOKEN = process.env.RAGE_TOKEN || '';
app.get('/api/rage/status', (req, res) =>
  res.json({ ok: true, target: RAGE_URL + RAGE_INGEST_PATH, tokenSet: !!RAGE_TOKEN }));
app.post('/api/rage/handoff', async (req, res) => {
  const count = Math.max(1, Math.min(5000, Number(req.body?.count) || 500));
  let nodes = [], src = 'getnodeaddresses', stale = false;
  try {                                                          // addrman snapshot (cache-aware)
    const r = await rpcCached('full', 'getnodeaddresses', [count], null, 15000);
    stale = r.stale;
    nodes = (r.value || []).map(a => ({ address: a.address, port: a.port, services: a.services, time: a.time, network: a.network }));
  } catch {
    try {                                                        // fallback: connected peers (warm cache) when addrman is choked
      const r = await rpcCached('full', 'getpeerinfo', [], null, 10000);
      stale = r.stale; src = 'getpeerinfo';
      nodes = (r.value || []).map(p => ({ address: (p.addr || '').replace(/:\d+$/, ''),
        port: Number((p.addr || '').split(':').pop()) || 8333, services: p.services, time: p.conntime, network: p.network }));
    } catch (e) { return res.json({ ok: false, error: 'no node data (RPC choked + no cache): ' + String(e.message || e) }); }
  }
  if (!nodes.length) return res.json({ ok: false, error: 'no nodes gathered', source: src });
  let push = { ok: false, status: 0, response: '' };
  try {
    const r = await fetch(RAGE_URL + RAGE_INGEST_PATH, {
      method: 'POST',
      headers: { 'content-type': 'application/json', ...(RAGE_TOKEN ? { authorization: 'Bearer ' + RAGE_TOKEN } : {}) },
      body: JSON.stringify({ source: 'bankon', kind: 'btc-nodes', source_rpc: src, stale, count: nodes.length, nodes }),
      signal: AbortSignal.timeout(20000),
    });
    push = { ok: r.ok, status: r.status, response: (await r.text().catch(() => '')).slice(0, 200) };
  } catch (e) { push = { ok: false, status: 0, response: String(e.message || e) }; }
  res.json({ ok: push.ok, gathered: nodes.length, source: src, stale, target: RAGE_URL + RAGE_INGEST_PATH, push });
});

// Index state — served from the warm cache (instant, no node call) so polling it never adds RPC
// load during IBD. The warmer refreshes getindexinfo when the flooded node has a gap; between
// refreshes the Indexes view advances its frontier from the live debug.log tip (txindex tracks
// validation), so it still updates in realtime without hammering the node.
// on-disk size per index dir — refreshed in the BACKGROUND (du traverses thousands of .ldb
// files; never block the request path). Names match getindexinfo keys.
const IDX_DIRS = { 'txindex': 'indexes/txindex', 'coinstatsindex': 'indexes/coinstatsindex',
                   'basic block filter index': 'indexes/blockfilter/basic' };
let _idxSizes = {};
function _refreshIdxSizes() {
  for (const [name, rel] of Object.entries(IDX_DIRS)) {
    const p = join(BTC_DATADIR, rel);
    if (!existsSync(p)) { delete _idxSizes[name]; continue; }
    execFile('du', ['-sb', p], { timeout: 25000, maxBuffer: 1 << 20 }, (err, out) => {
      if (!err && out) { const n = parseInt(out.split(/\s+/)[0], 10); if (n) _idxSizes[name] = n; }
    });
  }
}
setInterval(_refreshIdxSizes, 60000); _refreshIdxSizes();
app.get('/api/indexinfo', (req, res) => {
  const v = (CACHE.get(ckey('full', 'getindexinfo', [], null)) || {}).value || null;
  res.json({ ok: !!v, indexes: v || {}, sizes: _idxSizes, cached: true });
});

// Export the ONE piece of index-like data another node can actually consume: a UTXO snapshot
// (dumptxoutset "latest"). The raw txindex/coinstatsindex LevelDBs are node-local (txindex maps
// txid → byte offset in THIS node's blk*.dat, unique per node) and cannot be shared; a UTXO
// snapshot is portable and loads on any node via `loadtxoutset` for fast assumeUTXO bootstrap.
// "latest" avoids the rollback that would suspend network activity. Guarded by NODE_CONTROL; heavy.
let _utxoExporting = false;
app.post('/api/index/export-utxo', (req, res) => {
  if (!NODE_CONTROL) return res.status(403).json({ ok: false, error: 'node control disabled (BANKON_NODE_CONTROL=0)' });
  if (_utxoExporting) return res.json({ ok: false, error: 'a UTXO export is already in progress' });
  const dir = process.env.BANKON_EXPORT_DIR || join(homedir(), 'bankon-tools', 'exports');
  try { mkdirSync(dir, { recursive: true }); } catch (e) { return res.json({ ok: false, error: String(e.message) }); }
  const out = join(dir, 'bankon-utxo-snapshot.dat');
  try { if (existsSync(out)) rmSync(out); } catch {}       // dumptxoutset refuses to overwrite
  _utxoExporting = true;
  const t0 = Date.now();
  execFile(`${BTC_BIN}/bitcoin-cli`, [`-datadir=${BTC_DATADIR}`, '-rpcclienttimeout=0', 'dumptxoutset', out, 'latest'],
    { timeout: 45 * 60 * 1000, maxBuffer: 1 << 20 }, (err, stdout, stderr) => {
      _utxoExporting = false;
      if (err) return res.json({ ok: false, error: String((stderr || err.message || '').trim()) });
      let info; try { info = JSON.parse(stdout); } catch { info = { raw: (stdout || '').trim() }; }
      res.json({ ok: true, path: out, url: `/exports/${encodeURIComponent('bankon-utxo-snapshot.dat')}`,
                 elapsedSec: Math.round((Date.now() - t0) / 1000), result: info });
      // integrity sidecar so a SHARED snapshot is verifiable (async, best-effort)
      execFile('sha256sum', [out], { cwd: dir, timeout: 30 * 60 * 1000, maxBuffer: 1 << 20 }, (e, so) => {
        if (!e && so) { try { writeFileSync(out + '.sha256', so); } catch {} }
      });
    });
});

// ── Blockchain EXPORT SERVICE ──────────────────────────────────────────────────────────────
// The export endpoints (UTXO snapshot · recent-block slices) write artifacts to EXPORT_DIR. Serve
// them over HTTP + a manifest so OTHER nodes can fetch them and bootstrap — the export as a service.
// (The full-chain → pgvectorscale export is the searchable/queryable half; this is the file half.)
const EXPORT_DIR = process.env.BANKON_EXPORT_DIR || join(homedir(), 'bankon-tools', 'exports');
try { mkdirSync(EXPORT_DIR, { recursive: true }); } catch {}
app.use('/exports', express.static(EXPORT_DIR, { index: false, maxAge: 0 }));   // read-only downloads
app.get('/api/exports', (req, res) => {
  const kind = (n) => n.includes('utxo') ? 'utxo-snapshot' : /blk|block/.test(n) ? 'block-slice' : n.endsWith('.sha256') ? 'checksum' : 'artifact';
  try {
    const files = readdirSync(EXPORT_DIR).map(name => {
      try {
        const st = statSync(join(EXPORT_DIR, name));
        if (!st.isFile()) return null;
        return { name, kind: kind(name), bytes: st.size, mtime: Math.floor(st.mtimeMs / 1000),
                 url: `/exports/${encodeURIComponent(name)}` };
      } catch { return null; }
    }).filter(Boolean).sort((a, b) => b.mtime - a.mtime);
    res.json({ ok: true, dir: EXPORT_DIR, count: files.length, files });
  } catch (e) { res.json({ ok: false, error: String(e.message) }); }
});

// ── rageBTC .bitcoin DISCOVERY — find every datadir on local + external devices, pick the largest ──
// Fast: bounded BFS (depth ≤6) over $HOME + mount roots, skip-list for heavy trees, hard 15 s
// deadline, sizes from blocks/blk*.dat metadata only (never du over 800 GB). Runs on FIRST STARTUP
// and persists every location ever seen to .history — so a datadir on an unplugged drive is
// still remembered (lastSeen tells you it's offline).
const DDH_FILE = join(__dir, '.history');
const _ddHistLoad = () => { try { return JSON.parse(readFileSync(DDH_FILE, 'utf8')); } catch { return {}; } };
const _ddHistSave = (h) => { try { writeFileSync(DDH_FILE, JSON.stringify(h, null, 2)); } catch {} };
let _ddLast = null, _ddRunning = false;
const DD_SKIP = new Set(['node_modules', '__pycache__', 'Trash', 'lost+found', 'snap', 'flatpak',
  'venv', 'proc', 'sys', 'dev', 'run', 'tmp']);
function _ddMounts() {
  try {
    return readFileSync('/proc/mounts', 'utf8').split('\n').filter(l => l.startsWith('/dev/'))
      .map(l => { const p = l.split(' '); return { dev: p[0], mnt: p[1].replace(/\\040/g, ' ') }; })
      .sort((a, b) => b.mnt.length - a.mnt.length);
  } catch { return []; }
}
function _ddScore(p) {                    // chain size from blk*.dat metadata — cheap even on USB
  let blk = 0, bytes = 0;
  try {
    for (const f of readdirSync(join(p, 'blocks'))) {
      if (f.startsWith('blk') && f.endsWith('.dat')) { blk++; try { bytes += statSync(join(p, 'blocks', f)).size; } catch {} }
    }
  } catch {}
  return { blk, bytes };
}
// Well-known layouts probed FIRST (instant, no traversal): ~/.bitcoin, /home/*/.bitcoin,
// <mount>/.bitcoin and <mount>/home/*/.bitcoin for every /media|/mnt|/run/media volume.
function _ddProbes() {
  const out = [join(homedir(), '.bitcoin')];
  const users = (p) => { try { return readdirSync(p).map(u => join(p, u)); } catch { return []; } };
  for (const u of users('/home')) out.push(join(u, '.bitcoin'));
  const vols = [];
  for (const base of ['/media', '/run/media']) for (const u of users(base)) vols.push(...users(u));
  vols.push(...users('/mnt'));
  for (const v of vols) {
    out.push(join(v, '.bitcoin'));
    for (const hu of users(join(v, 'home'))) out.push(join(hu, '.bitcoin'));
  }
  return out;
}
async function findDatadirs() {
  const t0 = Date.now(); const DEADLINE = 15000, MAXD = 6;
  const seen = new Set(); const found = [];
  const consider = (full) => {
    let real; try { real = realpathSync(full); } catch { real = full; }
    if (seen.has(real)) return; seen.add(real);
    if (existsSync(join(real, 'blocks')) || existsSync(join(real, 'bitcoin.conf'))) found.push(real);
  };
  for (const pr of _ddProbes()) if (existsSync(pr)) consider(pr);      // phase 1: instant
  // phase 2: breadth-first sweep for exotic locations — ASYNC (never blocks the event loop),
  // shallow-first so a deadline hit only costs the deep tail.
  const q = [homedir(), '/home', '/media', '/mnt', '/run/media'].filter(r => existsSync(r)).map(r => ({ p: r, d: 0 }));
  for (let i = 0; i < q.length && Date.now() - t0 < DEADLINE; i++) {
    const { p, d } = q[i];
    let ents; try { ents = await fsp.readdir(p, { withFileTypes: true }); } catch { continue; }
    for (const e of ents) {
      if (!e.isDirectory() || e.isSymbolicLink()) continue;            // never follow symlinks (loops)
      if (e.name === '.bitcoin') { consider(join(p, e.name)); continue; }
      if (d >= MAXD || e.name.startsWith('.') || DD_SKIP.has(e.name)) continue;
      q.push({ p: join(p, e.name), d: d + 1 });
    }
  }
  const mounts = _ddMounts();
  let cur = null; try { cur = realpathSync(join(homedir(), '.bitcoin')); } catch {}
  const dirs = found.map(p => {
    const { blk, bytes } = _ddScore(p);
    const m = mounts.find(m => p === m.mnt || p.startsWith(m.mnt.endsWith('/') ? m.mnt : m.mnt + '/')) || {};
    return { path: p, blkFiles: blk, bytes, gib: +(bytes / 2 ** 30).toFixed(1),
             device: m.dev || null, mount: m.mnt || null,
             external: !!(m.mnt && /^\/(media|mnt|run\/media)\//.test(m.mnt)),
             current: p === cur };
  }).sort((a, b) => b.bytes - a.bytes);
  if (dirs.length) dirs[0].largest = true;                            // pick the LARGEST .bitcoin
  const h = _ddHistLoad(); const now = Math.floor(Date.now() / 1000);     // persistence: .history
  for (const d of dirs) h[d.path] = { ...(h[d.path] || { firstSeen: now }), lastSeen: now,
    bytes: d.bytes, blkFiles: d.blkFiles, device: d.device, mount: d.mount, external: d.external };
  if (cur) h._current = { path: cur, asOf: now };
  _ddHistSave(h);
  _ddLast = { ts: Date.now(), elapsedMs: Date.now() - t0, dirs, largest: dirs[0]?.path || null, current: cur };
  return _ddLast;
}
app.get('/api/datadirs', (req, res) => {
  try {
    if (req.query.fresh !== '1' && _ddLast && Date.now() - _ddLast.ts < 10 * 60 * 1000)
      return res.json({ ok: true, cached: true, history: _ddHistLoad(), ..._ddLast });
    if (_ddRunning) return res.json({ ok: true, running: true, history: _ddHistLoad(), ...(_ddLast || {}) });
    _ddRunning = true;
    findDatadirs()
      .then(r => res.json({ ok: true, cached: false, history: _ddHistLoad(), ...r }))
      .catch(e => res.json({ ok: false, error: String(e.message || e) }))
      .finally(() => { _ddRunning = false; });
  } catch (e) { res.json({ ok: false, error: String(e.message || e) }); }
});
// .history hygiene — delete, or TRUE-SHRED (N overwrite passes + final zero pass, then unlink).
app.post('/api/datadirs/history/clear', (req, res) => {
  if (!NODE_CONTROL) return res.status(403).json({ ok: false, error: 'node control disabled' });
  if (!existsSync(DDH_FILE)) return res.json({ ok: true, existed: false });
  if (req.body?.shred) {
    const passes = Math.max(1, Math.min(35, Number(req.body?.passes) || 7));   // default 7 passes
    execFile('shred', ['-u', '-z', '-n', String(passes), DDH_FILE], { timeout: 60000 }, (err) => {
      if (err) return res.json({ ok: false, error: String(err.message) });
      res.json({ ok: true, shredded: true, passes });
    });
    return;
  }
  try { rmSync(DDH_FILE); res.json({ ok: true, deleted: true }); }
  catch (e) { res.json({ ok: false, error: String(e.message) }); }
});
setTimeout(() => {                                                   // FIRST-STARTUP discovery
  findDatadirs()
    .then(r => console.log(`[rageBTC] datadir discovery: ${r.dirs.length} found in ${r.elapsedMs}ms · largest = ${r.largest}`))
    .catch(e => console.error('[rageBTC] datadir discovery failed:', e.message));
}, 4000);

// Host system — CPU usage %, load, temperature (°C), memory. Cheap local reads, no node RPC.
function _cpuTimes() {
  let idle = 0, total = 0;
  for (const c of oscpus()) { for (const k in c.times) total += c.times[k]; idle += c.times.idle; }
  return { idle, total };
}
app.get('/api/system', async (req, res) => {
  const a = _cpuTimes();
  await new Promise(r => setTimeout(r, 150));
  const b = _cpuTimes();
  const dt = b.total - a.total, di = b.idle - a.idle;
  const cpuPct = dt > 0 ? Math.round((1 - di / dt) * 100) : 0;
  let tempC = null, tempLabel = null;
  try {
    for (const z of readdirSync('/sys/class/thermal').filter(d => d.startsWith('thermal_zone'))) {
      try {
        const t = parseInt(readFileSync(`/sys/class/thermal/${z}/temp`, 'utf8'), 10) / 1000;
        if (t > 0 && t < 150 && (tempC === null || t > tempC)) {
          tempC = Math.round(t);
          try { tempLabel = readFileSync(`/sys/class/thermal/${z}/type`, 'utf8').trim(); } catch {}
        }
      } catch {}
    }
  } catch {}
  res.json({ ok: true, cpuPct, load1: +loadavg()[0].toFixed(2), cores: oscpus().length,
    tempC, tempLabel, memUsedPct: Math.round((1 - freemem() / totalmem()) * 100),
    memTotalGB: +(totalmem() / 1073741824).toFixed(1) });
});

// Filesystem monitor — the drive the node's datadir lives on (here: external 2 TB).
// df is cheap; we avoid du on the ~800 GB blocks dir and use size_on_disk instead.
// Datadir component sizes — background `du` on the RESOLVED (external-device) path. Heavy (blocks
// ~800 G) so cached + refreshed slowly; works even when the node is DOWN (pure filesystem).
const _realDatadir = () => { try { return realpathSync(BTC_DATADIR); } catch { return BTC_DATADIR; } };
let _ddSizes = {};
function _refreshDatadirSizes() {
  const real = _realDatadir();
  for (const sub of ['blocks', 'indexes', 'chainstate', 'wallets']) {
    const p = join(real, sub);
    if (!existsSync(p)) { delete _ddSizes[sub]; continue; }
    execFile('du', ['-sb', '-L', p], { timeout: 60000, maxBuffer: 1 << 20 }, (e, o) => {   // -L follows the chainstate symlink
      if (!e && o) { const n = parseInt(o.split(/\s+/)[0], 10); if (n) _ddSizes[sub] = n; }
    });
  }
  execFile('du', ['-sb', real], { timeout: 90000, maxBuffer: 1 << 20 }, (e, o) => {          // on-external total (symlinks not followed)
    if (!e && o) { const n = parseInt(o.split(/\s+/)[0], 10); if (n) _ddSizes.total = n; }
  });
}
setInterval(_refreshDatadirSizes, 30 * 60 * 1000); setTimeout(_refreshDatadirSizes, 3000);
// Top-level listing of the actual files at BANKON's datadir path (read-only; the "view the files").
function _datadirListing() {
  const real = _realDatadir();
  try {
    return readdirSync(real).map(name => {
      try {
        const st = statSync(join(real, name));
        return { name, isDir: st.isDirectory(), bytes: st.isDirectory() ? (_ddSizes[name] ?? null) : st.size,
                 mtime: Math.floor(st.mtimeMs / 1000), link: st.isSymbolicLink?.() || false };
      } catch { return { name, isDir: false, bytes: null }; }
    }).sort((a, b) => (b.bytes || 0) - (a.bytes || 0));
  } catch { return []; }
}
app.get('/api/filesystem', (req, res) => {
  execFile('df', ['-B1', '--output=source,fstype,size,used,avail,pcent,target', BTC_DATADIR],
    { timeout: 6000 }, async (err, out) => {
      let df = null;
      if (out) {
        const cols = (out.trim().split('\n')[1] || '').trim().split(/\s+/);
        df = { source: cols[0], fstype: cols[1], size: +cols[2], used: +cols[3],
               avail: +cols[4], pcent: cols[5], mount: cols.slice(6).join(' ') };
      }
      const chainBytes = (CACHE.get(ckey('full', 'getblockchaininfo', [], null)) || {}).value?.size_on_disk ?? null;
      res.json({ ok: true, datadir: BTC_DATADIR, realPath: _realDatadir(), df, chainBytes,
                 components: _ddSizes, files: req.query.files === '1' ? _datadirListing() : undefined,
                 error: err ? String(err.message) : null });
    });
});

setInterval(warmCheap, Number(process.env.BANKON_WARM_MS) || 25000); warmCheap();
setInterval(warmChain, 30000); warmChain();
if (HAMMER) { setInterval(burstBlocks, 60000); burstBlocks(); }   // continuous burst only if opted in

app.listen(PORT, '127.0.0.1', () =>
  console.log(`BANKON Console on http://127.0.0.1:${PORT}  (read-only RPCs + node control + cache warming; full :8332 / pruned :8342)`));
