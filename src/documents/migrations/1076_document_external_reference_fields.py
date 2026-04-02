import django.db.models.deletion
from django.db import migrations
from django.db import models


class Migration(migrations.Migration):
    dependencies = [
        ("documents", "1075_workflowaction_order"),
        ("external_sources", "0001_initial"),
    ]

    operations = [
        # 1. Add storage_backend field with default='managed'
        migrations.AddField(
            model_name="document",
            name="storage_backend",
            field=models.CharField(
                choices=[("managed", "managed"), ("external", "external reference")],
                db_index=True,
                default="managed",
                max_length=16,
                verbose_name="storage backend",
            ),
        ),
        # 2. Add external_source FK
        migrations.AddField(
            model_name="document",
            name="external_source",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="documents",
                to="external_sources.externalsource",
                verbose_name="external source",
            ),
        ),
        # 3. Add external_relpath
        migrations.AddField(
            model_name="document",
            name="external_relpath",
            field=models.TextField(
                blank=True,
                null=True,
                verbose_name="external relative path",
            ),
        ),
        # 4. Add external_mtime_ns
        migrations.AddField(
            model_name="document",
            name="external_mtime_ns",
            field=models.BigIntegerField(
                blank=True,
                null=True,
                verbose_name="external mtime (ns)",
            ),
        ),
        # 5. Add external_size
        migrations.AddField(
            model_name="document",
            name="external_size",
            field=models.BigIntegerField(
                blank=True,
                null=True,
                verbose_name="external file size",
            ),
        ),
        # 6. Add source_available
        migrations.AddField(
            model_name="document",
            name="source_available",
            field=models.BooleanField(
                db_index=True,
                default=True,
                verbose_name="source available",
            ),
        ),
        # 7. Add last_seen_at
        migrations.AddField(
            model_name="document",
            name="last_seen_at",
            field=models.DateTimeField(
                blank=True,
                null=True,
                verbose_name="last seen at",
            ),
        ),
        # 8. Add external_last_error
        migrations.AddField(
            model_name="document",
            name="external_last_error",
            field=models.TextField(
                blank=True,
                default="",
                verbose_name="external last error",
            ),
        ),
        # 9. Change checksum: remove unique=True, keep db_index=True
        migrations.AlterField(
            model_name="document",
            name="checksum",
            field=models.CharField(
                db_index=True,
                editable=False,
                help_text="The checksum of the original document.",
                max_length=32,
                verbose_name="checksum",
            ),
        ),
        # 10. Add partial unique constraint: checksum for managed docs only
        migrations.AddConstraint(
            model_name="document",
            constraint=models.UniqueConstraint(
                condition=models.Q(storage_backend="managed"),
                fields=("checksum",),
                name="documents_document_managed_checksum_uniq",
            ),
        ),
        # 11. Add partial unique constraint: (source, relpath) for external docs
        migrations.AddConstraint(
            model_name="document",
            constraint=models.UniqueConstraint(
                condition=models.Q(
                    storage_backend="external",
                    deleted_at__isnull=True,
                ),
                fields=("external_source", "external_relpath"),
                name="documents_document_external_source_relpath_uniq",
            ),
        ),
    ]
