"""
Test 1 from spec section 19:
source_path for external docs does not allow escaping mount_root.
"""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

from django.test import TestCase

from documents.models import Document
from documents.models import StorageBackend


class TestExternalSourcePath(TestCase):
    def _make_external_doc(self, mount_root: str, relpath: str) -> Document:
        source = MagicMock()
        source.mount_root = mount_root
        source.display_root = "\\\\FILESRV\\Docs"

        doc = Document()
        doc.storage_backend = StorageBackend.EXTERNAL
        doc.external_source = source
        doc.external_relpath = relpath
        return doc

    def test_valid_relpath(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            doc = self._make_external_doc(tmpdir, "subdir/file.pdf")
            path = doc.source_path
            self.assertTrue(str(path).startswith(tmpdir))

    def test_path_traversal_dotdot(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            doc = self._make_external_doc(tmpdir, "../../../etc/passwd")
            with self.assertRaises(ValueError) as ctx:
                _ = doc.source_path
            self.assertIn("path traversal", str(ctx.exception))

    def test_absolute_relpath(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            doc = self._make_external_doc(tmpdir, "/etc/passwd")
            # resolve() of /etc/passwd won't be under tmpdir
            with self.assertRaises(ValueError):
                _ = doc.source_path

    def test_missing_external_source(self):
        doc = Document()
        doc.storage_backend = StorageBackend.EXTERNAL
        doc.external_source = None
        doc.external_relpath = "file.pdf"
        with self.assertRaises(ValueError):
            _ = doc.source_path

    def test_missing_external_relpath(self):
        source = MagicMock()
        source.mount_root = "/mnt/share"
        doc = Document()
        doc.storage_backend = StorageBackend.EXTERNAL
        doc.external_source = source
        doc.external_relpath = None
        with self.assertRaises(ValueError):
            _ = doc.source_path

    def test_is_external_property(self):
        doc = Document()
        doc.storage_backend = StorageBackend.MANAGED
        self.assertFalse(doc.is_external)

        doc.storage_backend = StorageBackend.EXTERNAL
        self.assertTrue(doc.is_external)

    def test_display_source_path(self):
        source = MagicMock()
        source.mount_root = "/mnt/share"
        source.display_root = "\\\\FILESRV\\Docs"

        doc = Document()
        doc.storage_backend = StorageBackend.EXTERNAL
        doc.external_source = source
        doc.external_relpath = "reports/q1.pdf"
        self.assertEqual(
            doc.display_source_path,
            str(Path("\\\\FILESRV\\Docs") / "reports/q1.pdf"),
        )

    def test_display_source_path_managed(self):
        doc = Document()
        doc.storage_backend = StorageBackend.MANAGED
        self.assertIsNone(doc.display_source_path)
