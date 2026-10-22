from importlib import import_module
from types import SimpleNamespace

from django.contrib.auth.models import User
from django.core.cache import cache
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from user.models import Report, SurveyResponse, UserProfile
from .models import Personnel
from .personnel import personnel_options
from .views import build_rating_analytics_context, build_rating_responses_list


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class PersonnelManagementTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.head = User.objects.create_user(username='roster-head', is_staff=True)
        UserProfile.objects.filter(user=self.head).update(is_head_admin=True)
        self.client.force_login(self.head)
        self.personnel = Personnel.objects.get(slug='george-l-castro')

    def edit(self, name='George Renamed', category='electrical'):
        return self.client.post(reverse('personnel_edit', args=[self.personnel.pk]), {'name': name, 'category': category})

    def delete(self):
        return self.client.post(reverse('personnel_delete', args=[self.personnel.pk]))

    def test_seeded_roster_and_head_management_list(self):
        self.assertEqual(Personnel.objects.filter(is_active=True).count(), 20)
        self.assertTrue(Personnel.objects.filter(name='Mark Adrian Remigio', category='carpentry-painting').exists())
        page = self.client.get(reverse('user_management'))
        self.assertContains(page, 'id="managementListSwitch"')
        self.assertContains(page, 'Switch to All Personnels')
        self.assertContains(page, 'Completed concerns, work history, and ratings will not be deleted.')
        response = self.client.get(reverse('personnel_management_ajax'), {'search': 'Castro'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['total_count'], 1)
        self.assertIn('George L. Castro', response.json()['html'])
        electrical = self.client.get(reverse('personnel_management_ajax'), {'category': 'electrical'}).json()
        self.assertEqual(electrical['total_count'], 7)

    def test_existing_med_accounts_are_mapped_using_historical_models(self):
        account = User.objects.create_user(username='existing-med-account', first_name='George L.', last_name='Castro')
        profile = UserProfile.objects.create(user=account, role='med_personnel')
        migration = import_module('user.migrations.0040_historicalsurveyresponse_rated_by_and_more')
        historical_apps = MigrationExecutor(connection).loader.project_state([
            ('user', '0040_historicalsurveyresponse_rated_by_and_more'),
        ]).apps
        migration.map_existing_med_accounts(historical_apps, SimpleNamespace(connection=connection))
        profile.refresh_from_db()
        self.assertEqual(profile.med_personnel_slug, 'george-l-castro')

    def test_add_edit_and_category_changes_update_assignment_options(self):
        personnel_options()  # Warm cache before changing the roster.
        response = self.client.post(reverse('personnel_add'), {'name': 'New MED Worker', 'category': 'plumbing'})
        self.assertEqual(response.status_code, 200)
        added = Personnel.objects.get(name='New MED Worker')
        self.assertIn(added.slug, {item['slug'] for item in personnel_options()})
        self.assertEqual(self.edit().status_code, 200)
        options = {item['slug']: item for item in personnel_options()}
        self.assertEqual(options[self.personnel.slug]['name'], 'George Renamed')
        self.assertEqual(options[self.personnel.slug]['category_slug'], 'electrical')

    def test_rename_then_delete_preserves_report_snapshots_responses_and_ratings(self):
        report = Report.objects.create(
            job_order_id='ROSTER-HISTORY', category='electrical', location='Room',
            description='Completed concern', status='closed', priority='standard',
            assigned_personnel_data=[{'name': 'George L. Castro'}],
        )
        survey = SurveyResponse.objects.create(report=report, quality=5, quantity=4, timeliness=3)
        for action in (self.edit, self.delete):
            with self.subTest(action=action.__name__):
                self.assertEqual(action().status_code, 200)
                report.refresh_from_db()
                self.assertEqual(report.assigned_personnel_data, [{'name': 'George L. Castro'}])
                self.assertEqual(report.status, 'closed')
                self.assertTrue(SurveyResponse.objects.filter(pk=survey.pk, report=report).exists())
                rows = build_rating_analytics_context(include_responses=False)['personnel_rows']
                row = next(item for item in rows if item['name'] == 'George Renamed')
                self.assertEqual(row['complete_task'], 1)
                self.assertEqual((row['quality'], row['quantity'], row['timeliness']), (5, 4, 3))
                responses, page = build_rating_responses_list()
                self.assertEqual(page.paginator.count, 1)
                self.assertEqual(responses[0]['personnel'], 'George Renamed')
        self.personnel.refresh_from_db()
        self.assertFalse(self.personnel.is_active)
        self.assertEqual(self.personnel.previous_names, ['George L. Castro'])
        self.assertNotIn(self.personnel.slug, {item['slug'] for item in personnel_options()})

    def test_deleted_personnel_cannot_be_selected_for_new_assignments(self):
        report = Report.objects.create(job_order_id='ROSTER-PENDING', category='electrical', location='Room', description='Pending')
        self.assertEqual(self.delete().status_code, 200)
        response = self.client.post(reverse('adm_report_personnel', args=[report.pk]), {'personnel_names': [self.personnel.slug]})
        self.assertEqual(response.status_code, 400)
        report.refresh_from_db()
        self.assertEqual(report.assigned_personnel_data, [])
        listed = self.client.get(reverse('personnel_management_ajax'), {'search': 'Castro'}).json()
        self.assertEqual(listed['total_count'], 0)

    def test_duplicate_current_historical_and_removed_names_are_rejected(self):
        self.assertEqual(self.edit().status_code, 200)
        self.assertEqual(self.delete().status_code, 200)
        for name in ('George Renamed', 'george l. castro', 'MARK ADRIAN REMIGIO'):
            with self.subTest(name=name):
                response = self.client.post(reverse('personnel_add'), {'name': name, 'category': 'plumbing'})
                self.assertEqual(response.status_code, 409)

    def test_invalid_data_and_delete_method_do_not_change_the_roster(self):
        for payload in ({'name': ' ', 'category': 'electrical'}, {'name': 'Worker', 'category': 'invalid'}, {'name': 'x' * 101, 'category': 'plumbing'}):
            self.assertEqual(self.client.post(reverse('personnel_add'), payload).status_code, 400)
        self.assertEqual(self.client.get(reverse('personnel_delete', args=[self.personnel.pk])).status_code, 405)
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.head)
        self.assertEqual(csrf.post(reverse('personnel_delete', args=[self.personnel.pk])).status_code, 403)
        self.personnel.refresh_from_db()
        self.assertTrue(self.personnel.is_active)

    def test_regular_admin_cannot_manage_personnel(self):
        admin = User.objects.create_user(username='roster-staff-admin', is_staff=True)
        UserProfile.objects.filter(user=admin).update(role='staff')
        self.client.force_login(admin)
        response = self.client.get(reverse('personnel_management_ajax'))
        self.assertEqual(response.status_code, 403)
        for route, args in (('personnel_add', []), ('personnel_edit', [self.personnel.pk]), ('personnel_delete', [self.personnel.pk])):
            with self.subTest(route=route):
                response = self.client.post(reverse(route, args=args), {'name': 'Changed', 'category': 'plumbing'}, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
                self.assertEqual(response.status_code, 403)
        self.personnel.refresh_from_db()
        self.assertTrue(self.personnel.is_active)
        self.assertEqual(self.personnel.name, 'George L. Castro')
