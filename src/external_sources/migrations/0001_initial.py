from django.db import migrations
from django.db import models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="ExternalSource",
            fields=[
                (
                    "id",
                    models.AutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "code",
                    models.SlugField(
                        unique=True,
                        verbose_name="code",
                        help_text="Short unique identifier for this source.",
                    ),
                ),
                (
                    "name",
                    models.CharField(
                        max_length=128,
                        verbose_name="name",
                        help_text="Human-readable name for this source.",
                    ),
                ),
                (
                    "mount_root",
                    models.TextField(
                        verbose_name="mount root",
                        help_text="Absolute path inside the container where the share is mounted.",
                    ),
                ),
                (
                    "display_root",
                    models.TextField(
                        blank=True,
                        default="",
                        verbose_name="display root",
                        help_text="Path shown to users, e.g. \\\\FILESRV\\Docs. Falls back to mount_root if empty.",
                    ),
                ),
                ("enabled", models.BooleanField(default=True, verbose_name="enabled")),
                ("recursive", models.BooleanField(default=True, verbose_name="recursive")),
                ("follow_symlinks", models.BooleanField(default=False, verbose_name="follow symlinks")),
                (
                    "include_regex",
                    models.TextField(
                        blank=True,
                        default="",
                        verbose_name="include regex",
                        help_text="Only index files matching this regex. Empty means all files.",
                    ),
                ),
                (
                    "exclude_regex",
                    models.TextField(
                        blank=True,
                        default="",
                        verbose_name="exclude regex",
                        help_text="Skip files matching this regex.",
                    ),
                ),
                (
                    "max_depth",
                    models.PositiveIntegerField(
                        blank=True,
                        null=True,
                        verbose_name="max depth",
                        help_text="Maximum directory depth to recurse. Null means unlimited.",
                    ),
                ),
                (
                    "max_file_size_mb",
                    models.PositiveIntegerField(
                        blank=True,
                        null=True,
                        verbose_name="max file size (MB)",
                        help_text="Skip files larger than this. Null means unlimited.",
                    ),
                ),
                (
                    "scan_interval_minutes",
                    models.PositiveIntegerField(
                        blank=True,
                        null=True,
                        verbose_name="scan interval (minutes)",
                        help_text="Override global scan interval for this source.",
                    ),
                ),
                ("last_scan_started_at", models.DateTimeField(blank=True, null=True, verbose_name="last scan started at")),
                ("last_scan_finished_at", models.DateTimeField(blank=True, null=True, verbose_name="last scan finished at")),
                ("last_scan_status", models.CharField(blank=True, default="", max_length=32, verbose_name="last scan status")),
                ("last_scan_message", models.TextField(blank=True, default="", verbose_name="last scan message")),
            ],
            options={
                "ordering": ("name",),
                "verbose_name": "external source",
                "verbose_name_plural": "external sources",
            },
        ),
        migrations.CreateModel(
            name="ExternalSourceScan",
            fields=[
                (
                    "id",
                    models.AutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "mode",
                    models.CharField(
                        choices=[("full", "full"), ("delta", "delta")],
                        max_length=16,
                        verbose_name="mode",
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[("running", "running"), ("success", "success"), ("failed", "failed")],
                        max_length=16,
                        verbose_name="status",
                    ),
                ),
                ("started_at", models.DateTimeField(verbose_name="started at")),
                ("finished_at", models.DateTimeField(blank=True, null=True, verbose_name="finished at")),
                ("seen_count", models.PositiveIntegerField(default=0, verbose_name="seen")),
                ("queued_count", models.PositiveIntegerField(default=0, verbose_name="queued")),
                ("updated_count", models.PositiveIntegerField(default=0, verbose_name="updated")),
                ("unchanged_count", models.PositiveIntegerField(default=0, verbose_name="unchanged")),
                ("missing_count", models.PositiveIntegerField(default=0, verbose_name="missing")),
                ("error_count", models.PositiveIntegerField(default=0, verbose_name="errors")),
                ("message", models.TextField(blank=True, default="", verbose_name="message")),
                (
                    "source",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="runs",
                        to="external_sources.externalsource",
                    ),
                ),
            ],
            options={
                "ordering": ("-started_at",),
                "verbose_name": "external source scan",
                "verbose_name_plural": "external source scans",
            },
        ),
    ]
