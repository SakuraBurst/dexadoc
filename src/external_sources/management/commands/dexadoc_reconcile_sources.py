from pathlib import Path

from django.core.management.base import BaseCommand
from django.utils import timezone

from documents.models import Document
from documents.models import StorageBackend
from external_sources.models import ExternalSource


class Command(BaseCommand):
    help = (
        "Check all external documents against their sources and mark "
        "missing files as unavailable."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--source-code",
            type=str,
            help="Only reconcile a specific source (by code). Default: all sources.",
        )

    def handle(self, *args, **options):
        sources = ExternalSource.objects.filter(enabled=True)
        if options["source_code"]:
            sources = sources.filter(code=options["source_code"])

        total_checked = 0
        total_missing = 0

        for source in sources:
            mount_root = Path(source.mount_root).resolve()
            docs = Document.objects.filter(
                storage_backend=StorageBackend.EXTERNAL,
                external_source=source,
                source_available=True,
            )

            self.stdout.write(f"Reconciling source '{source.code}': {docs.count()} available documents")

            for doc in docs.iterator():
                total_checked += 1
                try:
                    fpath = (mount_root / doc.external_relpath).resolve()
                    if not fpath.is_relative_to(mount_root):
                        raise ValueError("path traversal")
                    if not fpath.is_file():
                        raise FileNotFoundError()
                except (FileNotFoundError, ValueError, OSError):
                    Document.objects.filter(pk=doc.pk).update(
                        source_available=False,
                        external_last_error="missing (reconcile)",
                    )
                    total_missing += 1
                else:
                    Document.objects.filter(pk=doc.pk).update(
                        last_seen_at=timezone.now(),
                    )

        self.stdout.write(
            self.style.SUCCESS(
                f"Reconciled {total_checked} documents, {total_missing} marked as missing.",
            ),
        )
