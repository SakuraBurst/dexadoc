# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Dexadoc is a fork of paperless-ngx v2.20.13 that adds **external reference storage** mode. External documents live on read-only mounted SMB shares; dexadoc indexes their content, stores thumbnails/metadata locally, but never copies originals into `media/originals/`. The spec is in `PROMPT.md`.

Two storage backends: `managed` (upstream paperless-ngx behavior) and `external` (reference/search-only).

## Build & Run

### Backend
```bash
uv sync --group dev
cd src/
uv run manage.py migrate
uv run manage.py createsuperuser
uv run manage.py runserver        # Django dev server
uv run celery --app paperless worker -l DEBUG  # Celery worker
```

### Frontend (Angular)
```bash
cd src-ui/
pnpm install
ng serve   # Dev server at http://localhost:4200/
```

### Supporting Services
```bash
scripts/start_services.sh  # PostgreSQL, Redis, Gotenberg, Tika via Docker
```

## Testing

### Backend (pytest)
```bash
cd src/
uv run pytest                                          # all tests
uv run pytest path/to/test_file.py                     # single file
uv run pytest path/to/test_file.py::TestClass::test_fn # single test
uv run pytest --cov                                    # with coverage
```

### Frontend
```bash
cd src-ui/
pnpm run test              # Jest unit tests
npx playwright test        # E2E tests
npx playwright test --ui   # E2E with interactive UI
```

## Linting & Formatting

```bash
uv run ruff check src/     # Python lint
uv run ruff format src/    # Python format
cd src-ui/ && pnpm run lint   # ESLint
cd src-ui/ && pnpm run prettier  # Prettier
```

Pre-commit hooks: `uv run pre-commit install` then hooks run automatically on commit.

## Architecture

### Directory Layout
- `src/` — Django backend (Python)
- `src-ui/` — Angular frontend (TypeScript)
- `src/documents/` — Core document models, consumer pipeline, views, index
- `src/paperless/` — Django project settings, URLs, Celery config
- `src/external_sources/` — **Dexadoc addition**: ExternalSource models, scanner service, Celery tasks, management commands

### Document Consume Pipeline
Entry point: `src/documents/tasks.py:consume_file()` — Celery task that builds a plugin chain based on document source.

- **Managed docs**: `ConsumerPreflightPlugin` → `CollatePlugin` → `BarcodePlugin` → `WorkflowTriggerPlugin` → `ConsumerPlugin`
- **External docs** (Crawler): `ReferencePreflightPlugin` → `ReferenceConsumerPlugin`

Plugins implement `ConsumeTaskPlugin` interface (`src/documents/plugins/base.py`): `able_to_run`, `setup()`, `run()`, `cleanup()`.

### Key Models
- `Document` (`src/documents/models.py`) — Core model. Has `storage_backend` field (`managed`/`external`). `source_path` property is backend-aware: managed docs resolve via `settings.ORIGINALS_DIR`, external docs resolve via `external_source.mount_root / external_relpath` with path traversal protection.
- `ExternalSource` (`src/external_sources/models.py`) — Represents a mounted share with scan config.
- `ExternalSourceScan` — Tracks scan run results.

### External Document Uniqueness
- Managed docs: unique by `checksum` (partial unique constraint)
- External docs: unique by `(external_source, external_relpath)` where `deleted_at IS NULL`
- Same checksum at different paths = two separate documents (not duplicates)

### Signal Handlers (`src/documents/signals/handlers.py`)
- `cleanup_document_deletion`: For external docs, only deletes local thumbnail + archive cache. Never touches external original.
- `update_filename_and_move_files`: No-op for external docs (they have no local original to move).

### Search Index (`src/documents/index.py`)
Uses Whoosh. External docs add fields: `storage_backend`, `is_external`, `external_source`, `external_relpath`, `external_dir`, `external_basename`, `display_path`, `source_available`. Full-text search includes `external_relpath` and `display_path`.

### API
- Serializer: `src/documents/serialisers.py` (British spelling)
- Filters: `src/documents/filters.py`
- Views: `src/documents/views.py` — `unindex` action for external docs, `destroy` returns 409 for external docs
- External sources API: `src/external_sources/views.py` registered at `api/external_sources/`

### Configuration
Env vars in `src/paperless/settings.py`. Dexadoc-specific vars prefixed with `DEXADOC_`:
- `DEXADOC_ENABLE_EXTERNAL_SOURCES` (default: true)
- `DEXADOC_EXTERNAL_STORE_ARCHIVE_CACHE` (default: false)
- `DEXADOC_EXTERNAL_ALLOW_UNINDEX` (default: true)
- `DEXADOC_EXTERNAL_MAX_SCAN_WORKERS` (default: 2)
- `DEXADOC_EXTERNAL_IGNORE_PATTERNS` (default: `~$*,._*,Thumbs.db,desktop.ini`)

### Migrations
Backend: `src/documents/migrations/` (sequential integers, latest: 1076). External sources: `src/external_sources/migrations/0001_initial.py`.

### Frontend Filter IDs
`src-ui/src/app/data/filter-rule-type.ts` — Highest upstream ID: 47 (`FILTER_MIME_TYPE`). Dexadoc adds 48-50 (`FILTER_STORAGE_BACKEND`, `FILTER_SOURCE_AVAILABLE`, `FILTER_EXTERNAL_SOURCE`).
