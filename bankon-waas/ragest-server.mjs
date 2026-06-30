// ragest-server.mjs — REFERENCE ingest endpoint for BANKON's node handoff into pgvectorscale.
//
// Pipeline:
//   BANKON node → rageRPC (accelerated RPC) → POST /ragest (web2 URL, bankon.pythai.net)
//     → PostgreSQL pgvectorscale (vector store) ──┬─→ RAGE retrieval/generation (rage.pythai.net,
//                                                 │   Retrieval Augmented Generative Engine)
//                                                 └─→ web3 permaweb bridge
//
// pgvectorscale is the bridge point: the vector(8) embedding (geo + network profile) is what RAGE
// retrieves over, and the same table is the source for the web2→web3 permaweb publish.
//
// Deploy this on bankon.pythai.net to receive the Console's POST /api/rage/handoff and upsert the
// gathered Bitcoin nodes into the pgvectorscale `bitcoin_nodes` table (db/schema.sql). It reuses the
// SAME enrichment + embedding + upsert as the local collector, so rows are identical — no drift.
//
// The ingest URL is  POST /ragest  (named to match the handoff target bankon.pythai.net/ragest).
//
// Run:
//   psql "$DATABASE_URL" -f db/schema.sql                 # once
//   DATABASE_URL=postgres://…  RAGE_TOKEN=…  node ragest-server.mjs
//   (behind nginx/Apache, proxy https://bankon.pythai.net/ragest → http://127.0.0.1:$RAGEST_PORT/ragest)
//
// Payload (from the Console handoff):
//   { "source":"bankon", "kind":"btc-nodes", "count":N,
//     "nodes":[ { "address":"1.2.3.4", "port":8333, "services":1037, "time":..., "network":"ipv4" }, … ] }
import express from 'express';
import pg from 'pg';
import { readers, rowFromAddr, upsert } from './node-collector.mjs';

const PORT  = Number(process.env.RAGEST_PORT) || 8099;
const TOKEN = process.env.RAGE_TOKEN || '';                 // require Bearer auth if set
const DBURL = process.env.DATABASE_URL || process.env.BANKON_DATABASE_URL;
if (!DBURL) { console.error('ragest: DATABASE_URL not set — point it at your pgvectorscale Postgres'); process.exit(1); }
const db = new pg.Pool({ connectionString: DBURL, max: 4 });

const app = express();
app.use(express.json({ limit: '16mb' }));

function auth(req, res, next) {
  if (!TOKEN) return next();                                // open if no token configured (dev)
  if ((req.get('authorization') || '') === 'Bearer ' + TOKEN) return next();
  return res.status(403).json({ ok: false, error: 'forbidden — missing/invalid Bearer token' });
}

// The ingest endpoint — matches https://bankon.pythai.net/ragest
app.post('/ragest', auth, async (req, res) => {
  const { nodes, source = null, kind = null } = req.body || {};
  if (!Array.isArray(nodes)) return res.status(400).json({ ok: false, error: 'expected JSON {nodes:[…]}' });
  let rd;
  try { rd = await readers(); } catch { rd = { city: null, asn: null }; }   // GeoIP optional
  const rows = nodes.map(n => rowFromAddr(n, rd)).filter(Boolean);          // same enrichment + embedding as the collector
  const client = await db.connect();
  let n = 0;
  try {
    await client.query('BEGIN');
    for (const r of rows) { await upsert(client, r); n++; }
    await client.query('COMMIT');
  } catch (e) {
    await client.query('ROLLBACK').catch(() => {});
    client.release();
    return res.status(500).json({ ok: false, error: String(e.message || e), upserted: n });
  }
  client.release();
  res.json({ ok: true, received: nodes.length, upserted: n, source, kind });
});

// Quick health/stats so the handoff can confirm the far side is live.
app.get('/ragest/stats', async (req, res) => {
  try {
    const { rows } = await db.query(
      'SELECT count(*)::int AS nodes, count(DISTINCT country_code)::int AS countries, max(last_seen) AS last_ingest FROM bitcoin_nodes');
    res.json({ ok: true, ...rows[0] });
  } catch (e) { res.status(500).json({ ok: false, error: String(e.message || e) }); }
});

app.listen(PORT, () => console.log(`ragest ingest on :${PORT}  —  POST /ragest  (auth: ${TOKEN ? 'Bearer token' : 'OPEN'})`));
