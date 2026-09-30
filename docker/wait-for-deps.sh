#!/usr/bin/env bash
# Blocks until Postgres, Ollama, and (optionally) the Playwright server
# are accepting connections, then execs whatever command was passed in.
set -euo pipefail

echo "[wait-for-deps] Waiting for Postgres on localhost:5432 ..."
until PGPASSWORD="${POSTGRES_PASSWORD:-jobsearch-local-change-me}" \
      psql -h localhost -U "${POSTGRES_USER:-jobsearch}" -d "${POSTGRES_DB:-jobsearch_db}" -c '\q' >/dev/null 2>&1; do
    sleep 1
done
echo "[wait-for-deps] Postgres is up."

OLLAMA_HOST_PORT="${OLLAMA_BASE_URL:-http://localhost:11434}"
OLLAMA_HOST_PORT="${OLLAMA_HOST_PORT#http://}"
OLLAMA_HOST_PORT="${OLLAMA_HOST_PORT%/}"
echo "[wait-for-deps] Waiting for Ollama on ${OLLAMA_HOST_PORT} ..."
until curl -fsS "http://${OLLAMA_HOST_PORT}/api/tags" >/dev/null 2>&1; do
    sleep 1
done
echo "[wait-for-deps] Ollama is up."

if [ "${WAIT_FOR_BROWSER:-false}" = "true" ]; then
    echo "[wait-for-deps] Waiting for Playwright browser server on localhost:3000 ..."
    until nc -z localhost 3000 >/dev/null 2>&1; do
        sleep 1
    done
    echo "[wait-for-deps] Browser server is up."
fi

exec "$@"
