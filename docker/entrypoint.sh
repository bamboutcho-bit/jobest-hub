#!/usr/bin/env bash
set -euo pipefail

PG_BIN="/usr/lib/postgresql/${PG_VERSION:-16}/bin"
PGDATA="${PGDATA:-/var/lib/postgresql/data}"

POSTGRES_USER="${POSTGRES_USER:-jobsearch}"
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-jobsearch-local-change-me}"
POSTGRES_DB="${POSTGRES_DB:-jobsearch_db}"

mkdir -p "${PGDATA}"
chown -R postgres:postgres "${PGDATA}"
chmod 700 "${PGDATA}"

# --- First-boot Postgres initialization ------------------------------------
if [ ! -s "${PGDATA}/PG_VERSION" ]; then
    echo "[entrypoint] Initializing Postgres data directory at ${PGDATA} ..."
    su postgres -c "${PG_BIN}/initdb -D ${PGDATA} --auth=trust --username=postgres" >/tmp/initdb.log 2>&1

    echo "[entrypoint] Starting Postgres temporarily to create role/db ..."
    su postgres -c "${PG_BIN}/pg_ctl -D ${PGDATA} -l /tmp/pg-init.log -w start"

    su postgres -c "psql -v ON_ERROR_STOP=1 --username postgres" <<-EOSQL
        DO \$\$
        BEGIN
            IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = '${POSTGRES_USER}') THEN
                CREATE ROLE "${POSTGRES_USER}" LOGIN PASSWORD '${POSTGRES_PASSWORD}';
            END IF;
        END
        \$\$;
EOSQL
    su postgres -c "psql -v ON_ERROR_STOP=1 --username postgres" <<-EOSQL
        SELECT 'CREATE DATABASE ${POSTGRES_DB} OWNER ${POSTGRES_USER}'
        WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '${POSTGRES_DB}')\gexec
EOSQL

    su postgres -c "${PG_BIN}/pg_ctl -D ${PGDATA} -m fast -w stop"
    echo "[entrypoint] Postgres initialized."
else
    echo "[entrypoint] Existing Postgres data directory found, skipping initdb."
fi

# Listen on localhost only — everything talks to it inside this one container.
CONF="${PGDATA}/postgresql.conf"
grep -q "^listen_addresses" "${CONF}" 2>/dev/null \
    && sed -i "s/^listen_addresses.*/listen_addresses = 'localhost'/" "${CONF}" \
    || echo "listen_addresses = 'localhost'" >> "${CONF}"

# --- Hand off to supervisord, which starts and supervises everything else --
echo "[entrypoint] Starting supervisord ..."
exec /usr/bin/supervisord -n -c /etc/supervisor/conf.d/jobsearch.conf
