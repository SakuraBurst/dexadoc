#!/usr/bin/env python3
"""Mirror and queue files from a mounted Windows share into Paperless staging.

Design goals:
- keep the source share read-only
- maintain a local mirror for audit/requeue
- queue only new or changed files into Paperless consume
- track status in a local SQLite database
"""

from __future__ import annotations

import argparse
import fnmatch
import logging
import os
import shutil
import sqlite3
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

LOG = logging.getLogger("share-sync")


@dataclass(frozen=True)
class Settings:
    source_dir: Path
    mirror_dir: Path
    queue_dir: Path
    state_db: Path
    exclude_file: Path | None
    interval_seconds: int
    max_files_per_run: int
    requeue_missing_after_seconds: int
    include_extensions: set[str] | None


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_extensions(value: str | None) -> set[str] | None:
    if not value:
        return None
    exts = set()
    for item in value.split(","):
        item = item.strip().lower()
        if not item:
            continue
        if not item.startswith("."):
            item = f".{item}"
        exts.add(item)
    return exts or None


def load_patterns(path: Path | None) -> list[str]:
    if not path or not path.exists():
        return []
    patterns: list[str] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        patterns.append(line)
    return patterns


def should_skip(rel_path: str, filename: str, patterns: Iterable[str], include_extensions: set[str] | None) -> bool:
    suffix = Path(filename).suffix.lower()
    if include_extensions is not None and suffix not in include_extensions:
        return True
    for pattern in patterns:
        if fnmatch.fnmatch(rel_path, pattern) or fnmatch.fnmatch(filename, pattern):
            return True
    return False


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def copy_atomic(src: Path, dst: Path) -> None:
    ensure_parent(dst)
    tmp = dst.with_name(f".{dst.name}.tmp")
    shutil.copy2(src, tmp)
    os.replace(tmp, dst)


def connect_db(path: Path) -> sqlite3.Connection:
    ensure_parent(path)
    conn = sqlite3.connect(path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS file_state (
            rel_path TEXT PRIMARY KEY,
            source_size INTEGER NOT NULL,
            source_mtime_ns INTEGER NOT NULL,
            state TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            last_mirrored_at TEXT,
            last_queued_at TEXT,
            imported_at TEXT,
            imported_doc_id TEXT,
            imported_filename TEXT,
            last_error TEXT
        )
        """
    )
    return conn


def get_record(conn: sqlite3.Connection, rel_path: str) -> sqlite3.Row | None:
    conn.row_factory = sqlite3.Row
    cur = conn.execute("SELECT * FROM file_state WHERE rel_path = ?", (rel_path,))
    return cur.fetchone()


def upsert_record(
    conn: sqlite3.Connection,
    rel_path: str,
    *,
    source_size: int,
    source_mtime_ns: int,
    state: str,
    last_seen_at: str,
    last_mirrored_at: str | None = None,
    last_queued_at: str | None = None,
    imported_at: str | None = None,
    imported_doc_id: str | None = None,
    imported_filename: str | None = None,
    last_error: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO file_state (
            rel_path, source_size, source_mtime_ns, state, last_seen_at,
            last_mirrored_at, last_queued_at, imported_at, imported_doc_id,
            imported_filename, last_error
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(rel_path) DO UPDATE SET
            source_size=excluded.source_size,
            source_mtime_ns=excluded.source_mtime_ns,
            state=excluded.state,
            last_seen_at=excluded.last_seen_at,
            last_mirrored_at=COALESCE(excluded.last_mirrored_at, file_state.last_mirrored_at),
            last_queued_at=COALESCE(excluded.last_queued_at, file_state.last_queued_at),
            imported_at=excluded.imported_at,
            imported_doc_id=excluded.imported_doc_id,
            imported_filename=excluded.imported_filename,
            last_error=excluded.last_error
        """,
        (
            rel_path,
            source_size,
            source_mtime_ns,
            state,
            last_seen_at,
            last_mirrored_at,
            last_queued_at,
            imported_at,
            imported_doc_id,
            imported_filename,
            last_error,
        ),
    )


