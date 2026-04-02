import datetime

from django.core.management.base import BaseCommand
from django.utils import timezone

from documents.models import Document
from documents.models import StorageBackend


class Command(BaseCommand):
    help = (
        "Permanently delete external documents that have been marked as "
        "source_available=False for longer than the specified threshold."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--older-than-days",
            type=int,
            default=30,
            help="Only purge documents unavailable for more than N days (default: 30)",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be deleted without actually deleting",
        )

    def handle(self, *args, **options):
        threshold = timezone.now() - datetime.timedelta(days=options["older_than_days"])

        docs = Document.objects.filter(
            storage_backend=StorageBackend.EXTERNAL,
            source_available=False,
        )
        # If last_seen_at is set, use it as the threshold check.
        # If last_seen_at is NULL, the document was never seen — include it if
        # created before threshold.
        from django.db.models import Q

        docs = docs.filter(
            Q(last_seen_at__lt=threshold) | Q(last_seen_at__isnull=True, added__lt=threshold),
        )

        count = docs.count()

        if options["dry_run"]:
            self.stdout.write(f"Would purge {count} unavailable external documents.")
            for doc in docs[:20]:
                self.stdout.write(f"  - [{doc.pk}] {doc.external_relpath}")
            if count > 20:
                self.stdout.write(f"  ... and {count - 20} more")
            return

        if count == 0:
            self.stdout.write("No unavailable external documents to purge.")
            return

        self.stdout.write(f"Purging {count} unavailable external documents...")
        # Use hard_delete to bypass soft delete
        deleted, _ = docs.delete()
        self.stdout.write(self.style.SUCCESS(f"Purged {deleted} documents."))
