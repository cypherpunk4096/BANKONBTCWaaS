// chain-exporter.mjs — rageBTC: move the FULL blockchain into a pgvectorscale searchable DB.
//
// Extracts every block+tx from the local UNPRUNED node via the console's rageRPC engine
// (getblock verbosity 3 → full txs + per-input prevout values → exact fees, no extra RPC), enforces
// a per-block accuracy invariant, and writes with a high-throughput COPY path into staging tables +
// idempotent upsert, advancing a height checkpoint only on COMMIT so it is fully RESUMABLE. Runs
// dry-run (extract+transform+verify, no DB writes) when no DATABASE_URL is configured.
//
// Reuses BANKON's rageRPC rpc() (limiter + circuit breaker) — pass it in; do NOT bypass it.
// Concurrency/checkpoint patterns adapted from mindX performance_optimizer.py (asyncpg pool +
// Semaphore-bounded fan-out + circuit breaker), ported to Node.
// pg + pg-copy-streams are loaded LAZILY so the extract/transform/dry-run path (and unit tests) work
// without them; only the actual DB write path requires them installed in bankon-console/.
let _pg = null, _copyFrom = null;
async function loadPg() {
  if (!_pg) { _pg = (await import('pg')).default; _copyFrom = (await import('pg-copy-streams')).from; }
  return _pg;
}

const SUBSIDY_HALVING = 210000;
function subsidyAt(h) { const e = Math.floor(h / SUBSIDY_HALVING); return e >= 64 ? 0 : 50 / 2 ** e; }

// COPY text-format row: tab-separated, \N for null, backslash-escaped.
function tsv(vals) {
  return vals.map(v => (v === null || v === undefined) ? '\\N'
    : String(v).replace(/\\/g, '\\\\').replace(/\t/g, '\\t').replace(/\n/g, '\\n').replace(/\r/g, '\\r')
  ).join('\t') + '\n';
}
const vec = a => (a && a.length) ? `[${a.map(x => +(+x).toFixed(6)).join(',')}]` : null;
const clamp01 = x => Math.max(0, Math.min(1, x));

// ---- feature embeddings (deterministic, no model) ----
function embedBlock(b, totalFee) {
  const t = b.time ? new Date(b.time * 1000) : null;
  return [clamp01(Math.log10((b.nTx || 1)) / 6), clamp01(Math.log10((b.size || 1)) / 9),
          clamp01((b.weight || 0) / 4e6), clamp01(Math.log10((b.difficulty || 1) + 1) / 15),
          clamp01((totalFee || 0) / 5), t ? t.getUTCHours() / 24 : 0, t ? t.getUTCDay() / 7 : 0,
          clamp01(subsidyAt(b.height) / 50)];
}
function embedTx(tx, fee, totalOut, isCb) {
  const segwit = tx.vin.some(i => i.txinwitness && i.txinwitness.length) ? 1 : 0;
  const rbf = tx.vin.some(i => (i.sequence ?? 0xffffffff) < 0xfffffffe && !i.coinbase) ? 1 : 0;
  const feerate = (fee != null && tx.vsize) ? (fee * 1e8 / tx.vsize) : 0;
  return [clamp01(Math.log10((fee || 0) * 1e8 + 1) / 8), clamp01(Math.log10((totalOut || 0) + 1) / 12),
          clamp01((tx.vin.length) / 50), clamp01((tx.vout.length) / 50), clamp01((tx.vsize || 0) / 100000),
          isCb ? 1 : 0, segwit, rbf, 0, clamp01(feerate / 500), tx.locktime ? 1 : 0, 0];
}

// address + type from a scriptPubKey (verbosity-2/3 output or prevout)
function addrOf(spk) {
  if (!spk) return [null, null];
  return [spk.address || (Array.isArray(spk.addresses) ? spk.addresses[0] : null) || null, spk.type || null];
}

