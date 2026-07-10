#!/usr/bin/env bash
# install-pgvectorscale.sh — provision CURRENT official pgvector + pgvectorscale for BANKON rageBTC
# (the chain exporter's pgvectorscale target), then create the DB and apply the chain schema.
# Official sources only:
#   • PostgreSQL     → PGDG APT repo         https://www.postgresql.org/download/linux/ubuntu/
#   • pgvector       → PGDG package          https://github.com/pgvector/pgvector
#   • pgvectorscale  → Timescale release ZIP https://github.com/timescale/pgvectorscale/releases  (pg13–18)
#
# Defaults to the ALREADY-INSTALLED PostgreSQL major (non-disruptive). For the latest Postgres too:
#   PG_MAJOR=17 sudo -E bash install-pgvectorscale.sh   (installs PG 17 from PGDG alongside the old one)
#
# NEEDS ROOT (apt + file copy into PG dirs + createdb + CREATE EXTENSION).  Run:
#   sudo bash install-pgvectorscale.sh
# Idempotent; safe to re-run.
set -euo pipefail
PG_MAJOR="${PG_MAJOR:-$(ls -1 /usr/lib/postgresql 2>/dev/null | sort -n | tail -1)}"
PG_MAJOR="${PG_MAJOR:-17}"
DB_NAME="${BANKON_CHAIN_DB:-bankon_chain}"
DB_USER="${BANKON_CHAIN_USER:-bankon}"
DB_PASS="${BANKON_CHAIN_PASS:-bankon}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[ "$(id -u)" -eq 0 ] || { echo "run as root:  sudo bash $0"; exit 1; }
echo "▶ target PostgreSQL major: ${PG_MAJOR}"

echo "▶ 1/6  PGDG APT repo (official PostgreSQL / pgvector)…"
apt-get install -y curl ca-certificates gnupg lsb-release unzip >/dev/null || \
  apt-get install -y -o Dir::Etc::sourceparts="/dev/null" curl ca-certificates gnupg lsb-release unzip >/dev/null
install -d /usr/share/postgresql-common/pgdg
curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
  -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc
# PGDG uses the UBUNTU BASE codename — on Mint/Pop/etc. lsb_release -cs is wrong (e.g. "victoria");
# /etc/os-release UBUNTU_CODENAME (e.g. "jammy") is the correct base.
. /etc/os-release 2>/dev/null || true
CODENAME="${UBUNTU_CODENAME:-${VERSION_CODENAME:-$(lsb_release -cs 2>/dev/null)}}"
echo "  base codename: ${CODENAME}"
echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] \
https://apt.postgresql.org/pub/repos/apt ${CODENAME}-pgdg main" > /etc/apt/sources.list.d/pgdg.list
# update ONLY the PGDG list — sidesteps unrelated third-party repos with broken signatures.
apt-get update -y -o Dir::Etc::sourcelist="sources.list.d/pgdg.list" \
  -o Dir::Etc::sourceparts="/dev/null" -o APT::Get::List-Cleanup="0" >/dev/null

echo "▶ 2/6  PostgreSQL ${PG_MAJOR} (if missing) + pgvector…"
[ -d "/usr/lib/postgresql/${PG_MAJOR}" ] || apt-get install -y "postgresql-${PG_MAJOR}"
apt-get install -y "postgresql-${PG_MAJOR}-pgvector"
systemctl enable --now "postgresql@${PG_MAJOR}-main" 2>/dev/null || systemctl enable --now postgresql

PGCONFIG="/usr/lib/postgresql/${PG_MAJOR}/bin/pg_config"
LIBDIR="$("$PGCONFIG" --pkglibdir)"; EXTDIR="$("$PGCONFIG" --sharedir)/extension"

echo "▶ 3/6  pgvectorscale (latest official release, pg${PG_MAJOR}/$(dpkg --print-architecture))…"
ARCH="$(dpkg --print-architecture)"
ZIP_URL="$(curl -fsSL https://api.github.com/repos/timescale/pgvectorscale/releases/latest \
  | grep -oE "https://[^\"]*pgvectorscale-[0-9.]+-pg${PG_MAJOR}-${ARCH}\.zip" | head -1)"
[ -n "$ZIP_URL" ] || { echo "  ! no prebuilt zip for pg${PG_MAJOR}/${ARCH}; build from source: https://github.com/timescale/pgvectorscale#install-from-source"; exit 1; }
tmp="$(mktemp -d)"; curl -fsSL "$ZIP_URL" -o "$tmp/pgvs.zip"; unzip -o -q "$tmp/pgvs.zip" -d "$tmp/pgvs"
cp -f "$tmp"/pgvs/*.so       "$LIBDIR"/            2>/dev/null || cp -f "$(find "$tmp/pgvs" -name '*.so'      | head -1)" "$LIBDIR"/
cp -f "$tmp"/pgvs/*.control  "$EXTDIR"/            2>/dev/null || cp -f "$(find "$tmp/pgvs" -name '*.control' | head -1)" "$EXTDIR"/
cp -f "$tmp"/pgvs/*.sql      "$EXTDIR"/            2>/dev/null || cp -f $(find "$tmp/pgvs" -name '*.sql') "$EXTDIR"/ 2>/dev/null || true
rm -rf "$tmp"
echo "  installed into ${LIBDIR} and ${EXTDIR}"

echo "▶ 4/6  role + database…"
sudo -u postgres psql -tc "SELECT 1 FROM pg_roles WHERE rolname='${DB_USER}'" | grep -q 1 \
  || sudo -u postgres psql -c "CREATE ROLE ${DB_USER} LOGIN PASSWORD '${DB_PASS}';"
sudo -u postgres psql -tc "SELECT 1 FROM pg_database WHERE datname='${DB_NAME}'" | grep -q 1 \
  || sudo -u postgres createdb -O "${DB_USER}" "${DB_NAME}"
sudo -u postgres psql -c "ALTER ROLE ${DB_USER} SUPERUSER;"   # once, so it can CREATE EXTENSION

echo "▶ 5/6  extensions + chain schema…"
export PGPASSWORD="${DB_PASS}"
PORT="$(sudo -u postgres psql -tAc 'SHOW port' | tr -d ' ')"
psql "host=localhost port=${PORT} dbname=${DB_NAME} user=${DB_USER}" -f "${HERE}/schema-chain.sql"

DBURL="postgresql://${DB_USER}:${DB_PASS}@localhost:${PORT}/${DB_NAME}"
echo "▶ 6/6  done."
echo
echo "  ✓ PostgreSQL ${PG_MAJOR} + pgvector + pgvectorscale ready; chain schema applied on :${PORT}."
echo "  Point BANKON at it, then start a bounded real export + verify:"
echo
echo "      export DATABASE_URL='${DBURL}'"
echo "      ~/bankon-tools/bankon console                                  # restart Console with DATABASE_URL"
echo "      TIP=\$(bitcoin-cli getblockcount)"
echo "      curl -s -X POST :8090/api/chain/export -H content-type:application/json \\"
echo "           -d \"{\\\"fromHeight\\\":\$((TIP-500)),\\\"toHeight\\\":\$TIP}\""
echo "      watch -n2 'curl -s :8090/api/chain/export/status'"
echo "      curl -s \":8090/api/chain/export/verify?from=\$((TIP-500))&to=\$TIP\"   # DB vs getblockstats"
