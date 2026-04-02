# Dexadoc Implementation Summary

## What Was Done

Fork of paperless-ngx v2.20.13 with a new **external reference storage** mode. Documents on read-only mounted SMB shares are indexed (OCR, full-text search, thumbnails) without copying originals into local storage.

## PROMPT.md Corrections

Three issues found and fixed during implementation:

1. **checksum unique constraint** — `Document.checksum` had `unique=True` which blocked the requirement "same checksum, different path = two Documents". Fixed by replacing with partial `UniqueConstraint` for managed docs only.
2. **root_document_id reference** — Spec referenced `input_doc.root_document_id` in tasks.py branching, but this field didn't exist. Simplified to `if source == Crawler ... else ...`.
3. **filename=None source_path fallback** — Spec said `filename = None` for external docs, but the property fell back to ORIGINALS_DIR. Added `is_external` check before the fallback.

## Changes by Commit Area

### 1. Models + Migrations
- Created `src/external_sources/` Django app with `ExternalSource` and `ExternalSourceScan` models
- Added `StorageBackend` enum (`managed`/`external`) to `src/documents/models.py`
- Added 8 fields to `Document`: `storage_backend`, `external_source` (FK), `external_relpath`, `external_mtime_ns`, `external_size`, `source_available`, `last_seen_at`, `external_last_error`
- Changed `checksum` from `unique=True` to `db_index=True` + partial unique constraint
- Added `is_external` property
- Registered app in `INSTALLED_APPS`, added 7 `DEXADOC_*` config vars

### 2. Backend-Aware source_path
- `source_path` property checks `is_external` first, resolves via `mount_root / external_relpath`
- Path traversal protection via `resolve()` + `is_relative_to()`
- Added `display_source_path` property for human-readable paths (e.g. `\\FILESRV\Docs\report.pdf`)

### 3. Crawler Ingest Datamodel
- Added `Crawler = 5` to `DocumentSource` enum
- Added `external_source_id`, `external_relpath`, `source_stat_mtime_ns`, `source_stat_size` to `ConsumableDocument`

### 4. Reference Consume Plugins
- Created `src/documents/plugins/reference.py`:
  - `ReferencePreflightPlugin`: validates scratch file + external fields, no checksum dedup
  - `ReferenceConsumerPlugin`: parses/OCRs scratch copy, upserts by (source, relpath), writes only thumbnail locally, deletes scratch — never touches external original
- Modified `consume_file()` in tasks.py for plugin chain branching

### 5. Signals Branching
- `cleanup_document_deletion`: external docs only delete local thumbnail + archive cache
- `update_filename_and_move_files`: no-op for external docs

### 6. Crawler Service/Tasks/Commands
- `ExternalSourceScanner` in `src/external_sources/services.py`: walks mount, respects include/exclude regex, max_depth, max_file_size, ignore patterns, dispatches consume tasks
- Celery tasks: `scan_external_source`, `scan_enabled_external_sources` (periodic, every 30min)
- Management commands: `dexadoc_scan_source`, `dexadoc_reconcile_sources`, `dexadoc_purge_unavailable`

### 7. Index Changes
- 9 new Whoosh fields for external document metadata
- `update_document()` populates new fields
- Full-text search includes `external_relpath` and `display_path`

### 8. Views/Serializers/API
- 7 read-only fields in `DocumentSerializer`
- 3 new filters: `storage_backend`, `source_available`, `external_source__id`
- `unindex` action (POST) for external docs
- `destroy` returns 409 for external docs ("use unindex")
- `serve_file` returns 503 when external file unavailable
- `ExternalSourceViewSet` with `scan` and `runs` actions

### 9. Sanity Checker / Exporter
- Sanity checker: external missing original = WARNING (not ERROR), checksum mismatch = suggest re-index
- Exporter: `--refs-only` and `--with-external-files` flags, external metadata in manifest

### 10. Frontend UI
- Document interface: 7 new fields
- Filter rule types 48-50 for storage backend, availability, source
- Document detail: External/Available badges, "Copy path" button, "Unindex" instead of "Delete"
- Document cards: External badge
- `ExternalSourceService` for API

### 11. Tests
- `test_external_source_path.py`: path traversal, is_external, display_source_path
- `test_reference_consumer.py`: no original copy, duplicate checksums, update same path, missing file, unindex preserves file, signal no-ops
- `test_scanner.py`: ignore patterns, max_depth, max_file_size, include/exclude regex, delta skip, mark missing, non-recursive
- `test_models.py`: ExternalSource/ExternalSourceScan creation and constraints

## File Summary

**Modified**: 17 files (backend models, tasks, signals, consumer, index, serializers, filters, views, sanity checker, exporter, settings, URLs, frontend components)

**Created**: 19 files (external_sources app, reference plugins, migrations, tests, frontend service)

## Verification

- `makemigrations --check` reports "No changes detected" — migrations match model state
- All upstream managed-mode code paths preserved unchanged
