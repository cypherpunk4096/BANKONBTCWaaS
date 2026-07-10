-- BANKON rageBTC chain schema — PostgreSQL + pgvector + pgvectorscale (StreamingDiskANN).
--
-- The FULL Bitcoin blockchain (blocks + every transaction, input, output, address) as extracted
-- from a SINGLE unpruned Bitcoin Core node via getblock verbosity 3, searchable BOTH by structured
-- SQL (txid / address / height / amount) AND by vector similarity (feature embeddings). Companion to
-- schema.sql (node intelligence). No external API — the node is the source of truth.
--
-- Apply once:  psql "$DATABASE_URL" -f db/schema-chain.sql
-- pgvectorscale install: https://github.com/timescale/pgvectorscale
--
-- SCALE: full chain ≈ 1.1B txs / 3B+ outputs / 1–2 TB. Build the diskann indexes at the BOTTOM of
-- this file AFTER the bulk load (they are expensive to maintain during a multi-day COPY ingest).

CREATE EXTENSION IF NOT EXISTS vector;                 -- pgvector
CREATE EXTENSION IF NOT EXISTS vectorscale CASCADE;    -- pgvectorscale (StreamingDiskANN); CASCADE pulls vector

-- ── Blocks ────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS blocks (
  height            integer     PRIMARY KEY,
  hash              text        NOT NULL UNIQUE,
  time              timestamptz,
  mediantime        timestamptz,
  n_tx              integer,
  size              integer,
  weight            integer,
  version           bigint,
  merkleroot        text,
  bits              text,
  difficulty        double precision,
  nonce             bigint,
  previousblockhash text,
  total_fee         numeric(20,8),                      -- Σ tx fees in the block (BTC)
  subsidy           numeric(20,8),                      -- block subsidy at this height (BTC)
  -- 8-dim block feature embedding (see chain-exporter.mjs::embedBlock):
  --   [log10(n_tx)/6, log10(size)/9, weight/4e6, difficulty-norm, feerate-norm, hour/24, dow/7, subsidy-norm]
  embedding         vector(8)
);

-- ── Transactions ──────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS transactions (
  txid          text        PRIMARY KEY,
  block_height  integer     REFERENCES blocks(height) ON DELETE CASCADE,
  idx_in_block  integer,                                -- position in the block (0 = coinbase)
  size          integer,
  vsize         integer,
  weight        integer,
  version       bigint,
  locktime      bigint,
  is_coinbase   boolean     NOT NULL DEFAULT false,
  fee           numeric(20,8),                          -- Σinputs − Σoutputs (NULL for coinbase)
  in_count      integer,
  out_count     integer,
  total_out     numeric(20,8),
  -- 12-dim tx feature embedding (see chain-exporter.mjs::embedTx):
  --   log10(fee), log10(total_out), in/out counts, vsize, is_coinbase, segwit, rbf, hour, feerate, locktime
  embedding     vector(12)
);

-- ── Inputs / outputs ───────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS tx_inputs (
  txid       text     NOT NULL,
  vin        integer  NOT NULL,
  prev_txid  text,                                      -- NULL for coinbase
  prev_vout  integer,
  value      numeric(20,8),                             -- from verbosity-3 prevout (BTC)
  address    text,                                      -- prevout address, when standard
  sequence   bigint,
  coinbase   text,                                      -- decoded coinbase message (coinbase inputs only)
  PRIMARY KEY (txid, vin)
);

CREATE TABLE IF NOT EXISTS tx_outputs (
  txid         text     NOT NULL,
  vout         integer  NOT NULL,
  value        numeric(20,8),
  address      text,
  script_type  text,                                    -- witness_v0_keyhash / scripthash / nulldata / …
  PRIMARY KEY (txid, vout)
);

-- ── Address rollup (behaviour features for vector search) ───────────────────────────────
CREATE TABLE IF NOT EXISTS addresses (
  address        text        PRIMARY KEY,
  network        text,                                  -- bech32 / bech32m / p2sh / legacy (derived)
  first_height   integer,
  last_height    integer,
  n_outputs      bigint      NOT NULL DEFAULT 0,        -- times it appears as an output
  total_received numeric(30,8) NOT NULL DEFAULT 0,
  -- 8-dim address behaviour embedding (see chain-exporter.mjs::embedAddress):
  --   [log10(n_outputs), log10(total_received), active_span_norm, first_norm, last_norm, type0, type1, type2]
  embedding      vector(8)
);

-- ── Resumable checkpoint (single row) ───────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS chain_export_progress (
  id           integer     PRIMARY KEY DEFAULT 1 CHECK (id = 1),
  last_height  integer     NOT NULL DEFAULT -1,          -- highest FULLY-committed height (-1 = none)
  tip_height   integer,
  status       text        NOT NULL DEFAULT 'idle',      -- idle | running | paused | error | done
  started_at   timestamptz,
  updated_at   timestamptz DEFAULT now(),
  blocks_done  bigint      NOT NULL DEFAULT 0,
  tx_done      bigint      NOT NULL DEFAULT 0,
  last_error   text
);
INSERT INTO chain_export_progress (id) VALUES (1) ON CONFLICT (id) DO NOTHING;

-- ── Structured-search indexes (create these WITH the tables; cheap relative to diskann) ──
CREATE INDEX IF NOT EXISTS transactions_block_height ON transactions (block_height);
CREATE INDEX IF NOT EXISTS tx_outputs_address        ON tx_outputs (address);
CREATE INDEX IF NOT EXISTS tx_inputs_address         ON tx_inputs (address);
CREATE INDEX IF NOT EXISTS tx_inputs_prev            ON tx_inputs (prev_txid, prev_vout);
CREATE INDEX IF NOT EXISTS blocks_time               ON blocks (time);

-- ── Vector (diskann) indexes — BUILD AFTER BULK LOAD ────────────────────────────────────
-- Run these once the ingest has completed (or on a settled range). They are what makes ANN fast;
-- creating them up-front would make the multi-day COPY far slower.
--   CREATE INDEX blocks_embedding_diskann       ON blocks       USING diskann (embedding vector_cosine_ops);
--   CREATE INDEX transactions_embedding_diskann ON transactions USING diskann (embedding vector_cosine_ops);
--   CREATE INDEX addresses_embedding_diskann    ON addresses    USING diskann (embedding vector_cosine_ops);

-- ── Example searches ────────────────────────────────────────────────────────────────────
-- Structured: every output paying an address
--   SELECT o.txid, o.vout, o.value, t.block_height FROM tx_outputs o
--   JOIN transactions t USING (txid) WHERE o.address = $1 ORDER BY t.block_height;
-- Structured: a transaction with its inputs+outputs and fee
--   SELECT * FROM transactions WHERE txid = $1;
-- Vector: 20 transactions most similar to a query embedding $1 :: vector(12)
--   SELECT txid, block_height, fee, embedding <=> $1 AS distance
--   FROM transactions ORDER BY embedding <=> $1 LIMIT 20;
