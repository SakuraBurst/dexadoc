#!/usr/bin/env bash
# =============================================================================
# Dev test for dexadoc external reference storage mode.
#
# This script:
#   1. Starts infrastructure (Postgres, Redis, Tika, Gotenberg) via Docker
#   2. Runs Django migrations
#   3. Creates a superuser (admin/admin)
#   4. Creates an ExternalSource pointing at pilot test data
#   5. Runs a synchronous scan
#   6. Verifies results
#
# Prerequisites:
#   - Docker running
#   - uv installed (Python package manager)
#   - Ports 5432, 6379, 3000, 9998 free
#
# Usage:
#   ./scripts/dev_test_external.sh              # full run
#   ./scripts/dev_test_external.sh --skip-infra # skip docker, assume services running
#   ./scripts/dev_test_external.sh --cleanup    # tear down docker + volumes
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PILOT_DIR="$PROJECT_ROOT/paperless-realestate-pilot"
SOURCE_MOUNT="$PILOT_DIR/source-mount"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

info()  { echo -e "${CYAN}[INFO]${NC}  $*"; }
ok()    { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
fail()  { echo -e "${RED}[FAIL]${NC}  $*"; }
die()   { fail "$@"; exit 1; }

# ─── Parse args ───────────────────────────────────────────────────────────────
SKIP_INFRA=false
CLEANUP=false
for arg in "$@"; do
  case "$arg" in
    --skip-infra) SKIP_INFRA=true ;;
    --cleanup)    CLEANUP=true ;;
  esac
done

# Use sudo for docker if current user can't access the socket
DOCKER="docker"
if ! docker info >/dev/null 2>&1; then
  if sudo docker info >/dev/null 2>&1; then
    DOCKER="sudo docker"
  fi
fi
COMPOSE="$DOCKER compose -p dexadoc-dev"

if $CLEANUP; then
  info "Tearing down dev infrastructure..."
  $COMPOSE -f "$PROJECT_ROOT/docker-compose.dev.yml" down -v 2>/dev/null || true
  ok "Cleaned up."
  exit 0
fi

# ─── Load environment ────────────────────────────────────────────────────────
info "Loading dev.env..."
# shellcheck disable=SC1091
source "$PROJECT_ROOT/dev.env"

# ─── Check prerequisites ─────────────────────────────────────────────────────
command -v docker >/dev/null 2>&1   || die "docker not found"
command -v uv >/dev/null 2>&1       || die "uv not found"

# Docker/sudo already detected above
if ! docker info >/dev/null 2>&1 && ! $DOCKER info >/dev/null 2>&1; then
  die "Cannot connect to Docker daemon"
fi

if [ ! -d "$SOURCE_MOUNT" ]; then
  die "Pilot source-mount not found at $SOURCE_MOUNT"
fi

info "Test data directory: $SOURCE_MOUNT"
find "$SOURCE_MOUNT" -type f | while read -r f; do
  echo "  $(basename "$f")"
done

# ─── Start infrastructure ────────────────────────────────────────────────────
if ! $SKIP_INFRA; then
  info "Starting dev infrastructure (Postgres, Redis, Tika, Gotenberg)..."
  $COMPOSE -f "$PROJECT_ROOT/docker-compose.dev.yml" up -d

  info "Waiting for Postgres to accept connections..."
  for i in $(seq 1 30); do
    if $COMPOSE -f "$PROJECT_ROOT/docker-compose.dev.yml" exec -T db \
        pg_isready -U dexadoc -d dexadoc >/dev/null 2>&1; then
      ok "Postgres ready."
      break
    fi
    if [ "$i" -eq 30 ]; then
      die "Postgres not ready after 30s"
    fi
    sleep 1
  done

  info "Waiting for Redis..."
  for i in $(seq 1 15); do
    if $COMPOSE -f "$PROJECT_ROOT/docker-compose.dev.yml" exec -T broker \
        redis-cli ping 2>/dev/null | grep -q PONG; then
      ok "Redis ready."
      break
    fi
    if [ "$i" -eq 15 ]; then
      die "Redis not ready after 15s"
    fi
    sleep 1
  done
fi

# ─── Install Python deps ─────────────────────────────────────────────────────
info "Syncing Python dependencies..."
cd "$PROJECT_ROOT"
uv sync --group dev --quiet

