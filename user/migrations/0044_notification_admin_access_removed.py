from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('user', '0043_notification_account_event'),
    ]

    operations = [
        migrations.AlterField(
            model_name='notification',
            name='account_event',
            field=models.CharField(
                max_length=20, blank=True, default='',
                choices=[
                    ('admin_access_granted', 'Admin access granted'),
                    ('admin_access_removed', 'Admin access removed'),
                ],
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
                        account_event__in=('admin_access_granted', 'admin_access_removed'), status_snapshot='',
                    )
                ),
                name='notification_has_one_target',
            ),
        ),
    ]
