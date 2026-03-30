#!/bin/sh
set -eu

# Continuous Postgres backup loop for Docker Compose.
# Produces compressed custom-format dumps and deletes old files by retention policy.

POSTGRES_HOST="${POSTGRES_HOST:-pg_database}"
POSTGRES_PORT="${POSTGRES_PORT:-5432}"
POSTGRES_DB="${POSTGRES_DB:-bot}"
POSTGRES_USER="${POSTGRES_USER:-postgres}"
BACKUP_DIR="${BACKUP_DIR:-/backups}"
BACKUP_INTERVAL_SECONDS="${BACKUP_INTERVAL_SECONDS:-86400}"
BACKUP_RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-14}"

mkdir -p "${BACKUP_DIR}"

echo "[backup] starting loop host=${POSTGRES_HOST} db=${POSTGRES_DB} interval=${BACKUP_INTERVAL_SECONDS}s retention=${BACKUP_RETENTION_DAYS}d"

while true; do
  timestamp="$(date -u +%Y%m%d_%H%M%S)"
  outfile="${BACKUP_DIR}/db_${POSTGRES_DB}_${timestamp}.dump"

  echo "[backup] creating ${outfile}"
  pg_dump \
    --host="${POSTGRES_HOST}" \
    --port="${POSTGRES_PORT}" \
    --username="${POSTGRES_USER}" \
    --dbname="${POSTGRES_DB}" \
    --format=custom \
    --compress=6 \
    --file="${outfile}"

  if [ "${BACKUP_RETENTION_DAYS}" -gt 0 ] 2>/dev/null; then
    echo "[backup] pruning backups older than ${BACKUP_RETENTION_DAYS} days"
    find "${BACKUP_DIR}" -type f -name "*.dump" -mtime "+${BACKUP_RETENTION_DAYS}" -delete
  fi

  echo "[backup] sleeping ${BACKUP_INTERVAL_SECONDS}s"
  sleep "${BACKUP_INTERVAL_SECONDS}"
done
