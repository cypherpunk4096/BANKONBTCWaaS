// node-collector.mjs — collect the live Bitcoin network from ONE Bitcoin Core instance
// into PostgreSQL (pgvector + pgvectorscale). Source: the node's addrman
// (getnodeaddresses) + live peers (getpeerinfo). Enriches each with GeoIP (location,
// ASN/org), tracks uptime/availability across passes, records software version
// (user agent / protocol), and stores a feature embedding for vector similarity search.
//
// No external API — the single node is the source of truth (bitnodes.io is unreliable/down).
import pg from 'pg';
import maxmind from 'maxmind';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { rpc } from './rpc.mjs';

const __dir = dirname(fileURLToPath(import.meta.url));
const GEO_DIR = process.env.BANKON_GEOIP_DIR || join(__dir, '..', 'geoip');
export const DATABASE_URL = process.env.DATABASE_URL || process.env.BANKON_DATABASE_URL || null;

let _pool = null, _city = null, _asn = null, _readersTried = false;

function pool() {
  if (!DATABASE_URL) throw new Error('DATABASE_URL not set — point it at your pgvectorscale Postgres');
  if (!_pool) _pool = new pg.Pool({ connectionString: DATABASE_URL, max: 4 });
  return _pool;
}

export async function readers() {
  if (!_readersTried) {
    _readersTried = true;
    try { _city = await maxmind.open(join(GEO_DIR, 'GeoLite2-City.mmdb')); } catch { _city = null; }
    try { _asn = await maxmind.open(join(GEO_DIR, 'GeoLite2-ASN.mmdb')); } catch { _asn = null; }
  }
  return { city: _city, asn: _asn };
}

// ---- helpers ---------------------------------------------------------------
function splitAddr(addr) {              // "1.2.3.4:8333" or "[2001:db8::1]:8333"
  if (!addr) return [null, null];
  const m = addr.match(/^\[(.+)\]:(\d+)$/);
  if (m) return [m[1], +m[2]];
  const i = addr.lastIndexOf(':');
  return i < 0 ? [addr, null] : [addr.slice(0, i), +addr.slice(i + 1)];
}
function svcNum(s) {                    // hex string ("000000000000040d") or integer
  if (s == null) return null;
  if (typeof s === 'number') return s;
  const n = parseInt(String(s), 16);
  return Number.isFinite(n) ? n : null;
}
// 8-dim feature embedding: geography (ECEF unit + lat/lon) + network (asn, services, version)
export function embed({ lat, lon, asn, services, protocol_version }) {
  if (lat == null || lon == null) return null;
  const p = lat * Math.PI / 180, l = lon * Math.PI / 180;
  const x = Math.cos(p) * Math.cos(l), y = Math.cos(p) * Math.sin(l), z = Math.sin(p);
  const asnf = asn ? Math.min(1, Math.log10(asn + 1) / 6) : 0;
  const svc = services ? (services & 0xff) / 255 : 0;
  const pv = protocol_version ? Math.max(0, Math.min(1, (protocol_version - 70000) / 200)) : 0;
  return [x, y, z, lat / 90, lon / 180, asnf, svc, pv].map(v => +v.toFixed(6));
}
function enrich(ip, { city, asn }) {
  const g = { lat: null, lon: null, country: null, cc: null, cityName: null, asn: null, org: null };
  try {
    const c = city && city.get(ip);
    if (c) { g.lat = c.location?.latitude ?? null; g.lon = c.location?.longitude ?? null;
             g.country = c.country?.names?.en ?? null; g.cc = c.country?.iso_code ?? null;
             g.cityName = c.city?.names?.en ?? null; }
  } catch {}
  try {
    const a = asn && asn.get(ip);
    if (a) { g.asn = a.autonomous_system_number ?? null; g.org = a.autonomous_system_organization ?? null; }
  } catch {}
  return g;
}

export function rowFromAddr(a, rd) {            // getnodeaddresses entry
  const ip = a.address, port = a.port;
  if (!ip || a.network && ['onion', 'i2p', 'cjdns'].includes(a.network)) return null;
  const g = enrich(ip, rd);
  const services = svcNum(a.services);
  return {
    address: ip, port, network: a.network || null,
    latitude: g.lat, longitude: g.lon, country: g.country, country_code: g.cc, city: g.cityName,
    asn: g.asn, asn_org: g.org,
    user_agent: null, protocol_version: null, services, start_height: null,
    last_connected: null, connections: 0, source: 'addrman',
    addr_time: a.time ? new Date(a.time * 1000).toISOString() : null,
    embedding: embed({ lat: g.lat, lon: g.lon, asn: g.asn, services, protocol_version: null }),
  };
}
export function rowFromPeer(p, rd) {             // getpeerinfo entry (richer: version, conntime)
  const [ip, port] = splitAddr(p.addr);
  if (!ip || /\.onion$/.test(ip)) return null;
  const g = enrich(ip, rd);
  const services = svcNum(p.services);
  return {
    address: ip, port, network: p.network || null,
    latitude: g.lat, longitude: g.lon, country: g.country, country_code: g.cc, city: g.cityName,
    asn: g.asn, asn_org: g.org,
    user_agent: p.subver || null, protocol_version: p.version || null, services,
    start_height: p.startingheight ?? null,
    last_connected: new Date().toISOString(), connections: 1, source: 'peer',
    addr_time: p.conntime ? new Date(p.conntime * 1000).toISOString() : null,
    embedding: embed({ lat: g.lat, lon: g.lon, asn: g.asn, services, protocol_version: p.version }),
  };
}

