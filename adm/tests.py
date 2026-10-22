import json
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core import mail
from django.db import connection
from django.test import RequestFactory, TestCase, override_settings
from django.template.loader import render_to_string
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from django.core.cache import cache

from user.models import Notification, Report, SurveyResponse, UserProfile
from user.views import dashboardStatsAjax, get_notifications_page_ajax, userSurveyAjax

from .views import (
    build_rating_analytics_context,
    build_rating_responses_list,
    get_notifications_page_ajax_admin,
    _build_dashboard_data,
    _build_resolution_buckets,
    _cached_dashboard_data,
    _reports_for_resolution_buckets,
    _build_pdf_priority_tables,
)
from .user_management import _decorate
from .personnel import personnel_options


class RatingAnalyticsPaginationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user = User.objects.create_user(username="survey-pagination-test")
        reports = []
        for index in range(26):
            started_at = timezone.now()
            report = Report.objects.create(
                user=user,
                job_order_id=f"TEST-SURVEY-{index}",
                category="electrical",
                location="Test Building, 1, 101",
                description="Test report",
                status="closed",
                priority="standard",
                assigned_personnel_data=[{"name": "George L. Castro"}],
                in_progress_at=started_at,
                accomplished_at=started_at + timedelta(hours=1),
            )
            reports.append(report)
            SurveyResponse.objects.create(
                report=report,
                quality=5,
                quantity=4,
                timeliness=3,
            )
        Notification.objects.bulk_create([
            Notification(user=user, report=report, message=f"Notification {index}")
            for index, report in enumerate(reports)
        ])
        cls.user = user

    def test_responses_are_filtered_and_paginated_in_the_database(self):
        with CaptureQueriesContext(connection) as queries:
            first_page, page = build_rating_responses_list(1)

        second_page, second_page_obj = build_rating_responses_list(2)
        self.assertEqual(len(first_page), 25)
        self.assertEqual(len(second_page), 1)
        self.assertEqual(page.paginator.count, 26)
        self.assertEqual(second_page_obj.number, 2)
        # Includes one cold-cache lookup for the dynamic MED personnel names.
        self.assertLessEqual(len(queries), 3)

    def test_analytics_aggregate_scores_and_do_not_issue_survey_n_plus_one(self):
        # Isolate analytics queries from the separately tested cold roster lookup.
        personnel_options()
        with CaptureQueriesContext(connection) as queries:
            context = build_rating_analytics_context()

        self.assertEqual(context["total_surveys"], 26)
        self.assertEqual(context["avg_rating"], 4.0)
        self.assertEqual(context["rating_counts"], {1: 0, 2: 0, 3: 26, 4: 26, 5: 26})
        self.assertEqual(context["responses_total_count"], 26)
        self.assertLessEqual(len(queries), 4)

    def test_user_survey_history_is_paginated(self):
        request = RequestFactory().get("/user/surveys/?page=2")
        request.user = self.user

        response = userSurveyAjax(request)
        payload = json.loads(response.content)

        self.assertIn("Page 2 of 2", payload["html"])

    def test_dashboard_greeting_includes_logged_in_user_on_both_layouts(self):
        self.user.first_name = "Jane"
        request = RequestFactory().get("/user/dashboard/stats/")
        request.user = self.user

        payload = json.loads(dashboardStatsAjax(request).content)

        self.assertIn("Hello, Jane!", payload["mobile_html"])
        self.assertIn("Hello, Jane!", payload["desktop_html"])

    def test_user_notifications_are_paginated(self):
        request = RequestFactory().get("/user/notifications/ajax/?page=2")
        request.user = self.user

        response = get_notifications_page_ajax(request)
        payload = json.loads(response.content)

        self.assertEqual(payload["current_page"], 2)
        self.assertEqual(payload["total_count"], 26)
        self.assertIn("Page 2 of 2", payload["html"])

    def test_admin_notifications_are_paginated(self):
        request = RequestFactory().get("/adm/notifications/ajax/?page=2")
        request.user = self.user

        response = get_notifications_page_ajax_admin(request)
        payload = json.loads(response.content)

        self.assertEqual(payload["current_page"], 2)
        self.assertEqual(payload["total_count"], 26)
        self.assertIn("Page 2 of 2", payload["html"])

    def test_dashboard_resolution_buckets_use_one_query(self):
        buckets = _build_resolution_buckets("7", timezone.now())
        with CaptureQueriesContext(connection) as queries:
            reports_by_bucket = _reports_for_resolution_buckets(buckets)

        self.assertEqual(len(reports_by_bucket), 7)
        self.assertEqual(sum(map(len, reports_by_bucket)), 26)
        self.assertEqual(len(queries), 1)

    def test_dashboard_aggregates_preserve_report_and_rating_totals(self):
        dashboard = _build_dashboard_data("7")

        self.assertEqual(dashboard["avg_satisfaction"], 4.0)
        self.assertEqual(dashboard["satisfaction_counts"], [26, 26, 26, 0, 0])
        self.assertEqual(sum(dashboard["status_data"]), 26)
        self.assertEqual(sum(dashboard["categories_counts"]), 26)

    def test_pdf_priority_tables_use_one_query_for_all_buckets(self):
        with CaptureQueriesContext(connection) as queries:
            today = timezone.localdate()
            tables = _build_pdf_priority_tables(today, today)

        self.assertEqual(len(tables), 3)
        standard_table = next(table for table in tables if table["code"] == "standard")
        self.assertEqual(standard_table["completed"], 26)
        self.assertEqual(len(queries), 1)

    def test_dashboard_analytics_cache_reuses_result_for_same_period(self):
        cache.clear()
        self.addCleanup(cache.clear)
        with patch("adm.views._build_dashboard_data", return_value={"cached": True}) as build:
            first = _cached_dashboard_data("cache-test")
            second = _cached_dashboard_data("cache-test")

        self.assertEqual(first, second)
        build.assert_called_once()


class PendingReportProgressionTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username="progression-admin", is_staff=True)
        self.reporter = User.objects.create_user(username="progression-reporter")
        self.client.force_login(self.admin)
        self.report = Report.objects.create(
            user=self.reporter,
            job_order_id="PROGRESSION-TEST",
            category="electrical",
            location="Test Building, First Floor, Room 1",
            description="Test report",
        )

    def save_report(self, **overrides):
        data = {
            "category": "electrical",
            "priority": "standard",
            "personnel_names": ["george-l-castro"],
            "pending_awaiting_personnel": "1",
            "findings": "Inspected and identified the issue.",
            "recommendation": "Replace the damaged part.",
        }
        data.update(overrides)
        personnel_response = self.client.post(reverse('adm_report_personnel', args=[self.report.pk]), {
            'personnel_names': data.pop('personnel_names'),
        })
        self.assertEqual(personnel_response.status_code, 200)
        return self.client.post(reverse("adm_report_save", args=[self.report.pk]), data)

    def test_pending_form_marks_full_width_and_conditional_requirements(self):
        response = self.client.get(reverse("adm_report_detail", args=[self.report.pk]))

        self.assertEqual(response.content.count(b'data-cdd-class="w-full"'), 2)
        self.assertContains(response, 'class="flex-1 min-w-0 p-6 space-y-8 margin-bottom-8"')
        self.assertNotContains(response, "lg:max-h-[80vh]")
        self.assertContains(response, "Materials Available")
        self.assertContains(response, "Required if Materials Available is unchecked")
        self.assertContains(response, "Findings")
        self.assertContains(response, "Recommendation")
        self.assertContains(response, "Air-Conditioning and Mechanical")
        self.assertContains(response, "George L. Castro")

    def test_materials_available_allows_progress_without_a_material_list(self):
        response = self.save_report(pending_awaiting_materials="1")

        self.assertEqual(response.status_code, 302)
        self.report.refresh_from_db()
        self.assertEqual(self.report.status, "in_progress")
        self.assertEqual(self.report.materials_required, [])

    def test_materials_unavailable_needs_one_complete_material_row(self):
        response = self.save_report(
            mat_qty=["1"],
            mat_unit=["pcs"],
            mat_desc=["Replacement cable"],
        )

        self.assertEqual(response.status_code, 302)
        self.report.refresh_from_db()
        self.assertEqual(self.report.status, "in_progress")
        self.assertEqual(self.report.materials_required, [{
            "qty": "1", "unit": "pcs", "description": "Replacement cable",
        }])

    def test_materials_unavailable_without_complete_row_stays_pending(self):
        self.save_report()

        self.report.refresh_from_db()
        self.assertEqual(self.report.status, "pending")


    def test_incomplete_material_row_stays_pending(self):
        self.save_report(
            mat_qty=["1"],
            mat_unit=[""],
            mat_desc=["Replacement cable"],
        )

        self.report.refresh_from_db()
        self.assertEqual(self.report.status, "pending")
        self.assertEqual(self.report.materials_required, [])

    def test_findings_and_recommendation_are_required_for_progression(self):
        self.save_report(pending_awaiting_materials="1", findings="")
        self.report.refresh_from_db()
        self.assertEqual(self.report.status, "pending")

        self.save_report(
            pending_awaiting_materials="1",
            findings="Inspection completed.",
            recommendation="",
        )
        self.report.refresh_from_db()
        self.assertEqual(self.report.status, "pending")

    def test_priority_and_with_assigned_personnel_are_required_for_progression(self):
        self.save_report(pending_awaiting_materials="1", priority="")
        self.report.refresh_from_db()
        self.assertEqual(self.report.status, "pending")

        self.save_report(pending_awaiting_materials="1", pending_awaiting_personnel="")
        self.report.refresh_from_db()
        self.assertEqual(self.report.status, "pending")


