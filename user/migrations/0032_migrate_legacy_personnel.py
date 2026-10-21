import re

from django.db import migrations


def copy_legacy_personnel(apps, schema_editor):
    alias = schema_editor.connection.alias
    for model_name in ('Report', 'HistoricalReport'):
        model = apps.get_model('user', model_name)
        rows = model.objects.using(alias).filter(assigned_personnel_data=[]).exclude(
            assigned_personnel__isnull=True
        ).exclude(assigned_personnel='')
        for row in rows.iterator(chunk_size=500):
            names = [
                re.sub(r'\s*\(QTT\s+\d+\)', '', name).strip()
                for name in row.assigned_personnel.split(',')
            ]
            assignments = [{'name': name} for name in names if name]
            if assignments:
                model.objects.using(alias).filter(pk=row.pk).update(
                    assigned_personnel_data=assignments
                )


class Migration(migrations.Migration):
    dependencies = [
        ('user', '0031_remove_historicalsurveyresponse_feedback_and_more'),
    ]

    # Original strings remain intact; reversing keeps the additive JSON backfill.
    operations = [migrations.RunPython(copy_legacy_personnel, migrations.RunPython.noop)]
