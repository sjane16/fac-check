from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('user', '0015_report_pending_awaiting_approval_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='report',
            name='assigned_personnel',
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
        migrations.AddField(
            model_name='report',
            name='assigned_personnel_data',
            field=models.JSONField(blank=True, default=list),
        ),
    ]
