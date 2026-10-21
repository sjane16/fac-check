from importlib import import_module
from types import SimpleNamespace
from unittest.mock import patch

from django.apps import apps
from django.contrib.auth.models import User
from django.test import RequestFactory, TestCase
from django.urls import reverse

from .forms import ProfilePasswordChangeForm
from .models import Report, UserProfile
from .views import menu
from adm.views import ProfileMenu, _filter_admin_reports, build_personnel_rows


class ProfilePasswordTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='profile-test', password='Original1!')
        UserProfile.objects.create(user=self.user, role='student')

    def test_invalid_password_requests_do_not_change_password(self):
        cases = [
            ('Wrong1!', 'Replacement2!', 'Replacement2!'),
            ('Original1!', 'Replacement2!', 'Different2!'),
            ('Original1!', 'Original1!', 'Original1!'),
            ('Original1!', 'weak', 'weak'),
            ('', '', ''),
        ]
        for old, new, confirm in cases:
            form = ProfilePasswordChangeForm(self.user, {
                'old_password': old, 'new_password1': new, 'new_password2': confirm,
            })
            self.assertFalse(form.is_valid())
            self.user.refresh_from_db()
            self.assertTrue(self.user.check_password('Original1!'))

    def test_both_views_preserve_device_invalidation_audit_and_session_update(self):
        for view, module in ((ProfileMenu, 'adm.views'), (menu, 'user.views')):
            self.user.set_password('Original1!')
            self.user.save()
            request = RequestFactory().post('/profile/', {
                'change_password': '1', 'old_password': 'Original1!',
                'new_password1': 'Replacement2!', 'new_password2': 'Replacement2!',
            })
            request.user = self.user
            with patch(module + '.messages'), patch(module + '.log_event') as audit, patch(
                module + '.invalidate_trusted_devices'
            ) as invalidate, patch(module + '.update_session_auth_hash') as update_session:
                response = view(request)
            self.assertEqual(response.status_code, 302)
            self.user.refresh_from_db()
            self.assertTrue(self.user.check_password('Replacement2!'))
            invalidate.assert_called_once_with(self.user)
            audit.assert_called_once_with(request, 'password_changed', user=self.user, where='Edit Profile')
            update_session.assert_called_once_with(request, self.user)

    def test_failed_profile_save_preserves_typed_other_office(self):
        self.user.profile.role = 'staff'
        self.user.profile.save(update_fields=['role'])
        self.client.force_login(self.user)
        response = self.client.post(reverse('menu'), {
            'save_info': '1', 'first_name': 'Test', 'last_name': 'Student',
            'department': UserProfile.OTHER_OFFICE_CODE,
            'other_office': 'Campus Help Office', 'phone': 'invalid',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'value="Campus Help Office"')
        self.assertRegex(response.content.decode(), r'<option value="OTH"\s+selected>')


class PersonnelMigrationTests(TestCase):
    def test_backfill_preserves_json_and_legacy_strings_and_is_idempotent(self):
        legacy = Report.objects.create(job_order_id='LEGACY', category='electrical', location='Room', description='Test')
        existing = Report.objects.create(job_order_id='JSON', category='electrical', location='Room', description='Test', assigned_personnel_data=[{'name': 'External Person'}])
        text = 'Mr. Remigio (QTT 5), External Person'
        Report.objects.filter(pk=legacy.pk).update(assigned_personnel=text)
        Report.objects.filter(pk=existing.pk).update(assigned_personnel='Conflicting legacy name')
        legacy.history.all().update(assigned_personnel=text, assigned_personnel_data=[])
        migration = import_module('user.migrations.0032_migrate_legacy_personnel')
        editor = SimpleNamespace(connection=SimpleNamespace(alias='default'))
        migration.copy_legacy_personnel(apps, editor)
        migration.copy_legacy_personnel(apps, editor)
        legacy.refresh_from_db()
        existing.refresh_from_db()
        self.assertEqual(legacy.assigned_personnel, text)
        self.assertEqual(legacy.assigned_personnel_data, [{'name': 'Mr. Remigio'}, {'name': 'External Person'}])
        self.assertEqual(existing.assigned_personnel_data, [{'name': 'External Person'}])
        self.assertEqual(legacy.history.first().assigned_personnel_data, legacy.assigned_personnel_data)
        self.assertEqual(legacy.assigned_personnel_summary, 'Mr. Remigio, External Person')
        _, legacy_names = build_personnel_rows(legacy)
        self.assertIn({'name': 'Mr. Remigio'}, legacy_names)
        self.assertTrue(_filter_admin_reports('', 'External Person').filter(pk=legacy.pk).exists())

    def test_json_edit_updates_compatibility_copy_and_removal_clears_assignments(self):
        report = Report.objects.create(job_order_id='EDIT', category='electrical', location='Room', description='Test', assigned_personnel_data=[{'name': 'Mr. Remigio'}])
        self.assertEqual(report.assigned_personnel, 'Mr. Remigio')
        report.assigned_personnel_data = []
        report.save(update_fields=['assigned_personnel_data'])
        report.refresh_from_db()
        self.assertIsNone(report.assigned_personnel)
        self.assertIsNone(report.assigned_personnel_summary)
