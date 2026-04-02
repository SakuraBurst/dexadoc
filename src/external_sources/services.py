"""
ExternalSourceScanner — walks a mounted share and dispatches consume tasks
for new/changed files.
"""

import fnmatch
import logging
import os
import re
import shutil
import uuid
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path

from django.conf import settings
from django.utils import timezone

from documents.data_models import ConsumableDocument
from documents.data_models import DocumentSource
from documents.models import Document
from documents.models import StorageBackend
from documents.tasks import consume_file
from external_sources.models import ExternalSource

logger = logging.getLogger("paperless.external_sources.scanner")


@dataclass
class ScanResult:
    seen_count: int = 0
    queued_count: int = 0
    updated_count: int = 0
    unchanged_count: int = 0
    missing_count: int = 0
    error_count: int = 0
    errors: list[str] = field(default_factory=list)


class ExternalSourceScanner:
    DEFAULT_IGNORE_PATTERNS = ["~$*", "._*", "Thumbs.db", "desktop.ini"]

    def __init__(self, source: ExternalSource, *, synchronous: bool = False):
        self.source = source
        self.synchronous = synchronous
        self.ignore_patterns = self._build_ignore_patterns()
        self.include_re = re.compile(source.include_regex) if source.include_regex else None
        self.exclude_re = re.compile(source.exclude_regex) if source.exclude_regex else None

    def _build_ignore_patterns(self) -> list[str]:
        patterns = list(settings.DEXADOC_EXTERNAL_IGNORE_PATTERNS)
        if not patterns:
            patterns = list(self.DEFAULT_IGNORE_PATTERNS)
        return patterns

    def scan(self, mode: str = "delta") -> ScanResult:
        result = ScanResult()
        mount_root = Path(self.source.mount_root).resolve()

        if not mount_root.is_dir():
            result.error_count = 1
            result.errors.append(f"mount_root does not exist or is not a directory: {mount_root}")
            return result

        seen_relpaths: set[str] = set()

        for dirpath, dirnames, filenames in os.walk(
            mount_root,
            followlinks=self.source.follow_symlinks,
        ):
            dirpath = Path(dirpath)

            # Enforce max_depth
            if self.source.max_depth is not None:
                try:
                    depth = len(dirpath.relative_to(mount_root).parts)
                except ValueError:
                    continue
                if depth > self.source.max_depth:
                    dirnames.clear()
                    continue

            # Skip recursion if not recursive
            if not self.source.recursive and dirpath != mount_root:
                dirnames.clear()
                continue

            for filename in filenames:
                filepath = dirpath / filename
                relpath = str(filepath.relative_to(mount_root))

                try:
                    self._process_file(
                        filepath=filepath,
                        relpath=relpath,
                        mode=mode,
                        result=result,
                        seen_relpaths=seen_relpaths,
                    )
                except Exception as e:
                    result.error_count += 1
                    result.errors.append(f"Error processing {relpath}: {e}")
                    logger.error(f"Error processing {relpath}: {e}", exc_info=True)

        # Mark unseen documents as unavailable
        unseen_qs = Document.objects.filter(
            storage_backend=StorageBackend.EXTERNAL,
            external_source=self.source,
            source_available=True,
        ).exclude(external_relpath__in=seen_relpaths)

        result.missing_count = unseen_qs.count()
        unseen_qs.update(
            source_available=False,
            external_last_error="missing",
        )

        return result

    def _process_file(
        self,
        filepath: Path,
        relpath: str,
        mode: str,
        result: ScanResult,
        seen_relpaths: set[str],
    ):
        filename = filepath.name

        # Check ignore patterns
        if self._should_ignore(filename):
            return

        # Check include/exclude regex
        if self.include_re and not self.include_re.search(relpath):
            return
        if self.exclude_re and self.exclude_re.search(relpath):
            return

        # Stat the file
        try:
            stat = filepath.stat()
        except OSError as e:
            result.error_count += 1
            result.errors.append(f"Cannot stat {relpath}: {e}")
            return

        # Check max_file_size
        if self.source.max_file_size_mb and stat.st_size > self.source.max_file_size_mb * 1024 * 1024:
            return

        result.seen_count += 1
        seen_relpaths.add(relpath)

        # Look up existing document
        existing = Document.objects.filter(
            storage_backend=StorageBackend.EXTERNAL,
            external_source=self.source,
            external_relpath=relpath,
        ).first()

        if existing:
            # Check if file changed
            if (
                existing.external_mtime_ns == stat.st_mtime_ns
                and existing.external_size == stat.st_size
                and mode != "full"
            ):
                # Unchanged
                result.unchanged_count += 1
                Document.objects.filter(pk=existing.pk).update(
                    last_seen_at=timezone.now(),
                    source_available=True,
                    external_last_error="",
                )
                return
            else:
                # Changed — re-index
                result.updated_count += 1
        else:
            # New file
            result.queued_count += 1

        # Copy to scratch and dispatch consume task
        self._dispatch_consume(filepath, relpath, stat)

    def _dispatch_consume(self, filepath: Path, relpath: str, stat: os.stat_result):
        scratch_dir = settings.SCRATCH_DIR / f"crawler_{self.source.code}"
        scratch_dir.mkdir(parents=True, exist_ok=True)

        scratch_name = f"{uuid.uuid4().hex}_{filepath.name}"
        scratch_path = scratch_dir / scratch_name

        shutil.copy2(filepath, scratch_path)

        input_doc = ConsumableDocument(
            source=DocumentSource.Crawler,
            original_file=scratch_path,
            original_path=filepath,
            external_source_id=self.source.pk,
            external_relpath=relpath,
            source_stat_mtime_ns=stat.st_mtime_ns,
            source_stat_size=stat.st_size,
        )

        if self.synchronous:
            consume_file(input_doc, None)
        else:
            consume_file.delay(input_doc, None)

    def _should_ignore(self, filename: str) -> bool:
        for pattern in self.ignore_patterns:
            if fnmatch.fnmatch(filename, pattern):
                return True
        return False