// Transform ONE verbosity-3 block into rows + run the accuracy invariant.
function transformBlock(b) {
  const rows = { blocks: [], transactions: [], tx_inputs: [], tx_outputs: [], addrAgg: new Map() };
  const height = b.height, time = b.time ? new Date(b.time * 1000).toISOString() : null,
        mtime = b.mediantime ? new Date(b.mediantime * 1000).toISOString() : null;
  let blockFee = 0;
  const bumpAddr = (addr, value, ht) => {
    if (!addr) return; const a = rows.addrAgg.get(addr) || { n: 0, recv: 0, first: ht, last: ht };
    a.n++; a.recv += value || 0; a.first = Math.min(a.first, ht); a.last = Math.max(a.last, ht);
    rows.addrAgg.set(addr, a);
  };
  b.tx.forEach((tx, ti) => {
    const isCb = !!(tx.vin[0] && tx.vin[0].coinbase);
    let inSum = 0, resolved = true;
    tx.vin.forEach((vi, k) => {
      let value = null, address = null, coinbase = null;
      if (vi.coinbase) { try { coinbase = Buffer.from(vi.coinbase, 'hex').toString('latin1').replace(/[^\x20-\x7e]/g, '.'); } catch {} }
      else if (vi.prevout) { value = vi.prevout.value; [address] = addrOf(vi.prevout.scriptPubKey); if (value != null) inSum += value; else resolved = false; }
      else resolved = false;
      rows.tx_inputs.push([tx.txid, k, vi.txid || null, vi.vout ?? null, value, address, vi.sequence ?? null, coinbase]);
    });
    let outSum = 0;
    tx.vout.forEach(vo => {
      const [address, type] = addrOf(vo.scriptPubKey); outSum += vo.value || 0;
      rows.tx_outputs.push([tx.txid, vo.n, vo.value, address, type]);
      bumpAddr(address, vo.value, height);
    });
    const fee = isCb ? null : (typeof tx.fee === 'number' ? tx.fee : (resolved ? inSum - outSum : null));
    if (fee != null) blockFee += fee;
    rows.transactions.push([tx.txid, height, ti, tx.size, tx.vsize, tx.weight, tx.version, tx.locktime,
      isCb, fee, tx.vin.length, tx.vout.length, outSum, vec(embedTx(tx, fee, outSum, isCb))]);
  });
  // ACCURACY INVARIANT: tx-row count must equal the block's own nTx.
  const invariant = { height, nTx: b.nTx, txRows: rows.transactions.length,
                      ok: (b.nTx == null || b.nTx === rows.transactions.length) };
  rows.blocks.push([height, b.hash, time, mtime, b.nTx, b.size, b.weight, b.version, b.merkleroot,
    b.bits, b.difficulty, parseInt(b.nonce, 10) || 0, b.previousblockhash || null,
    blockFee.toFixed(8), subsidyAt(height).toFixed(8), vec(embedBlock(b, blockFee))]);
  return { rows, invariant };
}

const TABLES = {
  blocks: 'height,hash,time,mediantime,n_tx,size,weight,version,merkleroot,bits,difficulty,nonce,previousblockhash,total_fee,subsidy,embedding',
  transactions: 'txid,block_height,idx_in_block,size,vsize,weight,version,locktime,is_coinbase,fee,in_count,out_count,total_out,embedding',
  tx_inputs: 'txid,vin,prev_txid,prev_vout,value,address,sequence,coinbase',
  tx_outputs: 'txid,vout,value,address,script_type',
};

