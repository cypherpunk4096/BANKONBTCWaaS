# BANKON node intelligence → PostgreSQL + pgvectorscale

Collect the **live Bitcoin network** from your **single Bitcoin Core instance** into Postgres
with a vector embedding, for similarity search with
[pgvectorscale](https://github.com/timescale/pgvectorscale) (Timescale's StreamingDiskANN).
No external API — the node's own `getnodeaddresses` (addrman) + `getpeerinfo` (live peers) are
the source of truth (bitnodes.io is unreliable/down; this needs no third party).

Each node row carries **geographic location** (GeoLite2: lat/lon, country, city, ASN/org),
**software version** (user agent / protocol — Bitcoin Core or equivalent), and **uptime**
tracking (first/last seen, observation & connection counts, `reachable_pct` view).

## 1. Database (one-time)
Install pgvectorscale (see its repo), then:
```bash
createdb bankon_nodes
psql "postgres:///bankon_nodes" -f db/schema.sql     # creates extensions, table, diskann index
export DATABASE_URL="postgresql://user:pass@host:5432/bankon_nodes"
```
`schema.sql` runs `CREATE EXTENSION vector;` + `CREATE EXTENSION vectorscale CASCADE;` and builds
the `bitcoin_nodes` table, the `bitcoin_node_uptime` view, and the StreamingDiskANN index.

## 2. Collect
```bash
cd ~/bankon-tools/bankon-waas
node node-collector.mjs            # one pass: upsert addrman + peers
node node-collector.mjs --dry      # test: gather + geo + embed, no DB write
```
Or via the WaaS API (server must have `DATABASE_URL` set):
```bash
curl -X POST http://127.0.0.1:8088/api/nodes/collect      # run a pass
curl       http://127.0.0.1:8088/api/nodes/stats          # counts, top countries/versions/ASNs, uptime
```
Continuous (opt-in): set `BANKON_COLLECT_MS=600000` (10 min) and `DATABASE_URL` before starting
the WaaS — it then collects on a background loop.

## 3. The embedding (`vector(8)`)
Per node, normalized for cosine ANN:

| dim | meaning |
|-----|---------|
| 0–2 | ECEF unit vector (x,y,z) from WGS84 lat/lon — geographic position |
| 3   | latitude / 90 |
| 4   | longitude / 180 |
| 5   | log10(ASN+1) / 6 — network/provider |
| 6   | services low byte / 255 |
| 7   | (protocol_version − 70000) / 200 |

Geography dominates, so nearest-neighbours are geographically close nodes with similar network
profile. Nodes without a GeoIP fix get a `NULL` embedding (stored, but not ANN-indexed).

## 4. Query examples
```sql
-- 20 nodes most similar (geo + network) to a query vector :: vector(8)
SELECT address, port, country_code, asn_org, user_agent, embedding <=> $1 AS distance
FROM bitcoin_nodes ORDER BY embedding <=> $1 LIMIT 20;

-- software version distribution (Bitcoin Core or equivalent)
SELECT user_agent, count(*) FROM bitcoin_nodes
WHERE user_agent IS NOT NULL GROUP BY 1 ORDER BY 2 DESC LIMIT 25;

-- geographic distribution
SELECT country, count(*) FROM bitcoin_nodes GROUP BY 1 ORDER BY 2 DESC LIMIT 25;

-- uptime / reachability leaderboard
SELECT * FROM bitcoin_node_uptime ORDER BY reachable_pct DESC NULLS LAST, observations DESC LIMIT 25;
```

## Notes
- **Privacy / accuracy:** IP geolocation is approximate (resolves to population centres); node
  addresses are public P2P gossip. Disclose both downstream.
- **Uptime** is approximated by how often a node appears across passes (`observations`) and how
  often it was a live peer (`connections`) — not active probing.
- Config: `DATABASE_URL` (or `BANKON_DATABASE_URL`), `BANKON_GEOIP_DIR` (default `../geoip`),
  `BANKON_COLLECT_MS` (background interval), `RPC_TIMEOUT_MS`.
