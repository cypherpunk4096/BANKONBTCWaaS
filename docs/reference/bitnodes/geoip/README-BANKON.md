# GeoIP wiring (BANKON)

The complete Bitnodes source is vendored here (upstream: <https://github.com/ayeowch/bitnodes>,
their LICENSE alongside). The GeoLite2 `.mmdb` databases are **data, not source** — the repo's
policy keeps them out of git (`geoip/*.mmdb` in the root `.gitignore`, MaxMind licensing;
`LICENSE` kept for attribution).

Locally they are wired via the symlinks in this directory to BANKON's shared GeoIP store at
`bankon-tools/geoip/` (City + ASN + Country — the same databases the Qt Geo Map and I.C.E.
forensics read), so the vendored crawler runs in place. Refresh them with `./update.sh`
(upstream's own fetcher) or per `geoip/README.md` at the repo root.
