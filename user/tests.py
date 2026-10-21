from io import StringIO

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.template.loader import render_to_string
from django.core.paginator import Paginator

from accounts.forms import SignUpForm
from adm.models import Building, Floor, Room
from .constants import PRIORITY_SLA_HOURS
from .forms import ReportForm
from .models import Notification, Report, UserProfile
from .notifications import notifications_for_user, serialize_notification


class ConsolidationTests(TestCase):
    def test_form_preserves_room_order_and_dropdown_attributes(self):
        building = Building.objects.create(name='Building', code='B')
        floor = Floor.objects.create(building=building, floor_number=1, label='First')
        last = Room.objects.create(floor=floor, name='A', code='A', order=2)
        first = Room.objects.create(floor=floor, name='Z', code='Z', order=1)
        form = ReportForm(data={'building': building.pk, 'floor': floor.pk})
        self.assertEqual(list(form.fields['location'].queryset), [first, last])
        self.assertIn('data-cdd', form.fields['location'].widget.attrs)

    def test_department_and_priority_values_use_shared_definitions(self):
        self.assertEqual(SignUpForm.DEPARTMENT_CHOICES[1:], UserProfile.DEPARTMENT_CHOICES)
        for priority, hours in PRIORITY_SLA_HOURS.items():
            self.assertEqual(Report(priority=priority).expected_resolution_hours, hours)
        self.assertIsNone(Report().expected_resolution_hours)

    def test_seed_command_does_not_duplicate_staff_profile_on_repeated_runs(self):
        output = StringIO()
        call_command('all_test_data', stdout=output)
        call_command('all_test_data', stdout=output)
        admin = User.objects.get(username='19-02-001')
        self.assertEqual(UserProfile.objects.filter(user=admin).count(), 1)
        self.assertEqual(admin.profile.role, 'admin')
        self.assertEqual(UserProfile.objects.get(user__username='23-22-022').department, 'CCS')

    def test_notifications_preserve_ownership_filter_and_role_specific_output(self):
        user = User.objects.create_user(username='notif-user')
        other = User.objects.create_user(username='notif-other')
        report = Report.objects.create(user=user, job_order_id='CONSOLIDATION', category='electrical', location='Room', description='Test')
        notification = Notification.objects.create(user=user, report=report, message='Unread')
        Notification.objects.create(user=user, report=report, message='Read', is_read=True)
        Notification.objects.create(user=other, report=report, message='Other')
        rows = list(notifications_for_user(user, 'unread'))
        self.assertEqual([row.pk for row in rows], [notification.pk])
        self.assertNotIn('completion_photo_url', serialize_notification(rows[0]))
        self.assertIn('completion_photo_url', serialize_notification(rows[0], True))
        page = Paginator(rows, 25).get_page(1)
        user_html = render_to_string('user/partials/_notifications_rows.html', {'notifications': page})
        admin_html = render_to_string('partials/_notifications_rows.html', {'notifications': page})
        from django.urls import reverse
        self.assertIn(reverse('view_notification', args=[notification.pk]), user_html)
        self.assertIn(reverse('view_notification_admin', args=[notification.pk]), admin_html)

        report.completion_photo = 'reports/completion/test-proof'
        rows[0].report = report
        rows[0].status_snapshot = 'resolved'
        user_html = render_to_string('user/partials/_notifications_rows.html', {'notifications': page})
        admin_html = render_to_string('partials/_notifications_rows.html', {'notifications': page})
        self.assertIn('See resolution', user_html)
        self.assertNotIn('See resolution', admin_html)
