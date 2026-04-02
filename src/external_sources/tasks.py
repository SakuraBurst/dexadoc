import logging

from celery import shared_task
from django.utils import timezone

from external_sources.models import ExternalSource
from external_sources.models import ExternalSourceScan
from external_sources.services import ExternalSourceScanner

logger = logging.getLogger("paperless.external_sources.tasks")


@shared_task
def scan_external_source(source_id: int, mode: str = "delta"):
    """Scan a single external source for new/changed/missing files."""
    source = ExternalSource.objects.get(pk=source_id)

    if not source.enabled:
        logger.info(f"Source {source.code} is disabled, skipping scan")
        return

    scan = ExternalSourceScan.objects.create(
        source=source,
        mode=mode,
        status=ExternalSourceScan.Status.RUNNING,
        started_at=timezone.now(),
    )

    source.last_scan_started_at = scan.started_at
    source.last_scan_status = "running"
    source.save(update_fields=["last_scan_started_at", "last_scan_status"])

    try:
        scanner = ExternalSourceScanner(source)
        result = scanner.scan(mode=mode)

        scan.seen_count = result.seen_count
        scan.queued_count = result.queued_count
        scan.updated_count = result.updated_count
        scan.unchanged_count = result.unchanged_count
        scan.missing_count = result.missing_count
        scan.error_count = result.error_count
        scan.message = "\n".join(result.errors[:50]) if result.errors else ""
        scan.status = (
            ExternalSourceScan.Status.SUCCESS
            if result.error_count == 0
            else ExternalSourceScan.Status.FAILED
        )

        source.last_scan_status = scan.status
        source.last_scan_message = scan.message[:500]

    except Exception as e:
        logger.exception(f"Scan failed for source {source.code}: {e}")
        scan.status = ExternalSourceScan.Status.FAILED
        scan.message = str(e)[:1000]
        source.last_scan_status = "failed"
        source.last_scan_message = str(e)[:500]

    finally:
        scan.finished_at = timezone.now()
        scan.save()
        source.last_scan_finished_at = scan.finished_at
        source.save(
            update_fields=[
                "last_scan_finished_at",
                "last_scan_status",
                "last_scan_message",
            ],
        )

    logger.info(
        f"Scan {source.code} ({mode}): "
        f"seen={scan.seen_count} queued={scan.queued_count} "
        f"updated={scan.updated_count} unchanged={scan.unchanged_count} "
        f"missing={scan.missing_count} errors={scan.error_count}",
    )


@shared_task
def scan_enabled_external_sources():
    """Periodic task: scan all enabled external sources."""
    sources = ExternalSource.objects.filter(enabled=True)
    for source in sources:
        try:
            scan_external_source.delay(source.pk)
        except Exception as e:
            logger.error(
                f"Failed to dispatch scan for source {source.code}: {e}",
            )