def parse_ts(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return None


def iter_source_files(source_dir: Path) -> Iterable[Path]:
    for path in sorted(source_dir.rglob("*")):
        if path.is_file():
            yield path


def sync_once(settings: Settings, dry_run: bool = False) -> int:
    patterns = load_patterns(settings.exclude_file)
    queued_count = 0
    mirrored_count = 0
    skipped_count = 0
    requeued_count = 0
    error_count = 0
    scan_started_at = utcnow()

    conn = connect_db(settings.state_db)
    try:
        for src in iter_source_files(settings.source_dir):
            rel_path = src.relative_to(settings.source_dir).as_posix()
            filename = src.name

            if should_skip(rel_path, filename, patterns, settings.include_extensions):
                skipped_count += 1
                continue

            stat = src.stat()
            record = get_record(conn, rel_path)
            queue_dst = settings.queue_dir / rel_path
            mirror_dst = settings.mirror_dir / rel_path
            now = utcnow()

            source_changed = (
                record is None
                or int(record["source_size"]) != int(stat.st_size)
                or int(record["source_mtime_ns"]) != int(stat.st_mtime_ns)
            )

            try:
                if source_changed:
                    LOG.info("Mirroring changed file: %s", rel_path)
                    if not dry_run:
                        copy_atomic(src, mirror_dst)
                    mirrored_count += 1

                    if queue_dst.exists():
                        LOG.warning(
                            "Queue destination already exists, mirrored but not re-queued yet: %s",
                            rel_path,
                        )
                        if not dry_run:
                            upsert_record(
                                conn,
                                rel_path,
                                source_size=stat.st_size,
                                source_mtime_ns=stat.st_mtime_ns,
                                state="mirrored",
                                last_seen_at=now,
                                last_mirrored_at=now,
                                last_error="queue_path_exists",
                            )
                            conn.commit()
                        continue

                    LOG.info("Queueing new/changed file: %s", rel_path)
                    if not dry_run:
                        copy_atomic(mirror_dst if mirror_dst.exists() else src, queue_dst)
                        upsert_record(
                            conn,
                            rel_path,
                            source_size=stat.st_size,
                            source_mtime_ns=stat.st_mtime_ns,
                            state="queued",
                            last_seen_at=now,
                            last_mirrored_at=now,
                            last_queued_at=now,
                            imported_at=None,
                            imported_doc_id=None,
                            imported_filename=None,
                            last_error=None,
                        )
                        conn.commit()
                    queued_count += 1
                else:
                    # Refresh last_seen_at for unchanged files.
                    if not dry_run:
                        upsert_record(
                            conn,
                            rel_path,
                            source_size=stat.st_size,
                            source_mtime_ns=stat.st_mtime_ns,
                            state=record["state"],
                            last_seen_at=now,
                            last_mirrored_at=record["last_mirrored_at"],
                            last_queued_at=record["last_queued_at"],
                            imported_at=record["imported_at"],
                            imported_doc_id=record["imported_doc_id"],
                            imported_filename=record["imported_filename"],
                            last_error=record["last_error"],
                        )
                        conn.commit()

                    queue_missing_too_long = (
                        record["state"] == "queued"
                        and not queue_dst.exists()
                        and record["imported_at"] is None
                        and (
                            (
                                parse_ts(record["last_queued_at"]) is not None
                                and time.time() - parse_ts(record["last_queued_at"]) >= settings.requeue_missing_after_seconds
                            )
                        )
                    )

                    if queue_missing_too_long:
                        LOG.warning("Re-queueing missing staged file: %s", rel_path)
                        if not dry_run:
                            copy_atomic(mirror_dst if mirror_dst.exists() else src, queue_dst)
                            upsert_record(
                                conn,
                                rel_path,
                                source_size=stat.st_size,
                                source_mtime_ns=stat.st_mtime_ns,
                                state="queued",
                                last_seen_at=now,
                                last_mirrored_at=record["last_mirrored_at"] or now,
                                last_queued_at=now,
                                imported_at=None,
                                imported_doc_id=None,
                                imported_filename=None,
                                last_error=None,
                            )
                            conn.commit()
                        requeued_count += 1

            except Exception as exc:  # pragma: no cover - defensive logging
                error_count += 1
                LOG.exception("Failed handling %s: %s", rel_path, exc)
                if not dry_run:
                    upsert_record(
                        conn,
                        rel_path,
                        source_size=stat.st_size,
                        source_mtime_ns=stat.st_mtime_ns,
                        state="error",
                        last_seen_at=now,
                        last_mirrored_at=record["last_mirrored_at"] if record else None,
                        last_queued_at=record["last_queued_at"] if record else None,
                        imported_at=record["imported_at"] if record else None,
                        imported_doc_id=record["imported_doc_id"] if record else None,
                        imported_filename=record["imported_filename"] if record else None,
                        last_error=str(exc),
                    )
                    conn.commit()

            if settings.max_files_per_run > 0 and queued_count >= settings.max_files_per_run:
                LOG.info(
                    "Reached SYNC_MAX_FILES_PER_RUN=%s, stopping this batch.",
                    settings.max_files_per_run,
                )
                break

    finally:
        conn.close()

    LOG.info(
        "Sync finished. started_at=%s mirrored=%s queued=%s requeued=%s skipped=%s errors=%s",
        scan_started_at,
        mirrored_count,
        queued_count,
        requeued_count,
        skipped_count,
        error_count,
    )
    return 0 if error_count == 0 else 1


def read_settings() -> Settings:
    source_dir = Path(os.environ.get("SOURCE_DIR", "/source")).resolve()
    mirror_dir = Path(os.environ.get("MIRROR_DIR", "/mirror")).resolve()
    queue_dir = Path(os.environ.get("QUEUE_DIR", "/queue")).resolve()
    state_db = Path(os.environ.get("STATE_DB", "/state/share_sync.sqlite3")).resolve()

    exclude_raw = os.environ.get("EXCLUDE_FILE")
    exclude_file = Path(exclude_raw).resolve() if exclude_raw else None

    interval_seconds = int(os.environ.get("SYNC_INTERVAL_SECONDS", "300"))
    max_files_per_run = int(os.environ.get("SYNC_MAX_FILES_PER_RUN", "250"))
    requeue_missing_after_seconds = int(os.environ.get("REQUEUE_MISSING_AFTER_SECONDS", "3600"))
    include_extensions = parse_extensions(os.environ.get("INCLUDE_EXTENSIONS"))

    return Settings(
        source_dir=source_dir,
        mirror_dir=mirror_dir,
        queue_dir=queue_dir,
        state_db=state_db,
        exclude_file=exclude_file,
        interval_seconds=interval_seconds,
        max_files_per_run=max_files_per_run,
        requeue_missing_after_seconds=requeue_missing_after_seconds,
        include_extensions=include_extensions,
    )


def configure_logging() -> None:
    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Mirror and queue files from a Windows share for Paperless.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="run one sync pass and exit")
    mode.add_argument("--daemon", action="store_true", help="run forever, sleeping between sync passes")
    parser.add_argument("--dry-run", action="store_true", help="log actions without copying files or updating state")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    args = parse_args(argv or sys.argv[1:])
    settings = read_settings()

    for path in (settings.source_dir, settings.mirror_dir, settings.queue_dir, settings.state_db.parent):
        path.mkdir(parents=True, exist_ok=True)

    if args.daemon:
        while True:
            sync_once(settings, dry_run=args.dry_run)
            time.sleep(settings.interval_seconds)
    return sync_once(settings, dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
