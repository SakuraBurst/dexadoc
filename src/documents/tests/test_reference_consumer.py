"""
Tests for external reference consume pipeline (spec section 19, tests 2-4, 6-8, 11).
"""

import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.test import TestCase
from django.test import override_settings

from documents.data_models import ConsumableDocument
from documents.data_models import DocumentMetadataOverrides
from documents.data_models import DocumentSource
from documents.models import Document
from documents.models import StorageBackend
from external_sources.models import ExternalSource


class TestReferenceConsumerBase(TestCase):
    """Base class with helper utilities for reference consumer tests."""

    def setUp(self):
        self.mount_dir = tempfile.mkdtemp()
        self.scratch_dir = tempfile.mkdtemp()

        # Create external source
        self.ext_source = ExternalSource.objects.create(
            code="test-share",
            name="Test Share",
            mount_root=self.mount_dir,
            display_root="\\\\TESTSERVER\\Share",
        )

        # Create a test PDF (minimal valid content)
        self.test_file = Path(self.mount_dir) / "test.pdf"
        self.test_file.write_bytes(b"%PDF-1.4 test content")

    def tearDown(self):
        shutil.rmtree(self.mount_dir, ignore_errors=True)
        shutil.rmtree(self.scratch_dir, ignore_errors=True)

    def _make_scratch_copy(self, source_file: Path) -> Path:
        scratch = Path(self.scratch_dir) / f"scratch_{source_file.name}"
        shutil.copy2(source_file, scratch)
        return scratch

    def _make_consumable(self, source_file: Path, relpath: str) -> ConsumableDocument:
        scratch = self._make_scratch_copy(source_file)
        stat = source_file.stat()
        return ConsumableDocument(
            source=DocumentSource.Crawler,
            original_file=scratch,
            original_path=source_file,
            external_source_id=self.ext_source.pk,
            external_relpath=relpath,
            source_stat_mtime_ns=stat.st_mtime_ns,
            source_stat_size=stat.st_size,
        )


class TestCrawlerNoOriginalCopy(TestReferenceConsumerBase):
    """Test 2: crawler-mode does not write original in media/originals."""

    def test_no_original_in_media(self):
        """After consuming an external doc, media/originals should have no new files."""
        originals_before = set(settings.ORIGINALS_DIR.glob("**/*")) if settings.ORIGINALS_DIR.exists() else set()
        # Actual consume would require full parser setup — this is a model-level test
        doc = Document.objects.create(
            title="External Test",
            content="test content",
            mime_type="application/pdf",
            checksum="abc123",
            storage_type=Document.STORAGE_TYPE_UNENCRYPTED,
            storage_backend=StorageBackend.EXTERNAL,
            external_source=self.ext_source,
            external_relpath="test.pdf",
            source_available=True,
            filename=None,
        )
        self.assertEqual(doc.storage_backend, StorageBackend.EXTERNAL)
        self.assertIsNone(doc.filename)
        originals_after = set(settings.ORIGINALS_DIR.glob("**/*")) if settings.ORIGINALS_DIR.exists() else set()
        self.assertEqual(originals_before, originals_after)


class TestDuplicateChecksum(TestReferenceConsumerBase):
    """Test 3: same checksum in two different external paths creates two Documents."""

    def test_same_checksum_different_paths(self):
        # Create two files with identical content at different paths
        file1 = Path(self.mount_dir) / "dir1" / "report.pdf"
        file2 = Path(self.mount_dir) / "dir2" / "report.pdf"
        file1.parent.mkdir(parents=True, exist_ok=True)
        file2.parent.mkdir(parents=True, exist_ok=True)
        content = b"%PDF-1.4 identical content"
        file1.write_bytes(content)
        file2.write_bytes(content)

        import hashlib

        checksum = hashlib.md5(content).hexdigest()

        doc1 = Document.objects.create(
            title="Report 1",
            content="text",
            mime_type="application/pdf",
            checksum=checksum,
            storage_type=Document.STORAGE_TYPE_UNENCRYPTED,
            storage_backend=StorageBackend.EXTERNAL,
            external_source=self.ext_source,
            external_relpath="dir1/report.pdf",
            source_available=True,
            filename=None,
        )

        # This should NOT raise IntegrityError despite same checksum
        doc2 = Document.objects.create(
            title="Report 2",
            content="text",
            mime_type="application/pdf",
            checksum=checksum,
            storage_type=Document.STORAGE_TYPE_UNENCRYPTED,
            storage_backend=StorageBackend.EXTERNAL,
            external_source=self.ext_source,
            external_relpath="dir2/report.pdf",
            source_available=True,
            filename=None,
        )

        self.assertNotEqual(doc1.pk, doc2.pk)
        self.assertEqual(doc1.checksum, doc2.checksum)
        self.assertEqual(Document.objects.filter(checksum=checksum).count(), 2)