export function createChainExporter({ rpc, databaseUrl, schema = 'public', log = () => {} }) {
  let _running = false, _stop = false, _pool = null;
  let _p = { status: 'idle', height: -1, tip: null, blocksDone: 0, txDone: 0, startedAt: null,
             updatedAt: null, rateBlkS: 0, etaSec: null, lastError: null, dryRun: !databaseUrl };

  async function pool() { await loadPg(); if (!_pool) _pool = new _pg.Pool({ connectionString: databaseUrl, max: 4 }); return _pool; }

  async function copyInto(client, table, cols, rows) {
    if (!rows.length) return;
    await client.query(`CREATE TEMP TABLE _stg (LIKE ${schema}.${table} INCLUDING DEFAULTS) ON COMMIT DROP`);
    await new Promise((res, rej) => {
      const s = client.query(_copyFrom(`COPY _stg (${cols}) FROM STDIN`));
      s.on('error', rej); s.on('finish', res);
      for (const r of rows) s.write(tsv(r)); s.end();
    });
    // idempotent: never clobber committed rows (blocks/tx/io are immutable by PK)
    await client.query(`INSERT INTO ${schema}.${table} (${cols}) SELECT ${cols} FROM _stg ON CONFLICT DO NOTHING`);
    await client.query(`DROP TABLE _stg`);
  }

  async function writeBatch(batch) {                       // batch = merged rows across N blocks
    const client = await (await pool()).connect();
    try {
      await client.query('BEGIN');
      for (const [t, cols] of Object.entries(TABLES)) await copyInto(client, t, cols, batch.rows[t]);
      // addresses: pre-aggregated per batch → single upsert, accumulate
      if (batch.addr.size) {
        const vals = [...batch.addr.entries()].map(([addr, a]) =>
          `(${client.escapeLiteral(addr)},${a.first},${a.last},${a.n},${a.recv.toFixed(8)})`).join(',');
        await client.query(`INSERT INTO ${schema}.addresses (address,first_height,last_height,n_outputs,total_received)
          VALUES ${vals}
          ON CONFLICT (address) DO UPDATE SET
            first_height   = LEAST(addresses.first_height, EXCLUDED.first_height),
            last_height    = GREATEST(addresses.last_height, EXCLUDED.last_height),
            n_outputs      = addresses.n_outputs + EXCLUDED.n_outputs,
            total_received = addresses.total_received + EXCLUDED.total_received`);
      }
      await client.query(`UPDATE ${schema}.chain_export_progress SET last_height=$1, tip_height=$2,
        blocks_done=blocks_done+$3, tx_done=tx_done+$4, status='running', updated_at=now() WHERE id=1`,
        [batch.maxHeight, _p.tip, batch.nBlocks, batch.nTx]);
      await client.query('COMMIT');
    } catch (e) { await client.query('ROLLBACK').catch(() => {}); throw e; }
    finally { client.release(); }
  }

  async function readCheckpoint() {
    if (_p.dryRun) return -1;
    const client = await (await pool()).connect();
    try {
      await client.query(`SELECT 1`);   // ensure connectable
      const r = await client.query(`SELECT last_height FROM ${schema}.chain_export_progress WHERE id=1`);
      return r.rows[0]?.last_height ?? -1;
    } finally { client.release(); }
  }

  async function start({ fromHeight, toHeight, resume = true, dryRun = false, batchBlocks = 50 } = {}) {
    if (_running) throw new Error('an export is already running');
    _running = true; _stop = false;
    _p = { ..._p, status: 'running', startedAt: Date.now(), lastError: null, dryRun: dryRun || !databaseUrl };
    (async () => {
      try {
        const tip = await rpc('full', 'getblockcount', [], null, 20000);
        _p.tip = toHeight != null ? Math.min(tip, toHeight) : tip;
        let h = fromHeight != null ? fromHeight : (resume ? (await readCheckpoint()) + 1 : 0);
        if (h < 0) h = 0;
        _p.height = h - 1;
        const t0 = Date.now(); let seen = 0;
        while (h <= _p.tip && !_stop) {
          const batch = { rows: { blocks: [], transactions: [], tx_inputs: [], tx_outputs: [] },
                          addr: new Map(), nBlocks: 0, nTx: 0, maxHeight: h };
          const top = Math.min(h + batchBlocks - 1, _p.tip);
          // fetch this block-range concurrently through the rageRPC limiter (it self-throttles)
          const hs = []; for (let k = h; k <= top; k++) hs.push(k);
          const blocks = await Promise.all(hs.map(async k => {
            const hash = await rpc('full', 'getblockhash', [k], null, 20000);
            return rpc('full', 'getblock', [hash, 3], null, 90000);
          }));
          for (const b of blocks.sort((a, c) => a.height - c.height)) {
            const { rows, invariant } = transformBlock(b);
            if (!invariant.ok) throw new Error(`accuracy invariant failed at height ${invariant.height}: `
              + `nTx=${invariant.nTx} but built ${invariant.txRows} tx rows`);
            for (const t of Object.keys(batch.rows)) batch.rows[t].push(...rows[t]);
            for (const [a, d] of rows.addrAgg) {
              const e = batch.addr.get(a) || { n: 0, recv: 0, first: d.first, last: d.last };
              e.n += d.n; e.recv += d.recv; e.first = Math.min(e.first, d.first); e.last = Math.max(e.last, d.last);
              batch.addr.set(a, e);
            }
            batch.nBlocks++; batch.nTx += rows.transactions.length; batch.maxHeight = b.height;
          }
          if (!_p.dryRun) await writeBatch(batch);
          seen += batch.nBlocks; _p.blocksDone += batch.nBlocks; _p.txDone += batch.nTx; _p.height = top;
          const secs = (Date.now() - t0) / 1000; _p.rateBlkS = +(seen / Math.max(1, secs)).toFixed(2);
          _p.etaSec = _p.rateBlkS > 0 ? Math.round((_p.tip - top) / _p.rateBlkS) : null;
          _p.updatedAt = Date.now();
          h = top + 1;
        }
        _p.status = _stop ? 'paused' : 'done';
      } catch (e) {
        _p.status = 'error'; _p.lastError = String(e.message || e); log('chain-export error: ' + _p.lastError);
        if (!_p.dryRun) try { const c = await (await pool()).connect(); await c.query(
          `UPDATE ${schema}.chain_export_progress SET status='error', last_error=$1, updated_at=now() WHERE id=1`,
          [_p.lastError]); c.release(); } catch {}
      } finally { _running = false; }
    })();
    return status();
  }

  function stop() { _stop = true; return { stopping: _running }; }
  function status() {
    const pct = (_p.tip && _p.tip > 0) ? +((_p.height + 1) / (_p.tip + 1) * 100).toFixed(3) : 0;
    return { running: _running, ..._p, pct };
  }

  // Verify a range: DB row-counts vs the node's getblockstats.
  async function verify({ from, to }) {
    if (_p.dryRun) return { ok: false, error: 'no DATABASE_URL — verify needs the DB' };
    const client = await (await pool()).connect(); const mismatches = [];
    try {
      for (let h = from; h <= to; h++) {
        const stats = await rpc('full', 'getblockstats', [h, ['txs']], null, 20000);
        const r = await client.query(`SELECT count(*)::int n FROM ${schema}.transactions WHERE block_height=$1`, [h]);
        if (r.rows[0].n !== stats.txs) mismatches.push({ height: h, node: stats.txs, db: r.rows[0].n });
      }
    } finally { client.release(); }
    return { ok: mismatches.length === 0, checked: to - from + 1, mismatches };
  }

  return { start, stop, status, verify, _transformBlock: transformBlock };
}