@override_settings(
    PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
)
class StaffAdminPromotionTests(TestCase):
    def setUp(self):
        cache.clear()
        self.head = User.objects.create_user(username='head-role-admin', is_staff=True, password='test')
        head_profile, _ = UserProfile.objects.get_or_create(user=self.head)
        head_profile.role = 'admin'
        head_profile.is_head_admin = True
        head_profile.save(update_fields=['role', 'is_head_admin'])
        self.account = User.objects.create_user(
            username='staff-account', email='worker@gmail.com', is_active=True,
            first_name='Test', last_name='Staff',
        )
        account_profile, _ = UserProfile.objects.get_or_create(user=self.account)
        account_profile.role = 'staff'
        account_profile.save(update_fields=['role'])
        self.client.force_login(self.head)

    def action(self, action='make_admin', password='test', **extra):
        return self.client.post(
            reverse('user_management_action', args=[self.account.pk]),
            data=json.dumps({'action': action, 'password': password, **extra}),
            content_type='application/json',
        )

    def test_head_can_promote_staff_and_remove_admin_without_changing_base_role(self):
        response = self.action()
        self.assertEqual(response.status_code, 200)
        self.account.refresh_from_db()
        self.assertTrue(self.account.is_staff)
        self.assertFalse(self.account.is_superuser)
        self.assertEqual(self.account.profile.role, 'staff')
        self.assertFalse(self.account.profile.is_head_admin)
        self.assertEqual(self.action('remove_admin').status_code, 200)
        self.account.refresh_from_db()
        self.assertFalse(self.account.is_staff)
        self.assertEqual(self.account.profile.role, 'staff')

    def test_promotion_requires_correct_head_password(self):
        for password in ('', 'wrong-password'):
            with self.subTest(password=password):
                self.assertEqual(self.action(password=password).status_code, 403)
        self.account.refresh_from_db()
        self.assertFalse(self.account.is_staff)
        self.assertFalse(Notification.objects.filter(user=self.account).exists())

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_successful_promotion_sends_email_and_private_readable_system_notification(self):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.action()
        self.assertEqual(response.status_code, 200)
        alert = Notification.objects.get(user=self.account)
        self.assertEqual(alert.account_event, Notification.ADMIN_ACCESS_GRANTED)
        self.assertIsNone(alert.report_id)
        self.assertIsNone(alert.staff_account_id)
        self.assertFalse(alert.is_read)
        self.assertFalse(Notification.objects.exclude(user=self.account).exists())
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.account.email])
        self.assertIn('admin access', mail.outbox[0].subject.lower())
        self.assertIn('admin dashboard', mail.outbox[0].body.lower())

        self.assertEqual(self.action().status_code, 409)
        self.assertEqual(Notification.objects.filter(user=self.account).count(), 1)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(self.client.get(reverse('view_notification_admin', args=[alert.pk])).status_code, 404)

        self.account.refresh_from_db()
        self.client.force_login(self.account)
        payload = self.client.get(reverse('get_notifications_ajax_admin'), {'filter': 'unread'}).json()
        self.assertEqual(payload['unread_count'], 1)
        self.assertEqual(payload['notifications'][0]['status'], Notification.ADMIN_ACCESS_GRANTED)
        page = self.client.get(reverse('get_notifications_page_ajax_admin')).json()['html']
        self.assertIn('Admin access granted', page)
        self.assertIn(reverse('view_notification_admin', args=[alert.pk]), page)
        self.assertRedirects(
            self.client.get(reverse('view_notification_admin', args=[alert.pk])),
            reverse('dashboard'), fetch_redirect_response=False,
        )
        alert.refresh_from_db()
        self.assertTrue(alert.is_read)
        unread = self.client.get(reverse('get_notifications_ajax_admin'), {'filter': 'unread'}).json()
        self.assertEqual(unread['unread_count'], 0)
        self.assertEqual(unread['notifications'], [])

    def test_promotion_alert_remains_safe_to_open_after_admin_access_is_removed(self):
        self.assertEqual(self.action().status_code, 200)
        alert = Notification.objects.get(user=self.account)
        self.assertEqual(self.action('remove_admin').status_code, 200)
        self.account.refresh_from_db()
        self.client.force_login(self.account)
        payload = self.client.get(reverse('get_notifications_ajax')).json()
        self.assertEqual(payload['notifications'][0]['status'], Notification.ADMIN_ACCESS_GRANTED)
        self.assertIsNone(payload['notifications'][0]['completion_photo_url'])
        self.assertRedirects(
            self.client.get(reverse('view_notification', args=[alert.pk])),
            reverse('userDashboard'), fetch_redirect_response=False,
        )
        alert.refresh_from_db()
        self.assertTrue(alert.is_read)

    def test_removal_creates_unread_staff_notification_that_opens_staff_dashboard(self):
        self.assertEqual(self.action().status_code, 200)
        with patch('adm.user_management._notify'):
            response = self.action('remove_admin')
        self.assertEqual(response.status_code, 200)

        alert = Notification.objects.get(user=self.account, account_event=Notification.ADMIN_ACCESS_REMOVED)
        self.assertIn('removed your Admin access', alert.message)
        self.assertIn('Staff account', alert.message)
        self.assertIsNone(alert.report_id)
        self.assertIsNone(alert.staff_account_id)
        self.assertFalse(alert.is_read)
        with patch('adm.user_management._notify'):
            self.assertEqual(self.action('remove_admin').status_code, 409)
        self.assertEqual(Notification.objects.filter(user=self.account, account_event=Notification.ADMIN_ACCESS_REMOVED).count(), 1)

        self.client.force_login(self.account)
        payload = self.client.get(reverse('get_notifications_ajax'), {'filter': 'unread'}).json()
        self.assertEqual(payload['unread_count'], 2)
        self.assertIn(Notification.ADMIN_ACCESS_REMOVED, [item['status'] for item in payload['notifications']])
        page = self.client.get(reverse('get_notifications_page_ajax')).json()['html']
        self.assertIn('Admin access removed', page)
        self.assertIn(reverse('view_notification', args=[alert.pk]), page)
        self.assertRedirects(
            self.client.get(reverse('view_notification', args=[alert.pk])),
            reverse('userDashboard'), fetch_redirect_response=False,
        )
        alert.refresh_from_db()
        self.assertTrue(alert.is_read)
        payload = self.client.get(reverse('get_notifications_ajax'), {'filter': 'unread'}).json()
        self.assertEqual(payload['unread_count'], 1)
        self.assertNotIn(Notification.ADMIN_ACCESS_REMOVED, [item['status'] for item in payload['notifications']])

    def test_only_staff_can_be_promoted_even_with_forged_role_payload(self):
        for role in ('student', 'faculty', 'med_personnel'):
            with self.subTest(role=role):
                UserProfile.objects.filter(user=self.account).update(role=role)
                response = self.action(role='staff')
                self.assertEqual(response.status_code, 403)
                self.account.refresh_from_db()
                self.assertFalse(self.account.is_staff)
                self.assertEqual(self.account.profile.role, role)

    def test_change_role_action_is_no_longer_available(self):
        self.assertEqual(self.action('change_role', role='admin').status_code, 400)
        self.account.profile.refresh_from_db()
        self.assertEqual(self.account.profile.role, 'staff')

    def test_inactive_or_pending_staff_cannot_be_promoted(self):
        UserProfile.objects.filter(user=self.account).update(approval_status=UserProfile.APPROVAL_PENDING)
        self.assertEqual(self.action().status_code, 409)
        UserProfile.objects.filter(user=self.account).update(approval_status=UserProfile.APPROVAL_APPROVED)
        self.account.is_active = False
        self.account.save(update_fields=['is_active'])
        self.assertEqual(self.action().status_code, 409)

    def test_regular_admin_cannot_promote_accounts(self):
        admin = User.objects.create_user(username='regular-admin', is_staff=True, password='test')
        self.client.force_login(admin)
        self.assertEqual(self.action().status_code, 403)
        self.account.refresh_from_db()
        self.assertFalse(self.account.is_staff)

    def test_regular_staff_cannot_promote_itself(self):
        self.account.set_password('test')
        self.account.save(update_fields=['password'])
        self.client.force_login(self.account)
        response = self.action()
        self.assertRedirects(response, reverse('userDashboard'), fetch_redirect_response=False)
        self.account.refresh_from_db()
        self.assertFalse(self.account.is_staff)

    def test_head_user_management_page_and_ajax_offer_direct_staff_promotion(self):
        response = self.client.get(reverse('user_management'))
        self.assertContains(response, 'Enter your password to confirm')
        self.assertNotContains(response, 'changeRoleNeedsPassword')
        response = self.client.get(reverse('user_management_ajax'), {'search': self.account.email})
        self.assertEqual(response.status_code, 200)
        for key in ('desktop_html', 'mobile_html'):
            self.assertIn('data-um-action="make_admin"', response.json()[key])
            self.assertNotIn('data-um-action="change_role"', response.json()[key])

    def test_pending_staff_layout_supports_mobile_cards_and_desktop_details(self):
        UserProfile.objects.filter(user=self.account).update(
            approval_status=UserProfile.APPROVAL_PENDING,
        )

        response = self.client.get(reverse('user_management'))

        self.assertContains(response, 'sm:grid-cols-2')
        self.assertContains(response, 'lg:grid-cols-[minmax(0,1fr)_auto]')
        self.assertContains(response, 'xl:grid-cols-[minmax(0,1.35fr)_minmax(160px,1.2fr)_minmax(115px,0.8fr)_minmax(110px,0.75fr)_auto]')
        self.assertContains(response, 'lg:bg-gray-100')
        self.assertContains(response, 'lg:not-sr-only">Office</dt>')
        self.assertContains(response, 'data-um-action="approve"')
        self.assertContains(response, 'data-um-action="reject"')

    def test_active_user_buttons_match_role_and_deactivation_still_works(self):
        for role in ('student', 'faculty', 'staff'):
            with self.subTest(role=role):
                UserProfile.objects.filter(user=self.account).update(role=role)
                target = _decorate([User.objects.select_related('profile').get(pk=self.account.pk)])[0]
                html = render_to_string('partials/_user_action_buttons.html', {'u': target, 'me': self.head})
                self.assertIn('data-um-action="deactivate"', html)
                self.assertNotIn('change_role', html)
                self.assertEqual('data-um-action="make_admin"' in html, role == 'staff')
        response = self.action('deactivate')
        self.assertEqual(response.status_code, 200)
        self.account.refresh_from_db()
        self.assertFalse(self.account.is_active)
        self.assertIsNotNone(self.account.profile.deactivated_at)
        self.assertEqual(self.action('reactivate').status_code, 200)

    def test_admin_promotion_remains_rate_limited(self):
        with patch('adm.user_management.is_ratelimited', return_value=True):
            self.assertEqual(self.action().status_code, 429)
        self.account.refresh_from_db()
        self.assertFalse(self.account.is_staff)


class AdminProfileMenuTests(TestCase):
    def test_profile_summary_omits_redundant_username_row(self):
        admin = User.objects.create_user(
            username="profile-admin",
            email="profile-admin@example.com",
            first_name="Profile",
            last_name="Admin",
            is_staff=True,
        )
        self.client.force_login(admin)

        response = self.client.get(reverse("adm_menu_ajax"))
        html = response.json()["html"]

        self.assertIn(admin.get_full_name(), html)
        self.assertIn(admin.email, html)
        self.assertNotIn(f">{admin.username}</span>", html)
