# GeoIP databases

BANKON uses MaxMind **GeoLite2** databases to map node IPs → location/ASN for the Network
Map and Geo Map (self-sourced from the node's addrman — no external API at runtime).

The `.mmdb` files are **not committed** (≈70 MB, and redistribution is governed by the
MaxMind GeoLite2 EULA — see [LICENSE](LICENSE)). Provide them locally:

- `GeoLite2-City.mmdb`
- `GeoLite2-ASN.mmdb`

Obtain free with a MaxMind account: <https://dev.maxmind.com/geoip/geolite2-free-geolocation-data>
(or your distro's `geoipupdate`). Drop both files in this `geoip/` directory.

Without them, BANKON degrades gracefully — the maps fall back to the node-native /
activity-ring views instead of geographic placement.

## Complete world-city list (committed)

`cities1000.tsv.gz` (~3.2 MB) — **every city on earth with population ≥ 1000**
(170,399 cities · 246 countries), derived from the public **GeoNames `cities1000`
dump** (<https://download.geonames.org/export/dump/>), licensed **CC-BY 4.0**
(attribution: GeoNames, geonames.org). Columns: name · ISO2 · lat · lon ·
population · elevation_m · timezone.

Used by the Geo Map's nearest-city overlay and 🧊 ICE geo/IP forensics
(services/world_cities.py — lazy background load into a 1°×1° grid index; a
bundled ~800-city Natural Earth table is the instant fallback and the UI always
states which dataset is in use).
