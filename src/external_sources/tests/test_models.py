"""
Tests for ExternalSource and ExternalSourceScan models.
"""

from django.test import TestCase
from django.utils import timezone

from external_sources.models import ExternalSource
from external_sources.models import ExternalSourceScan


class TestExternalSourceModel(TestCase):
    def test_create_source(self):
        source = ExternalSource.objects.create(
            code="test-smb",
            name="Test SMB Share",
            mount_root="/mnt/share",
            display_root="\\\\FILESRV\\Docs",
        )
        self.assertEqual(str(source), "Test SMB Share (test-smb)")
        self.assertTrue(source.enabled)
        self.assertTrue(source.recursive)
        self.assertFalse(source.follow_symlinks)

    def test_unique_code(self):
        ExternalSource.objects.create(
            code="unique",
            name="First",
            mount_root="/mnt/a",
        )
        from django.db import IntegrityError

        with self.assertRaises(IntegrityError):
            ExternalSource.objects.create(
                code="unique",
                name="Second",
                mount_root="/mnt/b",
            )


class TestExternalSourceScanModel(TestCase):
    def test_create_scan(self):
        source = ExternalSource.objects.create(
            code="scan-model",
            name="Scan Model Test",
            mount_root="/mnt/share",
        )
        scan = ExternalSourceScan.objects.create(
            source=source,
            mode=ExternalSourceScan.Mode.DELTA,
            status=ExternalSourceScan.Status.SUCCESS,
            started_at=timezone.now(),
            seen_count=10,
            queued_count=2,
            unchanged_count=8,
        )
        self.assertIn("scan-model", str(scan))
        self.assertEqual(scan.seen_count, 10)