const COLS = ['address','port','network','latitude','longitude','country','country_code','city',
  'asn','asn_org','user_agent','protocol_version','services','start_height','last_connected',
  'connections','source','addr_time','embedding'];

export async function upsert(client, r) {
  const vals = COLS.map(c => c === 'embedding' && r.embedding ? `[${r.embedding.join(',')}]` : r[c]);
  const ph = COLS.map((_, i) => `$${i + 1}`).join(',');
  await client.query(
    `INSERT INTO bitcoin_nodes (${COLS.join(',')}) VALUES (${ph})
     ON CONFLICT (address, port) DO UPDATE SET
       last_seen        = now(),
       observations     = bitcoin_nodes.observations + 1,
       connections      = bitcoin_nodes.connections + EXCLUDED.connections,
       last_connected   = COALESCE(EXCLUDED.last_connected, bitcoin_nodes.last_connected),
       network          = COALESCE(EXCLUDED.network, bitcoin_nodes.network),
       latitude         = COALESCE(EXCLUDED.latitude, bitcoin_nodes.latitude),
       longitude        = COALESCE(EXCLUDED.longitude, bitcoin_nodes.longitude),
       country          = COALESCE(EXCLUDED.country, bitcoin_nodes.country),
       country_code     = COALESCE(EXCLUDED.country_code, bitcoin_nodes.country_code),
       city             = COALESCE(EXCLUDED.city, bitcoin_nodes.city),
       asn              = COALESCE(EXCLUDED.asn, bitcoin_nodes.asn),
       asn_org          = COALESCE(EXCLUDED.asn_org, bitcoin_nodes.asn_org),
       user_agent       = COALESCE(EXCLUDED.user_agent, bitcoin_nodes.user_agent),
       protocol_version = COALESCE(EXCLUDED.protocol_version, bitcoin_nodes.protocol_version),
       services         = COALESCE(EXCLUDED.services, bitcoin_nodes.services),
       start_height     = COALESCE(EXCLUDED.start_height, bitcoin_nodes.start_height),
       addr_time        = COALESCE(EXCLUDED.addr_time, bitcoin_nodes.addr_time),
       embedding        = COALESCE(EXCLUDED.embedding, bitcoin_nodes.embedding),
       source           = CASE WHEN EXCLUDED.connections = 1 THEN 'peer' ELSE bitcoin_nodes.source END`,
    vals);
}

// Run one collection pass. dryRun=true skips the DB and returns a summary (for testing).
export async function collectOnce({ limit = 5000, dryRun = false } = {}) {
  const rd = await readers();
  const [addrs, peers] = await Promise.all([
    rpc('getnodeaddresses', [limit]).catch(() => []),
    rpc('getpeerinfo').catch(() => []),
  ]);
  // peers first so their richer data wins the embedding for shared addresses
  const rows = [];
  for (const p of peers) { const r = rowFromPeer(p, rd); if (r) rows.push(r); }
  for (const a of addrs) { const r = rowFromAddr(a, rd); if (r) rows.push(r); }
  const geocoded = rows.filter(r => r.latitude != null).length;

  if (dryRun) {
    const byCC = {}, byUA = {};
    for (const r of rows) { if (r.country_code) byCC[r.country_code] = (byCC[r.country_code] || 0) + 1;
                            if (r.user_agent) byUA[r.user_agent] = (byUA[r.user_agent] || 0) + 1; }
    const top = o => Object.entries(o).sort((a, b) => b[1] - a[1]).slice(0, 8);
    return { dryRun: true, addrman: addrs.length, peers: peers.length, rows: rows.length,
             geocoded, topCountries: top(byCC), topVersions: top(byUA), sample: rows[0] };
  }

  const client = await pool().connect();
  let n = 0;
  try {
    await client.query('BEGIN');
    for (const r of rows) { await upsert(client, r); n++; }
    await client.query('COMMIT');
  } catch (e) { await client.query('ROLLBACK').catch(() => {}); throw e; }
  finally { client.release(); }
  return { collected: n, addrman: addrs.length, peers: peers.length, geocoded };
}

export async function stats() {
  const q = async (sql) => (await pool().query(sql)).rows;
  const [tot] = await q('SELECT count(*) AS nodes, count(*) FILTER (WHERE latitude IS NOT NULL) AS geocoded FROM bitcoin_nodes');
  return {
    total: tot,
    countries: await q("SELECT country, count(*) FROM bitcoin_nodes WHERE country IS NOT NULL GROUP BY 1 ORDER BY 2 DESC LIMIT 15"),
    versions: await q("SELECT user_agent, count(*) FROM bitcoin_nodes WHERE user_agent IS NOT NULL GROUP BY 1 ORDER BY 2 DESC LIMIT 15"),
    asns: await q("SELECT asn_org, count(*) FROM bitcoin_nodes WHERE asn_org IS NOT NULL GROUP BY 1 ORDER BY 2 DESC LIMIT 10"),
    mostReachable: await q("SELECT address, port, user_agent, country_code, reachable_pct, observations FROM bitcoin_node_uptime ORDER BY reachable_pct DESC NULLS LAST, observations DESC LIMIT 10"),
  };
}

// CLI: `node node-collector.mjs --dry` (test, no DB) | `node node-collector.mjs` (collect)
if (import.meta.url === `file://${process.argv[1]}`) {
  const dry = process.argv.includes('--dry');
  collectOnce({ dryRun: dry }).then(r => { console.log(JSON.stringify(r, null, 2)); process.exit(0); })
    .catch(e => { console.error('collect failed:', e.message); process.exit(1); });
}
