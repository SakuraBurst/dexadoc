# Paperless-ngx pilot for real-estate / development document archives

This repository is a **pilot implementation** for a customer that currently stores documents on a Windows shared disk and needs:

- OCR for scanned PDFs and images
- full-text indexing and search
- Office document ingestion (`.docx`, `.xlsx`, `.pptx`, etc.)
- a practical path from a legacy Windows share into a managed archive
- optional AI-assisted OCR / classification later

The core design is:

```text
Windows / SMB share (source only, read-only)
    -> local host mount
    -> share-sync service
    -> local mirror + local Paperless consume staging
    -> Paperless-ngx + PostgreSQL + Redis + Tika + Gotenberg
```

The important architectural choice is that the Windows share is **not** used as the primary Paperless data/media/index store. It is treated as the upstream source. Paperless keeps its own data, media, and search index on local storage.

## What is included

- `docker-compose.yml` – core pilot stack
- `docker-compose.ai.yml` – optional `paperless-gpt` overlay
- `.env.example` – compose + Paperless settings
- `paperless-gpt.env.example` – optional AI settings
- `scripts/share_sync.py` – mirrors and queues new/changed files from the mounted Windows share
- `hooks/paperless_post_consume.py` – records successful imports into a local SQLite state DB
- `scripts/backup_export.sh` – timestamped Paperless export + PostgreSQL dump
- `scripts/prepare.sh` – creates the required host directories
- `config/share-sync/exclude.txt` – ignore patterns for temporary / junk files
- `ops/systemd/*.example` – sample CIFS + timer units for Linux hosts

## Repository layout

```text
.
├── config/share-sync/exclude.txt
├── docker-compose.yml
├── docker-compose.ai.yml
├── hooks/paperless_post_consume.py
├── intake/mirror/                # local copy of source share documents
├── ops/systemd/                  # optional Linux host mount/scheduler examples
├── paperless-gpt/
│   ├── credentials/
│   ├── hocr/
│   ├── pdf/
│   └── prompts/
├── scripts/
│   ├── backup_export.sh
│   ├── prepare.sh
│   └── share_sync.py
├── source-mount/                 # mount your Windows share here (host side)
├── staging/consume/              # Paperless consume folder (local only)
├── state/                        # sync DB + import log
├── export/                       # exporter output / backups
└── volumes/
    ├── paperless/
    │   ├── data/
    │   └── media/
    ├── postgres/
    └── redis/
```

## Quick start

1. Copy the environment files.

   ```bash
   cp .env.example .env
   cp paperless-gpt.env.example paperless-gpt.env
   ```

2. Edit `.env`:
   - set `USERMAP_UID` / `USERMAP_GID`
   - change `PAPERLESS_SECRET_KEY`
   - change `PAPERLESS_ADMIN_PASSWORD`
   - adjust `PAPERLESS_URL`, `PAPERLESS_TIME_ZONE`, OCR languages, and share sync settings

3. Prepare the directories.

   ```bash
   ./scripts/prepare.sh
   ```

4. Mount the Windows share on the Linux Docker host **into `./source-mount`** or change `WINDOWS_SHARE_MOUNT` in `.env` to your real host mount path.

   Example host mountpoint:
   ```text
   /srv/paperless-pilot/source-mount
   ```

5. Start the pilot:

   ```bash
   docker compose up -d
   ```

6. Open Paperless at the configured URL, usually:

   ```text
   http://localhost:8000
   ```

   This pilot sets `PAPERLESS_ADMIN_USER` / `PAPERLESS_ADMIN_PASSWORD` so the first admin user is created automatically at startup.

7. Watch ingestion:

   ```bash
   docker compose logs -f share-sync webserver
   ```

## How the Windows-share intake works

The `share-sync` service does **not** feed the Windows share directly into Paperless. Instead it:

1. reads from the mounted share (`/source` inside the sync container)
2. copies new or changed files into `./intake/mirror`
3. copies the same files into `./staging/consume`
4. writes sync state to `./state/share_sync.sqlite3`
5. Paperless consumes from `./staging/consume`
6. `paperless_post_consume.py` records successful imports in the same SQLite DB

This gives you:
- a local audit trail of what was queued/imported
- a local mirror for requeue / troubleshooting
- no dependency on using a network share for Paperless media/data/indexes

## Core services

- `webserver` – Paperless-ngx
- `db` – PostgreSQL
- `broker` – Redis
- `tika` – Office / email parsing support
- `gotenberg` – Office-to-PDF conversion for Tika flows
- `share-sync` – local pilot ingestion helper

## Optional AI overlay

`docker-compose.ai.yml` adds `paperless-gpt` on top of the core stack.

Start it only when:
- the base Paperless import/search flow is already stable
- you have created a Paperless API token
- you have chosen an LLM/OCR provider

Launch it with:

```bash
docker compose -f docker-compose.yml -f docker-compose.ai.yml up -d
```

By default the overlay assumes:
- Paperless is reachable as `http://webserver:8000`
- paperless-gpt listens on `http://localhost:8080`
- Ollama can be reached via `http://host.docker.internal:11434`

## Recommended pilot settings

These defaults are intentionally conservative:

- **PostgreSQL** instead of SQLite
- **bind mounts** instead of anonymous/named Docker volumes
- **Tika + Gotenberg enabled** from day one for Office files
- **local Paperless data/media/export** on the Docker host
- **Windows share mounted read-only** on the host
- **batch sync** via `SYNC_MAX_FILES_PER_RUN` to avoid flooding the pilot
- **AI disabled by default**

## Useful commands

Bring the core stack up/down:

```bash
docker compose up -d
docker compose down
```

Run one sync pass manually:

```bash
docker compose run --rm share-sync python /app/scripts/share_sync.py --once
```

Rebuild the document index:

```bash
docker compose run --rm webserver document_index reindex
```

Run a timestamped export + PostgreSQL dump:

```bash
./scripts/backup_export.sh
```

Tail the key logs:

```bash
docker compose logs -f webserver share-sync
```

## Customer-specific follow-up work

This pilot gets the platform running. The next project phase should define:

- document types
- tags / nested tags
- custom fields
- workflows
- permissions and identity integration
- retention / deletion rules
- rules for signed contracts and other documents that should not be edited

For a real-estate / development customer, the first metadata model usually includes:
- project
- property / building
- unit / apartment
- tenant
- vendor / contractor
- permit / contract / parcel / cadastral numbers
- expiration / renewal dates

## Notes

- `source-mount/` is only a convenience mountpoint for the host. Docker does **not** mount SMB itself here; the Linux host does.
- If you already have another reverse proxy / SSO stack, put Paperless behind it rather than adding another one here.
- Leave `PAPERLESS_FILENAME_FORMAT` disabled during the pilot unless the metadata model is stable.
- `paperless-gpt` is optional. Prove the base OCR/search/archive flow first.

## Safety / pilot caveats

- Keep the Windows share mounted **read-only**.
- Start with small batches (`SYNC_MAX_FILES_PER_RUN`) and validate OCR/search quality before large backfills.
- Do not enable destructive AI PDF replacement until you have verified the entire flow and taken backups.
- The local mirror can grow large; size it according to the customer archive.

## License

This repository contains original deployment/configuration glue only. Upstream application licenses still apply to the containers you deploy.