# psycopg-c wheels in pyproject.toml are pinned to Python 3.12;
# install psycopg[binary] as a fallback for other Python versions.
if ! uv run python -c "import psycopg" 2>/dev/null; then
  info "Installing psycopg[binary] for Python $(python3 --version 2>&1 | awk '{print $2}')..."
  uv pip install "psycopg[binary,pool]>=3.2" --quiet
fi

# ─── Create required directories ─────────────────────────────────────────────
info "Creating media/data directories..."
mkdir -p "$PROJECT_ROOT/media/documents/originals"
mkdir -p "$PROJECT_ROOT/media/documents/archive"
mkdir -p "$PROJECT_ROOT/media/documents/thumbnails"
mkdir -p "$PROJECT_ROOT/media/trash"
mkdir -p "$PROJECT_ROOT/data/index"
mkdir -p "$PROJECT_ROOT/data/log"
mkdir -p "$PROJECT_ROOT/consume"
mkdir -p "$PROJECT_ROOT/scratch"

# ─── Run migrations ──────────────────────────────────────────────────────────
info "Running migrations..."
cd "$PROJECT_ROOT/src"
uv run manage.py migrate --no-input 2>&1 | tail -5
ok "Migrations applied."

# ─── Create superuser ────────────────────────────────────────────────────────
info "Creating superuser (admin/admin)..."
uv run manage.py createsuperuser --noinput 2>/dev/null && ok "Superuser created." \
  || warn "Superuser already exists (OK)."

# ─── Create ExternalSource ───────────────────────────────────────────────────
info "Creating ExternalSource 'pilot-alpha'..."
uv run manage.py shell -c "
from external_sources.models import ExternalSource

source, created = ExternalSource.objects.update_or_create(
    code='pilot-alpha',
    defaults=dict(
        name='Pilot: Project Alpha',
        mount_root='$SOURCE_MOUNT',
        display_root=r'\\\\FILESRV\\source-mount',
        enabled=True,
        recursive=True,
        follow_symlinks=False,
    ),
)
status = 'created' if created else 'updated'
print(f'ExternalSource pilot-alpha {status} (id={source.pk}, mount_root={source.mount_root})')
"

# ─── Run scan (synchronous) ──────────────────────────────────────────────────
info "Running synchronous scan of pilot-alpha..."
uv run manage.py dexadoc_scan_source --source-code pilot-alpha --sync --mode full 2>&1
ok "Scan complete."

# ─── Verify results ──────────────────────────────────────────────────────────
echo ""
info "=== Verification ==="
echo ""

uv run manage.py shell -c "
from documents.models import Document, StorageBackend
from pathlib import Path

ext_docs = Document.objects.filter(storage_backend=StorageBackend.EXTERNAL)
managed_docs = Document.objects.filter(storage_backend=StorageBackend.MANAGED)

print(f'External documents: {ext_docs.count()}')
print(f'Managed documents:  {managed_docs.count()}')
print()

if ext_docs.exists():
    for doc in ext_docs:
        avail = 'available' if doc.source_available else 'UNAVAILABLE'
        has_text = 'has text' if doc.content and len(doc.content.strip()) > 0 else 'NO TEXT'
        has_thumb = 'has thumbnail' if doc.thumbnail_path and Path(doc.thumbnail_path).exists() else 'no thumbnail'
        print(f'  [{doc.pk}] {doc.title}')
        print(f'       backend={doc.storage_backend}, {avail}')
        print(f'       relpath={doc.external_relpath}')
        print(f'       source_path={doc.source_path}')
        print(f'       {has_text}, {has_thumb}')
        print(f'       checksum={doc.checksum}')
        print()
else:
    print('  WARNING: No external documents found!')
    print()

# Check originals dir is empty
from django.conf import settings
originals = settings.ORIGINALS_DIR
if originals.exists():
    originals_files = list(originals.rglob('*'))
    originals_files = [f for f in originals_files if f.is_file()]
    if originals_files:
        print(f'WARNING: media/originals has {len(originals_files)} files (should be 0 for external-only test):')
        for f in originals_files:
            print(f'  {f}')
    else:
        print('media/originals/ is empty (correct for external docs)')
else:
    print('media/originals/ does not exist yet (OK)')
"

echo ""
ok "Dev test finished."
echo ""
info "Next steps:"
echo "  - Start Django:  cd src && source ../dev.env && uv run manage.py runserver"
echo "  - Start Celery:  cd src && source ../dev.env && uv run celery --app paperless worker -l DEBUG"
echo "  - Open browser:  http://localhost:8000  (admin / admin)"
echo "  - Stop infra:    ./scripts/dev_test_external.sh --cleanup"
