"""
Consume plugins for external reference documents (dexadoc crawler mode).

ReferencePreflightPlugin: validates scratch file and external metadata.
ReferenceConsumerPlugin: parses/OCRs the scratch copy, stores metadata + thumbnail
locally, but never copies the original into media/originals.
"""

import datetime
import hashlib
import tempfile
from pathlib import Path

import magic
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from filelock import FileLock

from documents.consumer import ConsumerPluginMixin
from documents.consumer import ConsumerStatusShortMessage
from documents.data_models import DocumentMetadataOverrides
from documents.file_handling import create_source_path_directory
from documents.loggers import LoggingMixin
from documents.models import Document
from documents.models import StorageBackend
from documents.parsers import DocumentParser
from documents.parsers import ParseError
from documents.parsers import get_parser_class_for_mime_type
from documents.parsers import parse_date
from documents.plugins.base import AlwaysRunPluginMixin
from documents.plugins.base import ConsumeTaskPlugin
from documents.plugins.base import NoCleanupPluginMixin
from documents.plugins.base import NoSetupPluginMixin
from documents.plugins.base import StopConsumeTaskError
from documents.plugins.helpers import ProgressStatusOptions
from documents.signals import document_consumption_finished
from documents.signals import document_consumption_started
from documents.utils import copy_basic_file_stats
from documents.utils import copy_file_with_basic_stats


# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------


class ReferencePreflightPlugin(
    AlwaysRunPluginMixin,
    NoSetupPluginMixin,
    NoCleanupPluginMixin,
    LoggingMixin,
    ConsumerPluginMixin,
    ConsumeTaskPlugin,
):
    NAME: str = "ReferencePreflightPlugin"
    logging_name = "paperless.consumer.reference"

    def run(self) -> None:
        self._send_progress(
            0,
            100,
            ProgressStatusOptions.STARTED,
            ConsumerStatusShortMessage.NEW_FILE,
        )
        self._check_file_exists()
        self._check_external_fields()
        self._check_directories()

    def _check_file_exists(self):
        if not self.input_doc.original_file.is_file():
            self._fail(
                ConsumerStatusShortMessage.FILE_NOT_FOUND,
                f"Scratch file not found: {self.input_doc.original_file}",
            )

    def _check_external_fields(self):
        if self.input_doc.external_source_id is None:
            self._fail(
                "missing_external_source",
                "external_source_id is required for crawler documents",
            )
        if not self.input_doc.external_relpath:
            self._fail(
                "missing_external_relpath",
                "external_relpath is required for crawler documents",
            )

    def _check_directories(self):
        settings.SCRATCH_DIR.mkdir(parents=True, exist_ok=True)
        settings.THUMBNAIL_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Consumer
# ---------------------------------------------------------------------------


