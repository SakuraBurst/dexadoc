#!/usr/bin/env python3
"""Post-consume hook for Paperless pilot ingestion tracking.

Paperless provides DOCUMENT_SOURCE_PATH and related variables to post-consume
scripts. This hook writes successful imports back to the same SQLite DB used by
the share-sync service, and appends a JSON line import log.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def main() -> int:
    state_db = Path(os.environ.get("PILOT_STATE_DB", "/opt/pilot-state/share_sync.sqlite3"))
    import_log = Path(os.environ.get("PILOT_IMPORT_LOG", "/opt/pilot-state/import-log.jsonl"))
    consume_root = Path(os.environ.get("PILOT_CONSUME_ROOT", "/usr/src/paperless/consume"))

    source_path = Path(os.environ.get("DOCUMENT_SOURCE_PATH", ""))
    document_id = os.environ.get("DOCUMENT_ID")
    document_file_name = os.environ.get("DOCUMENT_FILE_NAME")
    timestamp = utcnow()

    if not source_path:
        print("paperless_post_consume.py: no DOCUMENT_SOURCE_PATH set", file=sys.stderr)
        return 0

    try:
        rel_path = source_path.resolve().relative_to(consume_root.resolve()).as_posix()
    except Exception:
        # Not one of our tracked staged files; still write a generic import log.
        rel_path = None

    state_db.parent.mkdir(parents=True, exist_ok=True)
    import_log.parent.mkdir(parents=True, exist_ok=True)

    if state_db.exists() and rel_path:
        conn = sqlite3.connect(state_db, timeout=30)
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute(
                """
                UPDATE file_state
                SET state = ?,
                    imported_at = ?,
                    imported_doc_id = ?,
                    imported_filename = ?,
                    last_error = NULL
                WHERE rel_path = ?
                """,
                ("imported", timestamp, document_id, document_file_name, rel_path),
            )
            conn.commit()
        finally:
            conn.close()

    record = {
        "timestamp": timestamp,
        "document_id": document_id,
        "document_file_name": document_file_name,
        "document_original_filename": os.environ.get("DOCUMENT_ORIGINAL_FILENAME"),
        "document_created": os.environ.get("DOCUMENT_CREATED"),
        "document_added": os.environ.get("DOCUMENT_ADDED"),
        "document_correspondent": os.environ.get("DOCUMENT_CORRESPONDENT"),
        "document_tags": os.environ.get("DOCUMENT_TAGS"),
        "document_source_path": os.environ.get("DOCUMENT_SOURCE_PATH"),
        "relative_source_path": rel_path,
    }

    with import_log.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    print(f"paperless_post_consume.py: recorded import doc_id={document_id} rel_path={rel_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
