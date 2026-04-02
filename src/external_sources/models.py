from django.db import models
from django.utils.translation import gettext_lazy as _


class ExternalSource(models.Model):
    code = models.SlugField(
        _("code"),
        unique=True,
        help_text=_("Short unique identifier for this source."),
    )
    name = models.CharField(
        _("name"),
        max_length=128,
        help_text=_("Human-readable name for this source."),
    )
    mount_root = models.TextField(
        _("mount root"),
        help_text=_("Absolute path inside the container where the share is mounted."),
    )
    display_root = models.TextField(
        _("display root"),
        blank=True,
        default="",
        help_text=_(
            "Path shown to users, e.g. \\\\FILESRV\\Docs. "
            "Falls back to mount_root if empty."
        ),
    )
    enabled = models.BooleanField(_("enabled"), default=True)
    recursive = models.BooleanField(_("recursive"), default=True)
    follow_symlinks = models.BooleanField(_("follow symlinks"), default=False)
    include_regex = models.TextField(
        _("include regex"),
        blank=True,
        default="",
        help_text=_("Only index files matching this regex. Empty means all files."),
    )
    exclude_regex = models.TextField(
        _("exclude regex"),
        blank=True,
        default="",
        help_text=_("Skip files matching this regex."),
    )
    max_depth = models.PositiveIntegerField(
        _("max depth"),
        null=True,
        blank=True,
        help_text=_("Maximum directory depth to recurse. Null means unlimited."),
    )
    max_file_size_mb = models.PositiveIntegerField(
        _("max file size (MB)"),
        null=True,
        blank=True,
        help_text=_("Skip files larger than this. Null means unlimited."),
    )
    scan_interval_minutes = models.PositiveIntegerField(
        _("scan interval (minutes)"),
        null=True,
        blank=True,
        help_text=_("Override global scan interval for this source."),
    )

    last_scan_started_at = models.DateTimeField(
        _("last scan started at"),
        null=True,
        blank=True,
    )
    last_scan_finished_at = models.DateTimeField(
        _("last scan finished at"),
        null=True,
        blank=True,
    )
    last_scan_status = models.CharField(
        _("last scan status"),
        max_length=32,
        blank=True,
        default="",
    )
    last_scan_message = models.TextField(
        _("last scan message"),
        blank=True,
        default="",
    )

    class Meta:
        ordering = ("name",)
        verbose_name = _("external source")
        verbose_name_plural = _("external sources")

    def __str__(self) -> str:
        return f"{self.name} ({self.code})"


class ExternalSourceScan(models.Model):
    class Mode(models.TextChoices):
        FULL = "full", _("full")
        DELTA = "delta", _("delta")

    class Status(models.TextChoices):
        RUNNING = "running", _("running")
        SUCCESS = "success", _("success")
        FAILED = "failed", _("failed")

    source = models.ForeignKey(
        ExternalSource,
        on_delete=models.CASCADE,
        related_name="runs",
    )
    mode = models.CharField(
        _("mode"),
        max_length=16,
        choices=Mode.choices,
    )
    status = models.CharField(
        _("status"),
        max_length=16,
        choices=Status.choices,
    )
    started_at = models.DateTimeField(_("started at"))
    finished_at = models.DateTimeField(
        _("finished at"),
        null=True,
        blank=True,
    )

    seen_count = models.PositiveIntegerField(_("seen"), default=0)
    queued_count = models.PositiveIntegerField(_("queued"), default=0)
    updated_count = models.PositiveIntegerField(_("updated"), default=0)
    unchanged_count = models.PositiveIntegerField(_("unchanged"), default=0)
    missing_count = models.PositiveIntegerField(_("missing"), default=0)
    error_count = models.PositiveIntegerField(_("errors"), default=0)

    message = models.TextField(_("message"), blank=True, default="")

    class Meta:
        ordering = ("-started_at",)
        verbose_name = _("external source scan")
        verbose_name_plural = _("external source scans")

    def __str__(self) -> str:
        return f"{self.source.code} {self.mode} @ {self.started_at}"