class ReferenceConsumerPlugin(
    AlwaysRunPluginMixin,
    NoSetupPluginMixin,
    NoCleanupPluginMixin,
    LoggingMixin,
    ConsumerPluginMixin,
    ConsumeTaskPlugin,
):
    NAME: str = "ReferenceConsumerPlugin"
    logging_name = "paperless.consumer.reference"

    def __init__(self, input_doc, metadata, status_mgr, base_tmp_dir, task_id):
        super().__init__(input_doc, metadata, status_mgr, base_tmp_dir, task_id)
        # Override filename to use the external relpath name, not the scratch temp name
        if self.input_doc.external_relpath:
            self.filename = Path(self.input_doc.external_relpath).name

    def run(self) -> str:
        tempdir = None

        try:
            self.log.info(f"Consuming external reference {self.filename}")

            # Check if this is an unchanged file (skip reindex)
            existing = self._find_existing_document()
            if existing and not self._file_changed(existing):
                self.log.info(
                    f"External file unchanged, updating last_seen_at for doc {existing.pk}",
                )
                Document.objects.filter(pk=existing.pk).update(
                    last_seen_at=timezone.now(),
                    source_available=True,
                    external_last_error="",
                )
                raise StopConsumeTaskError(
                    f"Skipped unchanged external file: {self.input_doc.external_relpath}",
                )

            # Copy scratch file to working directory
            tempdir = tempfile.TemporaryDirectory(
                prefix="dexadoc-ref-",
                dir=settings.SCRATCH_DIR,
            )
            self.working_copy = Path(tempdir.name) / Path(self.filename)
            copy_file_with_basic_stats(self.input_doc.original_file, self.working_copy)
            self.unmodified_original = None

            # Detect mime type
            mime_type = magic.from_file(self.working_copy, mime=True)
            self.log.debug(f"Detected mime type: {mime_type}")

            parser_class: type[DocumentParser] | None = get_parser_class_for_mime_type(
                mime_type,
            )
            if not parser_class:
                tempdir.cleanup()
                self._fail(
                    ConsumerStatusShortMessage.UNSUPPORTED_TYPE,
                    f"Unsupported mime type {mime_type}",
                )

            document_consumption_started.send(
                sender=self.__class__,
                filename=self.working_copy,
                logging_group=self.logging_group,
            )

        except StopConsumeTaskError:
            # Clean up scratch file before re-raising
            self._cleanup_scratch()
            raise
        except Exception:
            if tempdir:
                tempdir.cleanup()
            raise

        # Parse
        def progress_callback(current_progress, max_progress):
            p = int((current_progress / max_progress) * 50 + 20)
            self._send_progress(p, 100, ProgressStatusOptions.WORKING)

        document_parser: DocumentParser = parser_class(
            self.logging_group,
            progress_callback=progress_callback,
        )

        text = None
        date = None
        thumbnail = None
        archive_path = None
        page_count = None

        try:
            self._send_progress(
                20,
                100,
                ProgressStatusOptions.WORKING,
                ConsumerStatusShortMessage.PARSING_DOCUMENT,
            )
            document_parser.parse(self.working_copy, mime_type, self.filename)

            self._send_progress(
                70,
                100,
                ProgressStatusOptions.WORKING,
                ConsumerStatusShortMessage.GENERATING_THUMBNAIL,
            )
            thumbnail = document_parser.get_thumbnail(
                self.working_copy,
                mime_type,
                self.filename,
            )

            text = document_parser.get_text()
            date = document_parser.get_date()
            if date is None:
                self._send_progress(
                    90,
                    100,
                    ProgressStatusOptions.WORKING,
                    ConsumerStatusShortMessage.PARSE_DATE,
                )
                date = parse_date(self.filename, text)
            archive_path = document_parser.get_archive_path()
            page_count = document_parser.get_page_count(self.working_copy, mime_type)

        except ParseError as e:
            document_parser.cleanup()
            if tempdir:
                tempdir.cleanup()
            self._fail(
                str(e),
                f"Error parsing external document {self.filename}: {e}",
                exc_info=True,
                exception=e,
            )
        except Exception as e:
            document_parser.cleanup()
            if tempdir:
                tempdir.cleanup()
            self._fail(
                str(e),
                f"Unexpected error parsing external document {self.filename}: {e}",
                exc_info=True,
                exception=e,
            )

        self._send_progress(
            95,
            100,
            ProgressStatusOptions.WORKING,
            ConsumerStatusShortMessage.SAVE_DOCUMENT,
        )

        try:
            with transaction.atomic():
                document = self._store(
                    text=text,
                    date=date,
                    page_count=page_count,
                    mime_type=mime_type,
                    existing=existing,
                )

                document_consumption_finished.send(
                    sender=self.__class__,
                    document=document,
                    logging_group=self.logging_group,
                    classifier=None,
                    original_file=self.working_copy,
                )

                # Write thumbnail locally
                with FileLock(settings.MEDIA_LOCK):
                    create_source_path_directory(document.thumbnail_path)
                    self._write(
                        document.storage_type,
                        thumbnail,
                        document.thumbnail_path,
                    )

                    # Optionally write archive/preview cache
                    if (
                        archive_path
                        and Path(archive_path).is_file()
                        and settings.DEXADOC_EXTERNAL_STORE_ARCHIVE_CACHE
                    ):
                        from documents.file_handling import generate_unique_filename

                        gen_archive_fn = generate_unique_filename(
                            document,
                            archive_filename=True,
                        )
                        document.archive_filename = gen_archive_fn
                        create_source_path_directory(document.archive_path)
                        self._write(
                            document.storage_type,
                            archive_path,
                            document.archive_path,
                        )
                        with Path(archive_path).open("rb") as f:
                            document.archive_checksum = hashlib.md5(
                                f.read(),
                            ).hexdigest()

                # Save again after file writes (triggers signal handlers,
                # but update_filename_and_move_files is a no-op for external docs)
                document.save()

                # Clean up scratch files — never touch the external original
                self._cleanup_scratch()
                if self.working_copy and self.working_copy.is_file():
                    self.working_copy.unlink(missing_ok=True)

        except Exception as e:
            self._fail(
                str(e),
                f"Error storing external document {self.filename}: {e}",
                exc_info=True,
                exception=e,
            )
        finally:
            document_parser.cleanup()
            if tempdir:
                tempdir.cleanup()

        self.log.info(f"External document {document} consumption finished")

        self._send_progress(
            100,
            100,
            ProgressStatusOptions.SUCCESS,
            ConsumerStatusShortMessage.FINISHED,
            document.id,
        )

        document.refresh_from_db()

        action = "updated" if existing else "created"
        return f"Success. External document id {document.pk} {action}"

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _find_existing_document(self) -> Document | None:
        return Document.objects.filter(
            storage_backend=StorageBackend.EXTERNAL,
            external_source_id=self.input_doc.external_source_id,
            external_relpath=self.input_doc.external_relpath,
        ).first()

    def _file_changed(self, doc: Document) -> bool:
        if self.input_doc.source_stat_mtime_ns is None:
            return True
        return (
            doc.external_mtime_ns != self.input_doc.source_stat_mtime_ns
            or doc.external_size != self.input_doc.source_stat_size
        )

    def _store(
        self,
        text: str,
        date: datetime.datetime | None,
        page_count: int | None,
        mime_type: str,
        existing: Document | None,
    ) -> Document:
        from external_sources.models import ExternalSource

        ext_source = ExternalSource.objects.get(pk=self.input_doc.external_source_id)
        relpath = self.input_doc.external_relpath

        file_for_checksum = (
            self.unmodified_original
            if self.unmodified_original is not None
            else self.working_copy
        )
        checksum = hashlib.md5(file_for_checksum.read_bytes()).hexdigest()

        create_date = date or timezone.now()
        now = timezone.now()

        if existing:
            self.log.debug(f"Updating existing external document {existing.pk}")
            existing.content = text
            existing.checksum = checksum
            existing.mime_type = mime_type
            existing.page_count = page_count
            existing.external_mtime_ns = self.input_doc.source_stat_mtime_ns
            existing.external_size = self.input_doc.source_stat_size
            existing.source_available = True
            existing.last_seen_at = now
            existing.external_last_error = ""
            if date:
                existing.created = create_date
            existing.modified = now
            existing.save()
            return existing

        self.log.debug("Creating new external document record")
        title = Path(relpath).stem[:127] if relpath else "Untitled"

        document = Document.objects.create(
            title=title,
            content=text,
            mime_type=mime_type,
            checksum=checksum,
            created=create_date,
            modified=create_date,
            storage_type=Document.STORAGE_TYPE_UNENCRYPTED,
            storage_backend=StorageBackend.EXTERNAL,
            external_source=ext_source,
            external_relpath=relpath,
            external_mtime_ns=self.input_doc.source_stat_mtime_ns,
            external_size=self.input_doc.source_stat_size,
            source_available=True,
            last_seen_at=now,
            page_count=page_count,
            original_filename=Path(relpath).name if relpath else "",
            filename=None,
        )

        return document

    def _cleanup_scratch(self):
        """Delete the scratch copy of the external file. Never touch the external original."""
        try:
            if self.input_doc.original_file.is_file():
                self.input_doc.original_file.unlink(missing_ok=True)
        except OSError:
            pass

    def _write(self, storage_type, source, target):
        with (
            Path(source).open("rb") as read_file,
            Path(target).open("wb") as write_file,
        ):
            write_file.write(read_file.read())

        try:
            copy_basic_file_stats(source, target)
        except Exception:
            pass
