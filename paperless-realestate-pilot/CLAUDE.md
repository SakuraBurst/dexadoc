# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A pilot deployment of Paperless-ngx for a real-estate customer migrating from a Windows shared disk. The repo contains Docker Compose definitions, a Python-based share-sync service, and supporting scripts — no application source code to build or test in the traditional sense. There are no tests, linters, or build steps configured.

## Commands

```bash
# Prepare host directories (first-time setup)
make prepare

# Start / stop core stack
make up        # docker compose up -d
make down      # docker compose down

# Start with AI overlay (paperless-gpt)
make ai-up     # uses both docker-compose.yml and docker-compose.ai.yml
make ai-down

# Run one sync pass manually
make sync-once

# Tail logs
make logs      # webserver + share-sync

# Rebuild Paperless search index
make reindex

# Timestamped backup (Paperless export + pg_dump)
make backup
```

## Architecture

The data flow is linear and one-directional:

```
Windows SMB share (read-only, host-mounted at ./source-mount)
  → share-sync container (scripts/share_sync.py --daemon)
    → ./intake/mirror  (local audit copy)
    → ./staging/consume (Paperless consume folder)
  → Paperless-ngx webserver consumes from ./staging/consume
  → hooks/paperless_post_consume.py records import in SQLite + JSONL log
```

Key design constraint: the Windows share is **never** used as Paperless storage. All Paperless data/media/indexes live under `./volumes/` on local disk. This is intentional — Paperless' mmap-based search index can fail on network filesystems, and Paperless removes files from the consume directory after import.

### Services (docker-compose.yml)

- **webserver** — Paperless-ngx (port from `PAPERLESS_WEB_PORT`, default 8000)
- **db** — PostgreSQL (volume at `/var/lib/postgresql` — matches upstream compose files)
- **broker** — Redis
- **tika** — Apache Tika for Office/email parsing
- **gotenberg** — Office-to-PDF conversion (timeout raised via `GOTENBERG_API_TIMEOUT`)
- **share-sync** — Python 3.12-slim container running `scripts/share_sync.py --daemon`

### Shared state (known race condition)

`share-sync` and the post-consume hook share a SQLite database at `./state/share_sync.sqlite3` (WAL mode). The `file_state` table tracks each file through states: `queued` → `imported` (or `error`, `mirrored`). The hook also appends to `./state/import-log.jsonl`.

There is a race condition between the two scripts: `share_sync.py` does a read-then-write (`get_record` → `upsert_record`) without an explicit transaction. If the post-consume hook sets a file to `imported` between the read and the write, the upsert overwrites it back to `queued`. The fix is to change the unchanged-file upsert (lines ~267–281 of `share_sync.py`) to a conditional `UPDATE ... WHERE state != 'imported'`, or wrap the read+write in `BEGIN IMMEDIATE`. In practice the impact is limited because sync runs every 5 minutes and Paperless has duplicate detection, but this should be fixed before production.

### Optional AI overlay (docker-compose.ai.yml)

Adds `paperless-gpt` (LLM-assisted OCR/classification). Configured via `paperless-gpt.env`. Requires a Paperless API token and an LLM provider (defaults to Ollama at `host.docker.internal:11434`).

Note: `PAPERLESS_GPT_IMAGE` and `PAPERLESS_GPT_PORT` are defined in `paperless-gpt.env.example`, but Docker Compose `${VAR}` interpolation reads from `.env`, not from `env_file:` directives. The `:-` defaults in the YAML prevent breakage, but overrides must go in `.env`.

## Environment

All configuration is in `.env` (copy from `.env.example`). Key variables:

- `USERMAP_UID`/`USERMAP_GID` — container user mapping
- `PAPERLESS_SECRET_KEY`, `PAPERLESS_ADMIN_PASSWORD`, `PAPERLESS_DBPASS` — must be changed
- `PAPERLESS_DBENGINE` — set to `postgresql` (canonical value; accepted values are `sqlite`, `postgresql`, `mariadb`)
- `WINDOWS_SHARE_MOUNT` — host path where SMB share is mounted (default `./source-mount`)
- `SYNC_INTERVAL_SECONDS` (300), `SYNC_MAX_FILES_PER_RUN` (250) — batch throttling
- `SYNC_INCLUDE_EXTENSIONS` — comma-separated allowlist of file extensions

## Python scripts

Both Python files (`scripts/share_sync.py`, `hooks/paperless_post_consume.py`) are standalone scripts with no external dependencies beyond the Python 3.12 stdlib. They run inside Docker containers and are not installed as packages.

Key details for `share_sync.py`:
- Change detection uses `st_size` + `st_mtime_ns`
- `copy_atomic` uses a `.`-prefixed tmp file + `os.replace` for atomic writes
- `--daemon` runs sync in a loop; `--once` runs a single pass (also the default when no flag given)
- `--dry-run` skips file copies and DB writes but still creates the DB file as a side effect

Key details for `paperless_post_consume.py`:
- Receives Paperless environment variables (`DOCUMENT_SOURCE_PATH`, `DOCUMENT_ID`, etc.)
- Matches files by computing `rel_path` relative to `PILOT_CONSUME_ROOT` — this works because `share_sync.py` preserves directory structure when copying to the queue
- Always returns 0, even on errors — Paperless only warns on non-zero, so misconfiguration is silent
- Does not check UPDATE rowcount, so a path mismatch silently skips the DB update

## File patterns to never commit

`.env`, `paperless-gpt.env`, `*.sqlite3`, and everything under `volumes/`, `state/`, `intake/mirror/`, `staging/consume/`, `source-mount/`, `export/` (see `.gitignore`).
