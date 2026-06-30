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