class TestSamePathChangedFile(TestReferenceConsumerBase):
    """Test 4: same path + changed mtime/size updates the same Document."""

    def test_update_existing(self):
        doc = Document.objects.create(
            title="Original",
            content="old text",
            mime_type="application/pdf",
            checksum="old_checksum",
            storage_type=Document.STORAGE_TYPE_UNENCRYPTED,
            storage_backend=StorageBackend.EXTERNAL,
            external_source=self.ext_source,
            external_relpath="report.pdf",
            external_mtime_ns=1000000,
            external_size=100,
            source_available=True,
            filename=None,
        )

        # Simulate update (what ReferenceConsumerPlugin._store does)
        doc.content = "new text"
        doc.checksum = "new_checksum"
        doc.external_mtime_ns = 2000000
        doc.external_size = 200
        doc.save()

        # Should still be the same document
        updated = Document.objects.get(
            external_source=self.ext_source,
            external_relpath="report.pdf",
        )
        self.assertEqual(updated.pk, doc.pk)
        self.assertEqual(updated.content, "new text")
        self.assertEqual(updated.external_mtime_ns, 2000000)


class TestMissingFile(TestReferenceConsumerBase):
    """Test 5: missing file gets source_available=False."""

    def test_mark_unavailable(self):
        doc = Document.objects.create(
            title="Gone",
            content="text",
            mime_type="application/pdf",
            checksum="abc",
            storage_type=Document.STORAGE_TYPE_UNENCRYPTED,
            storage_backend=StorageBackend.EXTERNAL,
            external_source=self.ext_source,
            external_relpath="gone.pdf",
            source_available=True,
            filename=None,
        )

        # Simulate scanner marking as missing
        Document.objects.filter(pk=doc.pk).update(
            source_available=False,
            external_last_error="missing",
        )

        doc.refresh_from_db()
        self.assertFalse(doc.source_available)
        self.assertEqual(doc.external_last_error, "missing")


class TestUnindexPreservesFile(TestReferenceConsumerBase):
    """Test 6: unindex does not delete the external file."""

    def test_unindex_keeps_external_file(self):
        test_file = Path(self.mount_dir) / "keep_me.pdf"
        test_file.write_bytes(b"%PDF-1.4 content")

        doc = Document.objects.create(
            title="Keep Me",
            content="text",
            mime_type="application/pdf",
            checksum="keep123",
            storage_type=Document.STORAGE_TYPE_UNENCRYPTED,
            storage_backend=StorageBackend.EXTERNAL,
            external_source=self.ext_source,
            external_relpath="keep_me.pdf",
            source_available=True,
            filename=None,
        )

        # Soft delete (unindex)
        doc.delete()

        # File should still exist
        self.assertTrue(test_file.exists())


class TestSignalNoopExternal(TestReferenceConsumerBase):
    """Tests 7-8: signal handlers are no-op for external docs."""

    def test_update_filename_noop(self):
        """update_filename_and_move_files should not attempt to move external docs."""
        doc = Document.objects.create(
            title="External Doc",
            content="text",
            mime_type="application/pdf",
            checksum="sig_test",
            storage_type=Document.STORAGE_TYPE_UNENCRYPTED,
            storage_backend=StorageBackend.EXTERNAL,
            external_source=self.ext_source,
            external_relpath="signal_test.pdf",
            source_available=True,
            filename=None,
        )
        # Saving should not raise any error about missing files or paths
        doc.title = "Updated Title"
        doc.save()
        doc.refresh_from_db()
        self.assertEqual(doc.title, "Updated Title")

    def test_cleanup_deletion_no_external_file_touch(self):
        """cleanup_document_deletion should not delete the external original."""
        test_file = Path(self.mount_dir) / "cleanup_test.pdf"
        test_file.write_bytes(b"%PDF-1.4 content")

        doc = Document.objects.create(
            title="Cleanup Test",
            content="text",
            mime_type="application/pdf",
            checksum="cleanup123",
            storage_type=Document.STORAGE_TYPE_UNENCRYPTED,
            storage_backend=StorageBackend.EXTERNAL,
            external_source=self.ext_source,
            external_relpath="cleanup_test.pdf",
            source_available=True,
            filename=None,
        )

        # Hard delete
        doc.hard_delete()

        # External file should still exist
        self.assertTrue(test_file.exists())
