from django.core.management.base import BaseCommand
from django.core.management.base import CommandError

from external_sources.models import ExternalSource
from external_sources.tasks import scan_external_source


class Command(BaseCommand):
    help = "Scan an external source for new, changed, or missing files."

    def add_arguments(self, parser):
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument("--source-code", type=str, help="Source code (slug)")
        group.add_argument("--source-id", type=int, help="Source database ID")
        parser.add_argument(
            "--mode",
            choices=["delta", "full"],
            default="delta",
            help="Scan mode: delta (skip unchanged) or full (re-check everything)",
        )
        parser.add_argument(
            "--sync",
            action="store_true",
            help="Run synchronously instead of dispatching to Celery",
        )

    def handle(self, *args, **options):
        if options["source_code"]:
            try:
                source = ExternalSource.objects.get(code=options["source_code"])
            except ExternalSource.DoesNotExist:
                raise CommandError(f"Source with code '{options['source_code']}' not found")
        else:
            try:
                source = ExternalSource.objects.get(pk=options["source_id"])
            except ExternalSource.DoesNotExist:
                raise CommandError(f"Source with id {options['source_id']} not found")

        mode = options["mode"]
        self.stdout.write(f"Scanning source '{source.code}' ({source.name}) in {mode} mode...")

        if options["sync"]:
            scan_external_source(source.pk, mode=mode, synchronous=True)
        else:
            scan_external_source.delay(source.pk, mode=mode)
            self.stdout.write("Scan task dispatched to Celery.")

        self.stdout.write(self.style.SUCCESS("Done."))
