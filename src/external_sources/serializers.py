from rest_framework import serializers

from external_sources.models import ExternalSource
from external_sources.models import ExternalSourceScan


class ExternalSourceSerializer(serializers.ModelSerializer):
    class Meta:
        model = ExternalSource
        fields = (
            "id",
            "code",
            "name",
            "mount_root",
            "display_root",
            "enabled",
            "recursive",
            "follow_symlinks",
            "include_regex",
            "exclude_regex",
            "max_depth",
            "max_file_size_mb",
            "scan_interval_minutes",
            "last_scan_started_at",
            "last_scan_finished_at",
            "last_scan_status",
            "last_scan_message",
        )
        read_only_fields = (
            "last_scan_started_at",
            "last_scan_finished_at",
            "last_scan_status",
            "last_scan_message",
        )


class ExternalSourceScanSerializer(serializers.ModelSerializer):
    class Meta:
        model = ExternalSourceScan
        fields = (
            "id",
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
        read_only_fields = fields
