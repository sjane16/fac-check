from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from user.models import Report, UserProfile


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class StaffAdminAndPersonnelTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.head = User.objects.create_user(username='personnel-head', is_staff=True)
        UserProfile.objects.filter(user=cls.head).update(is_head_admin=True)
        cls.admin = User.objects.create_user(username='personnel-staff-admin', is_staff=True)
        UserProfile.objects.filter(user=cls.admin).update(role='staff')
        cls.reporter = User.objects.create_user(username='personnel-reporter')
        UserProfile.objects.create(user=cls.reporter, role='staff')
        cls.report = Report.objects.create(
            user=cls.reporter, job_order_id='PERSONNEL-TEST', category='electrical',
            location='Test Room', description='Test concern',
        )

    def setUp(self):
        cache.clear()
        self.client.force_login(self.admin)

    def assign(self, **overrides):
        data = {'personnel_names': ['george-l-castro', 'mark-adrian-remigio']}
        data.update(overrides)
        return self.client.post(reverse('adm_report_personnel', args=[self.report.pk]), data)

    def test_staff_admin_navigation_and_export_controls_are_restricted(self):
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 200)
        for route in ('dashboard', 'reports', 'ProfileMenu'):
            self.assertContains(response, f'href="{reverse(route)}"')
        for route in ('survey_analysis', 'user_management'):
            self.assertNotContains(response, f'href="{reverse(route)}"')
        self.assertNotContains(response, 'id="reportDownload"')
        response = self.client.get(reverse('adm_dashboard_ajax'))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()['can_generate_report'])
        self.assertNotIn('id="generateReportBtn"', response.json()['html'])
        self.assertEqual(self.client.get(reverse('reports')).status_code, 200)
        self.assertEqual(self.client.get(reverse('ProfileMenu')).status_code, 200)
        details = self.client.get(reverse('adm_report_detail', args=[self.report.pk]))
        self.assertNotContains(details, 'onclick="exportJobOrder()"')

    def test_staff_admin_cannot_bypass_restrictions_using_direct_urls(self):
        for route, args in (
            ('exportJobOrderPDFAdmin', [self.report.pk]),
            ('export_dashboard_report_pdf', []),
            ('dashboard_report_summary', []),
            ('export_personnel_performance_pdf', []),
            ('adm_survey_analysis_ajax', []),
            ('adm_survey_responses_ajax', []),
            ('user_management_ajax', []),
        ):
            with self.subTest(route=route):
                response = self.client.get(reverse(route, args=args))
                self.assertEqual(response.status_code, 403)
        self.assertEqual(self.client.post(reverse('export_dashboard_report_pdf')).status_code, 403)
        for route in ('survey_analysis', 'user_management'):
            self.assertRedirects(self.client.get(reverse(route)), reverse('dashboard'), fetch_redirect_response=False)

    def test_head_keeps_ratings_export_controls_and_pdf_access(self):
        self.client.force_login(self.head)
        page = self.client.get(reverse('dashboard'))
        self.assertContains(page, f'href="{reverse("survey_analysis")}"')
        self.assertContains(page, 'id="reportDownload"')
        data = self.client.get(reverse('adm_dashboard_ajax'), {'report_month': '2026-10'}).json()
        self.assertTrue(data['can_generate_report'])
        self.assertIn('id="generateReportBtn"', data['html'])
        details = self.client.get(reverse('adm_report_detail', args=[self.report.pk]))
        self.assertContains(details, 'onclick="exportJobOrder()"')
        with patch('adm.views.pisa.CreatePDF', return_value=SimpleNamespace(err=0)) as pdf:
            response = self.client.get(reverse('exportJobOrderPDFAdmin', args=[self.report.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        pdf.assert_called_once()

    def test_modal_is_separate_from_overall_form_and_starts_with_add_button(self):
        response = self.client.get(reverse('adm_report_detail', args=[self.report.pk]))
        self.assertContains(response, '+ Add Personnel')
        self.assertContains(response, 'id="personnel-dialog"')
        self.assertContains(response, 'xl:grid-cols-4')
        self.assertContains(response, 'Mark Adrian Remigio')
        self.assertContains(response, 'Others')
        self.assertNotContains(response, 'List of Personnels')
        html = response.content.decode()
        self.assertGreater(html.index('id="personnel-assignment-form"'), html.index('</main>', html.index('id="report-save-form"')))

    def test_assignment_save_is_independent_and_displays_only_selected_names(self):
        self.report.priority = 'standard'
        self.report.findings = 'Issue inspected'
        self.report.recommendation = 'Repair the issue'
        self.report.pending_awaiting_personnel = True
        self.report.pending_awaiting_materials = True
        self.report.save()
        response = self.assign()
        self.assertEqual(response.status_code, 200)
        self.report.refresh_from_db()
        self.assertEqual(self.report.status, 'pending')
        self.assertIsNone(self.report.in_progress_at)
        self.assertEqual(self.report.priority, 'standard')
        self.assertEqual(self.report.findings, 'Issue inspected')
        self.assertEqual(self.report.assigned_personnel_data, [
            {'name': 'George L. Castro'}, {'name': 'Mark Adrian Remigio'},
        ])
        html = response.json()['html']
        self.assertIn('George L. Castro', html)
        self.assertIn('Mark Adrian Remigio', html)
        self.assertNotIn('Alfredo E. Badaguas', html)
        self.assertNotIn('Electrical', html)
        self.assertIn('Edit MED personnel assignment', html)
        details = self.client.get(reverse('adm_report_detail', args=[self.report.pk]))
        self.assertEqual(len(details.context['selected_personnel_groups']), 2)

    def test_edit_replace_and_clear_assignment_and_preserve_others_safely(self):
        self.assign()
        response = self.assign(
            personnel_names=['george-l-castro', 'george-l-castro'],
            include_other_personnel='1',
            other_personnel=['External Worker', 'External Worker', '', '<script>alert(1)</script>'],
        )
        self.assertEqual(response.status_code, 200)
        self.report.refresh_from_db()
        self.assertEqual(len(self.report.assigned_personnel_data), 3)
        self.assertNotIn('Mark Adrian Remigio', response.json()['html'])
        self.assertIn('&lt;script&gt;', response.json()['html'])
        self.assertNotIn('<script>', response.json()['html'])
        response = self.assign(personnel_names=[])
        self.assertEqual(response.status_code, 200)
        self.report.refresh_from_db()
        self.assertEqual(self.report.assigned_personnel_data, [])
        self.assertIn('+ Add Personnel', response.json()['html'])

    def test_invalid_selection_does_not_overwrite_saved_assignments(self):
        self.assign()
        for payload in (
            {'personnel_names': ['unknown-person']},
            {'include_other_personnel': '1', 'other_personnel': ['x' * 101]},
        ):
            with self.subTest(payload=payload):
                self.assertEqual(self.assign(**payload).status_code, 400)
                self.report.refresh_from_db()
                self.assertEqual(len(self.report.assigned_personnel_data), 2)

    def test_non_pending_assignments_are_locked_and_history_stays_visible(self):
        self.assign()
        for status in ('in_progress', 'resolved', 'closed'):
            with self.subTest(status=status):
                Report.objects.filter(pk=self.report.pk).update(status=status)
                self.assertEqual(self.assign(personnel_names=[]).status_code, 409)
                page = self.client.get(reverse('adm_report_detail', args=[self.report.pk]))
                self.assertContains(page, 'Mark Adrian Remigio')
                self.assertNotContains(page, 'id="personnel-dialog"')
                self.assertNotContains(page, 'data-open-personnel')
        self.report.refresh_from_db()
        self.assertEqual(len(self.report.assigned_personnel_data), 2)

    def test_assignment_requires_admin_post_and_csrf(self):
        url = reverse('adm_report_personnel', args=[self.report.pk])
        self.assertEqual(self.client.get(url).status_code, 405)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.admin)
        self.assertEqual(csrf_client.post(url, {}).status_code, 403)
        self.client.force_login(self.reporter)
        self.assertRedirects(self.assign(), reverse('userDashboard'), fetch_redirect_response=False)
        self.client.logout()
        self.assertEqual(self.assign().status_code, 302)
        self.report.refresh_from_db()
        self.assertEqual(self.report.assigned_personnel_data, [])

    def test_overall_save_uses_saved_assignment_and_retains_progression_requirements(self):
        self.assign()
        url = reverse('adm_report_save', args=[self.report.pk])
        data = {
            'category': 'electrical', 'priority': 'standard',
            'findings': 'Inspected', 'recommendation': 'Repair',
            'pending_awaiting_personnel': '1', 'pending_awaiting_materials': '1',
        }
        self.client.post(url, {**data, 'findings': ''})
        self.report.refresh_from_db()
        self.assertEqual(self.report.status, 'pending')
        self.assertEqual(len(self.report.assigned_personnel_data), 2)
        self.client.post(url, {**data, 'personnel_names': ['jayson-t-garcia']})
        self.report.refresh_from_db()
        self.assertEqual(self.report.status, 'in_progress')
        self.assertIsNotNone(self.report.in_progress_at)
        self.assertEqual(len(self.report.assigned_personnel_data), 2)
        self.assertEqual(self.report.assigned_personnel_data[0]['name'], 'George L. Castro')
