from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def backfill_staff_notifications(apps, schema_editor):
    User = apps.get_model(settings.AUTH_USER_MODEL)
    Notification = apps.get_model('user', 'Notification')
    db = schema_editor.connection.alias
    heads = list(User.objects.using(db).filter(
        models.Q(is_staff=True) | models.Q(is_superuser=True),
        is_active=True, profile__is_head_admin=True,
    ).values_list('pk', flat=True))
    pending = User.objects.using(db).filter(
        is_active=True, profile__role='staff', profile__approval_status='pending',
        profile__deactivated_at__isnull=True,
    )
    for staff in pending.iterator():
        name = f'{staff.first_name} {staff.last_name}'.strip() or staff.email or staff.username
        for head_id in heads:
            Notification.objects.using(db).get_or_create(
                user_id=head_id, staff_account_id=staff.pk,
                defaults={'message': f'Staff account awaiting approval: {name[:150]} has verified their email.'},
            )


def remove_staff_notifications(apps, schema_editor):
    apps.get_model('user', 'Notification').objects.using(schema_editor.connection.alias).filter(
        staff_account__isnull=False,
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('user', '0041_historicaluserprofile_med_personnel_category_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='notification',
            name='report',
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.CASCADE,
                related_name='notifications', to='user.report',
            ),
        ),
        migrations.AddField(
            model_name='notification',
            name='staff_account',
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.CASCADE,
                related_name='approval_notifications', to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddConstraint(
            model_name='notification',
            constraint=models.UniqueConstraint(
                fields=('user', 'staff_account'), name='unique_staff_approval_notification',
            ),
        ),
        migrations.AddConstraint(
            model_name='notification',
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(report__isnull=False, staff_account__isnull=True)
                    | models.Q(report__isnull=True, staff_account__isnull=False)
                ),
                name='notification_has_one_target',
            ),
        ),
        migrations.RunPython(backfill_staff_notifications, remove_staff_notifications),
    ]
