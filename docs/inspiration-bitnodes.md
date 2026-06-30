# Inspiration — Bitnodes

[bitnodes.io](https://bitnodes.io/) is a long-running (since ~2013) public-good Bitcoin
project by **Addy Yeow (@ayeowch)** that **estimates the size of the Bitcoin peer-to-peer
network by finding all reachable and unreachable nodes.** It's a good model for BANKON's
diagnostics side: durable, transparent, API-first, and self-hostable.

- Site: https://bitnodes.io/ · Source: https://github.com/ayeowch/bitnodes (clone:
  `https://github.com/ayeowch/bitnodes.git`) · **License: MIT** · Python (97.5%)
- Local copy (if cloned): [`reference/bitnodes/`](reference/bitnodes/) — MIT, © Addy Yeow.

## How it works (architecture)
A protocol-level **crawler** rather than an RPC client: it speaks the Bitcoin P2P
protocol (v70001+) and recursively sends `getaddr` to discover peers, then tests
reachability and enriches with GeoIP. Key modules:

| Module | Role |
|--------|------|
| `crawl.py` | network crawl — recursive `getaddr` peer discovery |
| `protocol.py` | Bitcoin P2P protocol implementation |
| `ping.py` | node reachability / latency testing |
| `resolve.py` | address resolution (+ GeoIP) |
| `seeder.py` | seed-node management (DNS seeder) |
| `cache_inv.py` · `export.py` | inventory caching · data export/API feeds |

Notable: long-running uptime/reachability history, a global node **map**, per-node detail
pages, a public **REST API**, and network breakdowns (IPv4 / IPv6 / Tor, by version/UA).

## What BANKON can borrow
- **Network/geo map** — extend the Console *Network* tab with a peer map + per-version /
  per-network breakdown (BANKON already charts peers-by-version; geo is the next step).
- **Reachability history** — `bankon-monitor.sh` already samples peers/sync; persist a
  time series and chart uptime/peer-count over time (Bitnodes' core value).
- **API-first, self-hostable** — BANKON's `/api/*` mirrors this ethos; document a stable
  read API others can consume (see [api.md](api.md)).
- **Durability mindset** — minimal deps, clear modules, CI; Bitnodes' longevity comes from
  doing one thing reliably. Keep BANKON's read paths read-only and cache-resilient.
- **Crawler vs RPC** — Bitnodes crawls the P2P layer directly; BANKON deliberately uses the
  local node's RPC (`getpeerinfo`, `getnodeaddresses`) — simpler, no separate crawler, but
  scoped to *your* node's view rather than the whole network. A future BANKON "network map"
  could optionally use `getnodeaddresses` for a wider sample.

## Attribution
Bitnodes is © Addy Yeow and contributors, MIT-licensed. Any code or ideas adapted into
BANKON retain that attribution; see their `LICENSE`.
