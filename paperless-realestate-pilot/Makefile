SHELL := /bin/bash

.PHONY: prepare up down logs sync-once backup reindex ai-up ai-down

prepare:
	./scripts/prepare.sh

up:
	docker compose up -d

down:
	docker compose down

logs:
	docker compose logs -f webserver share-sync

sync-once:
	docker compose run --rm share-sync python /app/scripts/share_sync.py --once

backup:
	./scripts/backup_export.sh

reindex:
	docker compose run --rm webserver document_index reindex

ai-up:
	docker compose -f docker-compose.yml -f docker-compose.ai.yml up -d

ai-down:
	docker compose -f docker-compose.yml -f docker-compose.ai.yml down
