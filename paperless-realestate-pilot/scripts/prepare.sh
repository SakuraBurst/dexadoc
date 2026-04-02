#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

mkdir -p \
  "${ROOT_DIR}/source-mount" \
  "${ROOT_DIR}/staging/consume" \
  "${ROOT_DIR}/intake/mirror" \
  "${ROOT_DIR}/state" \
  "${ROOT_DIR}/export" \
  "${ROOT_DIR}/volumes/paperless/data" \
  "${ROOT_DIR}/volumes/paperless/media/trash" \
  "${ROOT_DIR}/volumes/postgres" \
  "${ROOT_DIR}/volumes/redis" \
  "${ROOT_DIR}/paperless-gpt/prompts" \
  "${ROOT_DIR}/paperless-gpt/hocr" \
  "${ROOT_DIR}/paperless-gpt/pdf" \
  "${ROOT_DIR}/paperless-gpt/credentials" \
  "${ROOT_DIR}/config/share-sync" \
  "${ROOT_DIR}/ops/systemd"

touch \
  "${ROOT_DIR}/export/.gitkeep" \
  "${ROOT_DIR}/state/.gitkeep" \
  "${ROOT_DIR}/source-mount/.gitkeep" \
  "${ROOT_DIR}/staging/consume/.gitkeep" \
  "${ROOT_DIR}/intake/mirror/.gitkeep" \
  "${ROOT_DIR}/volumes/paperless/data/.gitkeep" \
  "${ROOT_DIR}/volumes/paperless/media/.gitkeep" \
  "${ROOT_DIR}/volumes/paperless/media/trash/.gitkeep" \
  "${ROOT_DIR}/volumes/postgres/.gitkeep" \
  "${ROOT_DIR}/volumes/redis/.gitkeep" \
  "${ROOT_DIR}/paperless-gpt/prompts/.gitkeep" \
  "${ROOT_DIR}/paperless-gpt/hocr/.gitkeep" \
  "${ROOT_DIR}/paperless-gpt/pdf/.gitkeep" \
  "${ROOT_DIR}/paperless-gpt/credentials/.gitkeep"

chmod +x \
  "${ROOT_DIR}/scripts/share_sync.py" \
  "${ROOT_DIR}/scripts/backup_export.sh" \
  "${ROOT_DIR}/hooks/paperless_post_consume.py"

if [[ ! -f "${ROOT_DIR}/.env" ]]; then
  echo "No .env file found. Copy .env.example to .env and edit it before starting the stack."
fi

if [[ ! -f "${ROOT_DIR}/paperless-gpt.env" ]]; then
  echo "Optional: copy paperless-gpt.env.example to paperless-gpt.env if you want the AI overlay."
fi

echo
echo "Prepared directories under: ${ROOT_DIR}"
echo "Next:"
echo "  1) mount your Windows share into ${ROOT_DIR}/source-mount or edit WINDOWS_SHARE_MOUNT in .env"
echo "  2) review .env"
echo "  3) run: docker compose up -d"
