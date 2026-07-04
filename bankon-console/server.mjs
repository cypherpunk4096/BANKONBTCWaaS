// server.mjs — BANKON Console: read-only tabbed diagnostics over the background
// Bitcoin Core node(s). Serves the UI, proxies a categorized WHITELIST of
// read-only RPCs, and exposes that catalog so the UI can express every aspect
// of CLI interaction. No spends / no stop / no config writes — safe by design.
import express from 'express';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { readFileSync, existsSync, readdirSync, writeFileSync } from 'node:fs';
import { homedir, cpus as oscpus, loadavg, totalmem, freemem } from 'node:os';
import { spawn, execFile, execSync } from 'node:child_process';
import net from 'node:net';
import { promises as dnsp } from 'node:dns';
import { apiAuth, rateLimit } from '../shared/security.mjs';

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
let _effInflight = RPC.maxInflight;                               // ADAPTIVE cap — backs off under distress, ramps up when healthy
let _okStreak = 0;
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

async function rpc(node, method, params = [], wallet = null, timeoutMs = null) {
  // CIRCUIT BREAKER: if the node recently said "work queue exceeded", don't call it at all for a
  // cooldown — let callers serve cache. This drains the node's queue and stops the flood at the source.
  if (rpcCircuitOpen()) throw new Error('node busy (circuit breaker open)');
  await _acquire();
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
      _distressUntil = Date.now() + RPC.distressMs;               // trip the breaker → back off
      _effInflight = Math.max(2, Math.floor(_effInflight / 4));   // ADAPTIVE: quarter the live cap under distress (sticky)
      _okStreak = 0;
      throw new Error(`node overwhelmed (${res.status}) — backing off ${RPC.distressMs / 1000}s`);
    }
    let json; try { json = JSON.parse(text); } catch { throw new Error(`non-JSON (${res.status}): ${text.slice(0,160)}`); }
    if (json.error) throw new Error(`${json.error.message} (code ${json.error.code})`);
    if (++_okStreak >= 25 && _effInflight < RPC.maxInflight) { _okStreak = 0; _effInflight++; }  // ADAPTIVE: ramp up slowly when sustained-healthy
    return json.result;
  } finally {
    _release();
  }
}

