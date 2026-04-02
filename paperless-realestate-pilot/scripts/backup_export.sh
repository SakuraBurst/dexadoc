#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TS="$(date +%Y%m%d-%H%M%S)"
TARGET_DIR="${ROOT_DIR}/export/${TS}"

mkdir -p "${TARGET_DIR}"

echo "Creating Paperless exporter backup in ${TARGET_DIR} ..."
docker compose exec -T webserver document_exporter /usr/src/paperless/export/${TS} --no-progress-bar

echo "Creating PostgreSQL dump ..."
docker compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB"' | gzip > "${TARGET_DIR}/postgres.sql.gz"

cat > "${TARGET_DIR}/README.txt" <<EOF
Backup created: ${TS}

Contents:
- Paperless exporter output in this directory
- PostgreSQL dump: postgres.sql.gz

Restore notes:
1. Restore a clean Paperless instance.
2. Import exporter data with document_importer.
3. Restore PostgreSQL separately only if you want a DB-level restore instead of importer-based restore.
EOF

echo "Backup complete: ${TARGET_DIR}"
