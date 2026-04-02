"""
Tests for the ExternalSourceScanner service.
"""

import shutil
import tempfile
from pathlib import Path

from django.test import TestCase
from django.test import override_settings

from documents.models import Document
from documents.models import StorageBackend
from external_sources.models import ExternalSource
from external_sources.services import ExternalSourceScanner


class TestExternalSourceScanner(TestCase):
    def setUp(self):
        self.mount_dir = tempfile.mkdtemp()
        self.scratch_dir = tempfile.mkdtemp()

        self.ext_source = ExternalSource.objects.create(
            code="scan-test",
            name="Scan Test",
            mount_root=self.mount_dir,
        )

    def tearDown(self):
        shutil.rmtree(self.mount_dir, ignore_errors=True)
        shutil.rmtree(self.scratch_dir, ignore_errors=True)

    def _create_file(self, relpath: str, content: bytes = b"test") -> Path:
        fpath = Path(self.mount_dir) / relpath
        fpath.parent.mkdir(parents=True, exist_ok=True)
        fpath.write_bytes(content)
        return fpath

    def test_ignore_thumbs_db(self):
        self._create_file("Thumbs.db")
        self._create_file("real.pdf", b"%PDF-1.4 content")

        scanner = ExternalSourceScanner(self.ext_source)
        # Patch consume_file to avoid actual Celery dispatch
        from unittest.mock import patch

        with patch("external_sources.services.consume_file") as mock_consume:
            mock_consume.delay = lambda *a, **kw: None
            result = scanner.scan(mode="full")

        self.assertEqual(result.seen_count, 1)  # Only real.pdf

    def test_ignore_dot_underscore(self):
        self._create_file("._hidden")
        self._create_file("visible.pdf", b"%PDF")

        scanner = ExternalSourceScanner(self.ext_source)
        from unittest.mock import patch

        with patch("external_sources.services.consume_file") as mock_consume:
            mock_consume.delay = lambda *a, **kw: None
            result = scanner.scan(mode="full")

        self.assertEqual(result.seen_count, 1)

    def test_max_depth(self):
        self.ext_source.max_depth = 1
        self.ext_source.save()

        self._create_file("level1/file.pdf", b"%PDF")
        self._create_file("level1/level2/deep.pdf", b"%PDF")

        scanner = ExternalSourceScanner(self.ext_source)
        from unittest.mock import patch

        with patch("external_sources.services.consume_file") as mock_consume:
            mock_consume.delay = lambda *a, **kw: None
            result = scanner.scan(mode="full")

        self.assertEqual(result.seen_count, 1)  # Only level1/file.pdf

    def test_max_file_size(self):
        self.ext_source.max_file_size_mb = 1
        self.ext_source.save()

        self._create_file("small.pdf", b"%PDF" * 10)
        self._create_file("large.pdf", b"x" * (2 * 1024 * 1024))  # 2MB

        scanner = ExternalSourceScanner(self.ext_source)
        from unittest.mock import patch

        with patch("external_sources.services.consume_file") as mock_consume:
            mock_consume.delay = lambda *a, **kw: None
            result = scanner.scan(mode="full")

        self.assertEqual(result.seen_count, 1)  # Only small.pdf

    def test_include_regex(self):
        self.ext_source.include_regex = r"\.pdf$"
        self.ext_source.save()

        self._create_file("doc.pdf", b"%PDF")
        self._create_file("image.png", b"PNG")

        scanner = ExternalSourceScanner(self.ext_source)
        from unittest.mock import patch

        with patch("external_sources.services.consume_file") as mock_consume:
            mock_consume.delay = lambda *a, **kw: None
            result = scanner.scan(mode="full")

        self.assertEqual(result.seen_count, 1)

    def test_exclude_regex(self):
        self.ext_source.exclude_regex = r"\.tmp$"
        self.ext_source.save()

        self._create_file("doc.pdf", b"%PDF")
        self._create_file("temp.tmp", b"temp")

        scanner = ExternalSourceScanner(self.ext_source)
        from unittest.mock import patch

        with patch("external_sources.services.consume_file") as mock_consume:
            mock_consume.delay = lambda *a, **kw: None
            result = scanner.scan(mode="full")

        self.assertEqual(result.seen_count, 1)

    def test_delta_skip_unchanged(self):
        fpath = self._create_file("unchanged.pdf", b"%PDF")
        stat = fpath.stat()

        # Create existing document
        Document.objects.create(
            title="Unchanged",
            content="text",
            mime_type="application/pdf",
            checksum="abc",
            storage_type=Document.STORAGE_TYPE_UNENCRYPTED,
            storage_backend=StorageBackend.EXTERNAL,
            external_source=self.ext_source,
            external_relpath="unchanged.pdf",
            external_mtime_ns=stat.st_mtime_ns,
            external_size=stat.st_size,
            source_available=True,
            filename=None,
        )

        scanner = ExternalSourceScanner(self.ext_source)
        from unittest.mock import patch

        with patch("external_sources.services.consume_file") as mock_consume:
            mock_consume.delay = lambda *a, **kw: None
            result = scanner.scan(mode="delta")

        self.assertEqual(result.unchanged_count, 1)
        self.assertEqual(result.queued_count, 0)

    def test_mark_missing_after_scan(self):
        # Create document for a file that no longer exists
        doc = Document.objects.create(
            title="Missing",
            content="text",
            mime_type="application/pdf",
            checksum="missing123",
            storage_type=Document.STORAGE_TYPE_UNENCRYPTED,
            storage_backend=StorageBackend.EXTERNAL,
            external_source=self.ext_source,
            external_relpath="deleted_file.pdf",
            source_available=True,
            filename=None,
        )

        scanner = ExternalSourceScanner(self.ext_source)
        from unittest.mock import patch

        with patch("external_sources.services.consume_file") as mock_consume:
            mock_consume.delay = lambda *a, **kw: None
            result = scanner.scan(mode="delta")

        self.assertEqual(result.missing_count, 1)
        doc.refresh_from_db()
        self.assertFalse(doc.source_available)

    def test_nonrecursive(self):
        self.ext_source.recursive = False
        self.ext_source.save()

        self._create_file("root.pdf", b"%PDF")
        self._create_file("subdir/nested.pdf", b"%PDF")

        scanner = ExternalSourceScanner(self.ext_source)
        from unittest.mock import patch

        with patch("external_sources.services.consume_file") as mock_consume:
            mock_consume.delay = lambda *a, **kw: None
            result = scanner.scan(mode="full")

        self.assertEqual(result.seen_count, 1)  # Only root.pdf
