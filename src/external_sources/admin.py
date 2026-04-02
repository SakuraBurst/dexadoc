from django.contrib import admin

from external_sources.models import ExternalSource
from external_sources.models import ExternalSourceScan


@admin.register(ExternalSource)
class ExternalSourceAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "enabled", "last_scan_status")
    list_filter = ("enabled",)
    search_fields = ("code", "name")


@admin.register(ExternalSourceScan)
class ExternalSourceScanAdmin(admin.ModelAdmin):
    list_display = ("source", "mode", "status", "started_at", "finished_at")
    list_filter = ("status", "mode")
    readonly_fields = (
        "source",
        "mode",
        "status",
        "started_at",
        "finished_at",
        "seen_count",
        "queued_count",
        "updated_count",
        "unchanged_count",
        "missing_count",
        "error_count",
        "message",
    )
