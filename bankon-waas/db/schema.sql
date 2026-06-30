-- BANKON node-intelligence schema — PostgreSQL + pgvector + pgvectorscale (StreamingDiskANN).
--
-- Stores the live Bitcoin network as seen from a SINGLE Bitcoin Core instance: the node's
-- addrman (getnodeaddresses) plus live peers (getpeerinfo), enriched with GeoIP, with a
-- feature embedding for vector similarity search ("nodes like this one" by geography +
-- network profile). No external API — the node is the source of truth.
--
-- Apply once:  psql "$DATABASE_URL" -f db/schema.sql
-- pgvectorscale install: https://github.com/timescale/pgvectorscale

CREATE EXTENSION IF NOT EXISTS vector;                 -- pgvector
CREATE EXTENSION IF NOT EXISTS vectorscale CASCADE;    -- pgvectorscale (StreamingDiskANN); CASCADE pulls vector

CREATE TABLE IF NOT EXISTS bitcoin_nodes (
  address          text        NOT NULL,             -- IP (or onion/i2p host)
  port             integer     NOT NULL,
  network          text,                             -- ipv4 | ipv6 | onion | i2p | cjdns

  -- geographic location (GeoLite2)
  latitude         double precision,
  longitude        double precision,
  country          text,
  country_code     text,
  city             text,
  asn              bigint,
  asn_org          text,

  -- software / version: Bitcoin Core or equivalent (Knots, btcd, …)
  user_agent       text,                             -- e.g. /Satoshi:27.0.0/
  protocol_version integer,
  services         bigint,
  start_height     integer,

  -- uptime / availability tracking (accumulated across collection passes)
  first_seen       timestamptz NOT NULL DEFAULT now(),
  last_seen        timestamptz NOT NULL DEFAULT now(),
  last_connected   timestamptz,                      -- last live getpeerinfo connection
  observations     bigint      NOT NULL DEFAULT 1,   -- passes this node appeared in (addrman or peer)
  connections      bigint      NOT NULL DEFAULT 0,   -- passes seen as a live peer
  addr_time        timestamptz,                      -- addrman self-reported last activity
  source           text,                             -- addrman | peer

  -- feature embedding for pgvectorscale ANN (8 dims; see node-collector.mjs::embed)
  --   0-2 ECEF unit (x,y,z)  ·  3 lat/90  ·  4 lon/180  ·  5 log10(asn)/6  ·  6 services-byte/255  ·  7 protocol norm
  embedding        vector(8),

  PRIMARY KEY (address, port)
);

-- Uptime/availability estimate: fraction of observed passes the node was reachable as a peer.
CREATE OR REPLACE VIEW bitcoin_node_uptime AS
SELECT address, port, network, user_agent, country_code, asn_org,
       observations, connections, first_seen, last_seen, last_connected,
       EXTRACT(EPOCH FROM (last_seen - first_seen))                       AS tracked_seconds,
       CASE WHEN observations > 0
            THEN round((connections::numeric / observations) * 100, 1)
            ELSE NULL END                                                  AS reachable_pct
FROM bitcoin_nodes;

-- pgvectorscale StreamingDiskANN index — fast approximate nearest-neighbour over the embedding.
CREATE INDEX IF NOT EXISTS bitcoin_nodes_embedding_diskann
  ON bitcoin_nodes USING diskann (embedding vector_cosine_ops);

-- secondary indexes for the stats/filters
CREATE INDEX IF NOT EXISTS bitcoin_nodes_country   ON bitcoin_nodes (country_code);
CREATE INDEX IF NOT EXISTS bitcoin_nodes_asn       ON bitcoin_nodes (asn);
CREATE INDEX IF NOT EXISTS bitcoin_nodes_ua        ON bitcoin_nodes (user_agent);
CREATE INDEX IF NOT EXISTS bitcoin_nodes_last_seen ON bitcoin_nodes (last_seen);

-- ── Example queries ──────────────────────────────────────────────────────────────────
-- ANN: 20 nodes most similar (geo + network) to a query embedding $1 :: vector(8)
--   SELECT address, port, country_code, asn_org, user_agent, embedding <=> $1 AS distance
--   FROM bitcoin_nodes ORDER BY embedding <=> $1 LIMIT 20;
-- Version distribution (Bitcoin Core or equivalent):
--   SELECT user_agent, count(*) FROM bitcoin_nodes WHERE user_agent IS NOT NULL
--   GROUP BY user_agent ORDER BY 2 DESC LIMIT 25;
-- Geographic distribution:
--   SELECT country, count(*) FROM bitcoin_nodes GROUP BY country ORDER BY 2 DESC LIMIT 25;
-- Most reachable nodes (uptime):
--   SELECT * FROM bitcoin_node_uptime ORDER BY reachable_pct DESC NULLS LAST, observations DESC LIMIT 25;
