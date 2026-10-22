from django.db import migrations, models


def remove_account_notifications(apps, schema_editor):
    apps.get_model('user', 'Notification').objects.using(schema_editor.connection.alias).filter(
        account_event='admin_access_granted',
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ('user', '0042_staff_approval_notifications'),
    ]

    operations = [
        migrations.AddField(
            model_name='notification',
            name='account_event',
            field=models.CharField(
                max_length=20, blank=True, default='',
                choices=[('admin_access_granted', 'Admin access granted')],
            ),
        ),
        migrations.RemoveConstraint(
            model_name='notification',
            name='notification_has_one_target',
        ),
        migrations.AddConstraint(
            model_name='notification',
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(report__isnull=False, staff_account__isnull=True, account_event='')
                    | models.Q(report__isnull=True, staff_account__isnull=False, account_event='')
                    | models.Q(
                        report__isnull=True, staff_account__isnull=True,
                        account_event='admin_access_granted', status_snapshot='',
                    )
                ),
                name='notification_has_one_target',
            ),
        ),
        migrations.RunPython(migrations.RunPython.noop, remove_account_notifications),
    ]
