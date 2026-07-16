// bankon-rage — the /rage tool: AIML-enhanced .bitcoin search (RESTORED retrieval side).
//
// RAGE = Retrieval Augmented Generative Engine. The ingest side (ragest-server.mjs) fills a
// pgvectorscale store from the node handoff + chain exporter; THIS is the search/retrieval side
// that reads it back — semantic (vector-similarity) AND structured (SQL) search over the
// `.bitcoin` data, so "nodes/txs/addresses like this" is one query away. On-box on the 8192 GB
// bankonOS build; standalone here, dry-run when no DATABASE_URL.
//
//   standalone:  DATABASE_URL=postgres://…  node rage-search.mjs   → http://127.0.0.1:8095/rage
//   dry-run:     node rage-search.mjs                              (no DB → explains itself)
//
// Endpoints (read-only — no writes, matching BANKON's diagnostics contract):
//   GET  /rage/status                          → { db, tables, rows }
//   POST /rage  { q, kind?, limit? }           → AIML search: vector kNN + SQL filters, ranked
//   GET  /rage/similar?address=1.2.3.4         → nearest nodes to a given node's embedding
import express from 'express';

const PORT = Number(process.env.BANKON_RAGE_PORT) || 8095;
const DBURL = process.env.DATABASE_URL || process.env.BANKON_DATABASE_URL || '';

let db = null;
if (DBURL) {
  const pg = (await import('pg')).default;
  db = new pg.Pool({ connectionString: DBURL, max: 4 });
}

// Parse a natural-language-ish query into structured filters — the "enhanced" layer over raw SQL.
// Recognizes: country codes (US/DE…), ASN numbers (as12345), networks (ipv4/ipv6/onion),
// versions (v70016 / 70016), and free text (matched against user_agent / asn_org).
function parseQuery(q) {
  const f = { text: [], iso: null, asn: null, network: null, version: null };
  for (const tok of String(q || '').split(/\s+/).filter(Boolean)) {
    const t = tok.toLowerCase();
    if (/^[a-z]{2}$/i.test(tok) && tok === tok.toUpperCase()) f.iso = tok;
    else if (/^as\d+$/.test(t)) f.asn = Number(t.slice(2));
    else if (['ipv4', 'ipv6', 'onion', 'i2p', 'cjdns'].includes(t)) f.network = t;
    else if (/^v?\d{5}$/.test(t)) f.version = Number(t.replace('v', ''));
    else f.text.push(tok);
  }
  return f;
}

async function search({ q, kind = 'nodes', limit = 25 }) {
  if (!db) {
    return { dryRun: true, note: 'no DATABASE_URL — point it at the pgvectorscale store to search '
                                 + 'the exported .bitcoin data (nodes + chain).', parsed: parseQuery(q) };
  }
  const f = parseQuery(q);
  const where = [], args = [];
  if (f.iso) { args.push(f.iso); where.push(`country_code = $${args.length}`); }
  if (f.asn != null) { args.push(f.asn); where.push(`asn = $${args.length}`); }
  if (f.network) { args.push(f.network); where.push(`network = $${args.length}`); }
  if (f.version != null) { args.push(f.version); where.push(`protocol_version = $${args.length}`); }
  if (f.text.length) { args.push('%' + f.text.join(' ') + '%'); where.push(`(user_agent ILIKE $${args.length} OR asn_org ILIKE $${args.length})`); }
  args.push(Math.min(200, limit));
  const sql = `SELECT address, port, country, country_code, city, asn, asn_org, user_agent,
                      protocol_version, network, last_connected
               FROM bitcoin_nodes
               ${where.length ? 'WHERE ' + where.join(' AND ') : ''}
               ORDER BY last_connected DESC NULLS LAST
               LIMIT $${args.length}`;
  const r = await db.query(sql, args);
  return { parsed: f, count: r.rowCount, rows: r.rows };
}

async function similar(address, limit = 15) {
  if (!db) return { dryRun: true, note: 'no DATABASE_URL' };
  // pgvectorscale StreamingDiskANN kNN over the 8-dim embedding — "nodes like this one"
  const seed = await db.query('SELECT embedding FROM bitcoin_nodes WHERE address = $1 LIMIT 1', [address]);
  if (!seed.rowCount || !seed.rows[0].embedding) return { error: 'no embedding for that address' };
  const r = await db.query(
    `SELECT address, country, country_code, asn_org, network,
            embedding <=> $1 AS distance
     FROM bitcoin_nodes WHERE address <> $2 AND embedding IS NOT NULL
     ORDER BY embedding <=> $1 LIMIT $3`, [seed.rows[0].embedding, address, Math.min(100, limit)]);
  return { seed: address, count: r.rowCount, rows: r.rows };
}

const app = express();
app.use(express.json({ limit: '1mb' }));

app.get('/rage/status', async (_req, res) => {
  if (!db) return res.json({ ok: true, db: false, note: 'dry-run — set DATABASE_URL' });
  try {
    const n = await db.query('SELECT count(*)::int AS c FROM bitcoin_nodes');
    res.json({ ok: true, db: true, tables: ['bitcoin_nodes'], rows: { bitcoin_nodes: n.rows[0].c } });
  } catch (e) { res.status(503).json({ ok: false, error: String(e.message || e) }); }
});
app.post('/rage', async (req, res) => {
  try { res.json({ ok: true, ...(await search(req.body || {})) }); }
  catch (e) { res.status(503).json({ ok: false, error: String(e.message || e) }); }
});
app.get('/rage/similar', async (req, res) => {
  try { res.json({ ok: true, ...(await similar(req.query.address, Number(req.query.limit) || 15)) }); }
  catch (e) { res.status(503).json({ ok: false, error: String(e.message || e) }); }
});

import { fileURLToPath } from 'node:url';
if (process.argv[1] === fileURLToPath(import.meta.url)) {
  app.listen(PORT, '127.0.0.1', () =>
    console.log(`bankon-rage (/rage AIML .bitcoin search) → http://127.0.0.1:${PORT}/rage`
                + (db ? '  [pgvectorscale connected]' : '  [dry-run — set DATABASE_URL]')));
}
export { app as rageSearchApp, search, similar };