// ---- result cache so the dashboard always shows last-known data -------------
// During IBD the node's RPC is lock-bound; a fresh call may time out. We cache
// every successful read and serve the last-known value (flagged stale) on miss,
// and a background loop keeps the common metrics warm by catching lock gaps.
const CACHE = new Map();   // key -> { value, ts }
const ckey = (node, method, params, wallet) => `${node}|${method}|${JSON.stringify(params)}|${wallet || ''}`;
async function rpcCached(node, method, params = [], wallet = null, timeoutMs = null) {
  const k = ckey(node, method, params, wallet);
  try {
    const value = await rpc(node, method, params, wallet, timeoutMs);
    CACHE.set(k, { value, ts: Date.now() });
    return { value, stale: false, asOf: Date.now() };
  } catch (e) {
    const c = CACHE.get(k);
    if (c) return { value: c.value, stale: true, asOf: c.ts, error: String(e.message || e) };
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
app.get('/api/recentblocks', (req, res) => {
  // IBD-proof + always current: read the last N UpdateTip lines from debug.log (the node writes
  // hash+height+date on every block it connects — no RPC, no cs_main). The burst (RPC, for nTx) is
  // a best-effort enrichment when the node has gaps; the log is the reliable source.
  const n = Math.max(1, Math.min(Number(req.query.n) || 50, RECENT_MAX));
  execFile('bash', ['-c', `grep -a UpdateTip ${DEBUG_LOG} 2>/dev/null | tail -${n}`],
    { timeout: 6000, maxBuffer: 1 << 22 }, (err, out) => {
      const ntx = new Map(RECENT_BLOCKS.map(b => [b.height, b.nTx]));   // nTx from any prior RPC burst
      const blocks = [];
      for (const line of (out || '').trim().split('\n')) {
        if (!line) continue;
        const mh = /best=([0-9a-f]+)/.exec(line), mn = /height=(\d+)/.exec(line), md = /date='([^']+)'/.exec(line);
        if (!mh || !mn) continue;
        const height = +mn[1];
        blocks.push({ height, hash: mh[1],
          time: md ? Math.floor(Date.parse(md[1]) / 1000) : null,
          nTx: ntx.has(height) ? ntx.get(height) : null });
      }
      blocks.reverse();   // newest first
      if (!blocks.length && RECENT_BLOCKS.length)        // fallback to RPC burst cache if log unreadable
        return res.json({ ok: true, blocks: RECENT_BLOCKS.slice(0, n), building, asOf: lastBurst, total: RECENT_BLOCKS.length, source: 'rpc' });
      res.json({ ok: true, blocks, building: false, asOf: Date.now(), total: blocks.length, source: 'debug.log' });
    });
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
app.get('/api/netactivity', (req, res) => {
  const n = Math.min(Number(req.query.n) || 40, 200);
  try {
    const raw = execSync(
      `grep -aE 'peer connected|connect\\(\\) to|disconnecting|Added connection|accepted connection|AddLocal|Bound to|Adding fixed seeds|addresses from peers.dat|new outbound|socket (send|recv)' ${DEBUG_LOG} 2>/dev/null | tail -${n}`,
      { timeout: 5000, maxBuffer: 1 << 20 }).toString();
    const local = [];
    const events = raw.split('\n').filter(Boolean).map(line => {
      const time = (/^(\S+)/.exec(line) || [])[1] || '';
      const addr = (/((?:\d{1,3}\.){3}\d{1,3}(?::\d+)?|\[[0-9a-f:]+\](?::\d+)?)/i.exec(line) || [])[1] || '';
      const peer = (/peer=(\d+)/.exec(line) || [])[1] || '';
      let kind = 'info';
      if (/new (outbound|inbound|manual|block-relay).*peer connected|peer connected/i.test(line)) kind = 'connected';
      else if (/connect\(\) to .* failed/i.test(line)) kind = 'failed';
      else if (/disconnecting/i.test(line)) kind = 'disconnect';
      else if (/accepted connection/i.test(line)) kind = 'inbound';
      else if (/AddLocal|Bound to/i.test(line)) { kind = 'local'; if (addr) local.push(addr); }
      const ver = (/version: (\d+)/.exec(line) || [])[1] || '';
      return { time: time.replace('T', ' ').replace('Z', ''), kind, addr, peer, ver, text: line.slice(20, 160) };
    });
    const tally = { connected: 0, failed: 0, disconnect: 0, inbound: 0 };
    for (const e of events) if (tally[e.kind] != null) tally[e.kind]++;
    res.json({ ok: true, events, tally, local: [...new Set(local)] });
  } catch (e) { res.json({ ok: false, error: String(e.message || e), events: [], tally: {}, local: [] }); }
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
function _score(p, now) {
  const ping = (p.pingtime || p.minping || 1) * 1000;                  // ms
  const recvRate = (p.bytesrecv || 0) / Math.max(1, now - (p.conntime || now));  // bytes/s
  const height = p.synced_blocks || p.startingheight || 0;
  return Math.round(recvRate / 1024 + height / 1e5 - ping / 50);       // higher = faster + more useful
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
  execFile(`${BTC_BIN}/bitcoin-cli`, [`-datadir=${BTC_DATADIR}`, ...args], { timeout: 8000 },
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

// Current settings — complete, live display (RPC policy, circuit-breaker state, warm/burst, control).
function settingsSnapshot() {
  return {
    rpc: { rage: RPC.rage, maxInflight: RPC.maxInflight, effInflight: _effCap(), tier: RPC.rage ? 'RAGE' : RPC.maxInflight,
           steps: [1, 2, 4, 8, 16, 32, 64, 128, 256], timeoutMs: RPC.timeoutMs, distressMs: RPC.distressMs,
           inflight: _inflight, waiting: _waiters.length,
           circuitOpen: rpcCircuitOpen(), distressRemainingMs: Math.max(0, _distressUntil - Date.now()) },
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
app.get('/api/indexinfo', (req, res) => {
  const v = (CACHE.get(ckey('full', 'getindexinfo', [], null)) || {}).value || null;
  res.json({ ok: !!v, indexes: v || {}, cached: true });
});

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
      res.json({ ok: true, datadir: BTC_DATADIR, df, chainBytes, error: err ? String(err.message) : null });
    });
});

setInterval(warmCheap, Number(process.env.BANKON_WARM_MS) || 25000); warmCheap();
setInterval(warmChain, 30000); warmChain();
if (HAMMER) { setInterval(burstBlocks, 60000); burstBlocks(); }   // continuous burst only if opted in

app.listen(PORT, '127.0.0.1', () =>
  console.log(`BANKON Console on http://127.0.0.1:${PORT}  (read-only RPCs + node control + cache warming; full :8332 / pruned :8342)`));
