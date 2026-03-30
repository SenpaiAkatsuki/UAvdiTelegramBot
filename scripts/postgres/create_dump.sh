#!/bin/sh
set -eu

# One-shot local dump helper (compressed custom format).
# Defaults match docker-compose service names.

PG_CONTAINER_NAME="${PG_CONTAINER_NAME:-pg_database}"
POSTGRES_USER="${POSTGRES_USER:-postgres}"
POSTGRES_DB="${POSTGRES_DB:-bot}"
OUTPUT_DIR="${OUTPUT_DIR:-./postgres}"
TIMESTAMP="$(date -u +%Y%m%d_%H%M%S)"
OUTPUT_FILE="${OUTPUT_DIR}/db_${POSTGRES_DB}_${TIMESTAMP}.dump"

mkdir -p "${OUTPUT_DIR}"
docker exec -t "${PG_CONTAINER_NAME}" \
  pg_dump -U "${POSTGRES_USER}" --dbname="${POSTGRES_DB}" -Fc -Z 6 -f "/tmp/db.dump"
docker cp "${PG_CONTAINER_NAME}:/tmp/db.dump" "${OUTPUT_FILE}"
echo "Dump created: ${OUTPUT_FILE}"
