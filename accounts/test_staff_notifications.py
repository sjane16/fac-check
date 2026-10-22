from importlib import import_module
from types import SimpleNamespace
from unittest.mock import patch

from django.apps import apps
from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.template.loader import render_to_string
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from user.context_processors import get_unread_notifications_count
from user.models import Notification, Report, UserProfile
from user.notifications import notify_staff_approval, serialize_notification


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class StaffApprovalNotificationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.head = User.objects.create_user(username='notification-head', is_staff=True)
        UserProfile.objects.update_or_create(user=cls.head, defaults={'role': 'admin', 'is_head_admin': True})
        cls.admin = User.objects.create_user(username='notification-admin', is_staff=True)
        UserProfile.objects.update_or_create(user=cls.admin, defaults={'role': 'admin'})
        cls.staff = User.objects.create_user(
            username='notification-staff', email='staff@gmail.com',
            first_name='Anna', last_name='Santos', is_active=False,
        )
        UserProfile.objects.update_or_create(user=cls.staff, defaults={
            'role': 'staff', 'approval_status': UserProfile.APPROVAL_PENDING,
            'otp': '123456', 'otp_created_at': timezone.now(),
        })

    def verify_email(self, user, otp='123456'):
        session = self.client.session
        session['pending_verification_user_id'] = user.pk
        session.save()
        with patch('accounts.views.is_ratelimited', return_value=False):
            return self.client.post(reverse('verify_otp'), {'otp': otp})

    def test_verified_staff_notifies_only_active_head_admins_once(self):
        second_head = User.objects.create_user(username='second-head', is_superuser=True)
        UserProfile.objects.update_or_create(user=second_head, defaults={'role': 'admin', 'is_head_admin': True})
        inactive_head = User.objects.create_user(username='inactive-head', is_staff=True, is_active=False)
        UserProfile.objects.update_or_create(user=inactive_head, defaults={'role': 'admin', 'is_head_admin': True})
        non_admin = User.objects.create_user(username='non-admin-head')
        UserProfile.objects.update_or_create(user=non_admin, defaults={'role': 'staff', 'is_head_admin': True})

        notify_staff_approval(self.staff)
        self.verify_email(self.staff, '999999')
        self.assertFalse(Notification.objects.exists())
        response = self.verify_email(self.staff)
        self.assertRedirects(response, reverse('login'), fetch_redirect_response=False)
        notify_staff_approval(self.staff)
        notify_staff_approval(self.staff)
        self.assertSetEqual(
            set(Notification.objects.values_list('user_id', flat=True)),
            {self.head.pk, second_head.pk},
        )
        self.assertEqual(Notification.objects.count(), 2)
        alert = Notification.objects.get(user=self.head)
        self.assertIsNone(alert.report_id)
        self.assertEqual(alert.staff_account_id, self.staff.pk)
        self.assertIn('Anna Santos', alert.message)
        self.assertFalse(alert.is_read)
        self.assertEqual(self.staff.profile.approval_status, UserProfile.APPROVAL_PENDING)

    def test_students_faculty_and_approved_staff_do_not_generate_approval_alerts(self):
        for role in ('student', 'faculty'):
            with self.subTest(role=role):
                user = User.objects.create_user(username=role, is_active=False)
                UserProfile.objects.update_or_create(user=user, defaults={
                    'role': role, 'otp': '123456', 'otp_created_at': timezone.now(),
                })
                self.verify_email(user)
        UserProfile.objects.filter(user=self.staff).update(approval_status=UserProfile.APPROVAL_APPROVED)
        self.verify_email(self.staff)
        self.assertFalse(Notification.objects.exists())

    def test_notification_bell_and_click_open_staff_approval_without_approving(self):
        self.verify_email(self.staff)
        alert = Notification.objects.get(user=self.head)
        self.client.force_login(self.head)
        payload = self.client.get(reverse('get_notifications_ajax_admin')).json()
        self.assertEqual(payload['unread_count'], 1)
        self.assertEqual(payload['notifications'][0]['staff_account_id'], self.staff.pk)
        self.assertEqual(payload['notifications'][0]['status'], 'staff_approval')
        self.assertIsNone(serialize_notification(alert, True)['completion_photo_url'])
        page = self.client.get(reverse('get_notifications_page_ajax_admin')).json()
        self.assertIn(alert.message, page['html'])
        self.assertIn(reverse('view_notification_admin', args=[alert.pk]), page['html'])

        response = self.client.get(reverse('view_notification_admin', args=[alert.pk]))
        self.assertRedirects(
            response, reverse('user_management') + f'#staff-{self.staff.pk}',
            fetch_redirect_response=False,
        )
        alert.refresh_from_db()
        self.assertTrue(alert.is_read)
        self.staff.profile.refresh_from_db()
        self.assertEqual(self.staff.profile.approval_status, UserProfile.APPROVAL_PENDING)
        self.assertContains(self.client.get(reverse('user_management')), f'id="staff-{self.staff.pk}"')
        unread = self.client.get(reverse('get_notifications_ajax_admin'), {'filter': 'unread'}).json()
        self.assertEqual(unread['unread_count'], 0)
        self.assertEqual(unread['notifications'], [])

    def test_other_accounts_cannot_open_alert_and_demoted_head_cannot_see_it(self):
        self.verify_email(self.staff)
        alert = Notification.objects.get(user=self.head)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse('view_notification_admin', args=[alert.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse('get_notifications_ajax_admin')).json()['notifications'], [])
        UserProfile.objects.filter(user=self.head).update(is_head_admin=False)
        self.client.force_login(self.head)
        self.assertEqual(self.client.get(reverse('view_notification_admin', args=[alert.pk])).status_code, 404)
        self.assertRedirects(
            self.client.get(reverse('view_notification', args=[alert.pk])),
            reverse('dashboard'), fetch_redirect_response=False,
        )
        self.assertEqual(self.client.get(reverse('get_notifications_ajax_admin')).json()['unread_count'], 0)
        request = RequestFactory().get('/')
        self.head.refresh_from_db()
        request.user = self.head
        self.assertEqual(get_unread_notifications_count(request), 0)
        self.head.is_staff = False
        self.head.save(update_fields=['is_staff'])
        self.client.force_login(self.head)
        self.assertEqual(self.client.get(reverse('view_notification', args=[alert.pk])).status_code, 404)
        alert.refresh_from_db()
        self.assertFalse(alert.is_read)

    def test_approval_clears_all_head_unread_alerts_and_old_link_still_works(self):
        second_head = User.objects.create_user(username='second-head', is_staff=True)
        UserProfile.objects.update_or_create(user=second_head, defaults={'role': 'admin', 'is_head_admin': True})
        self.verify_email(self.staff)
        alert = Notification.objects.get(user=self.head)
        self.client.force_login(self.head)
        with patch('adm.user_management._notify'):
            response = self.client.post(
                reverse('user_management_action', args=[self.staff.pk]),
                {'action': 'approve'}, content_type='application/json',
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Notification.objects.filter(staff_account=self.staff).count(), 2)
        self.assertFalse(Notification.objects.filter(staff_account=self.staff, is_read=False).exists())
        self.assertRedirects(
            self.client.get(reverse('view_notification_admin', args=[alert.pk])),
            reverse('user_management') + '#pendingCard', fetch_redirect_response=False,
        )

    def test_rejection_removes_alerts_with_the_account(self):
        self.verify_email(self.staff)
        self.client.force_login(self.head)
        with patch('adm.user_management._notify'):
            response = self.client.post(
                reverse('user_management_action', args=[self.staff.pk]),
                {'action': 'reject'}, content_type='application/json',
            )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(pk=self.staff.pk).exists())
        self.assertFalse(Notification.objects.exists())

    def test_backfill_is_idempotent_and_only_includes_verified_pending_staff(self):
        self.staff.is_active = True
        self.staff.save(update_fields=['is_active'])
        approved = User.objects.create_user(username='approved-staff')
        UserProfile.objects.update_or_create(user=approved, defaults={'role': 'staff'})
        inactive = User.objects.create_user(username='unverified-staff', is_active=False)
        UserProfile.objects.update_or_create(user=inactive, defaults={
            'role': 'staff', 'approval_status': UserProfile.APPROVAL_PENDING,
        })
        backfill = import_module('user.migrations.0042_staff_approval_notifications').backfill_staff_notifications
        editor = SimpleNamespace(connection=SimpleNamespace(alias='default'))
        backfill(apps, editor)
        backfill(apps, editor)
        notify_staff_approval(self.staff)
        self.assertEqual(Notification.objects.count(), 1)
        self.assertEqual(Notification.objects.get().staff_account_id, self.staff.pk)

    def test_duplicate_alerts_and_notifications_without_a_target_are_rejected(self):
        self.verify_email(self.staff)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Notification.objects.create(user=self.head, staff_account=self.staff, message='Duplicate')
        with self.assertRaises(IntegrityError), transaction.atomic():
            Notification.objects.create(user=self.head, message='No target')
        with self.assertRaises(IntegrityError), transaction.atomic():
            Notification.objects.create(
                user=self.staff, staff_account=self.staff,
                account_event=Notification.ADMIN_ACCESS_GRANTED, message='Mixed notification targets',
            )
        with self.assertRaises(IntegrityError), transaction.atomic():
            Notification.objects.create(user=self.staff, account_event='unknown', message='Unknown account event')

    def test_report_notifications_keep_their_status_and_destination(self):
        report = Report.objects.create(
            user=self.staff, category='electrical', location='Room', description='Test report',
        )
        alert = Notification.objects.create(user=self.head, report=report, message='Report submitted')
        self.client.force_login(self.head)
        payload = self.client.get(reverse('get_notifications_ajax_admin')).json()
        self.assertEqual(payload['notifications'][0]['report_id'], report.pk)
        self.assertEqual(payload['notifications'][0]['status'], 'pending')
        self.assertRedirects(
            self.client.get(reverse('view_notification_admin', args=[alert.pk])),
            reverse('adm_report_detail', args=[report.pk]), fetch_redirect_response=False,
        )

    def test_navigation_has_no_users_badge_and_bell_shows_unread_alert(self):
        self.verify_email(self.staff)
        self.client.force_login(self.head)
        response = self.client.get(reverse('user_management'))
        self.assertContains(response, 'id="desktop_notif_badge"')
        self.assertEqual(response.context['unread_notifications_count'], 1)
        for template in ('components/sidebar.html', 'components/mobile_nav.html'):
            html = render_to_string(template, {'user': self.head, 'is_head_admin': True, 'pending_staff_count': 5})
            self.assertIn('Users', html)
            self.assertNotIn('data-pending-badge', html)
        self.assertNotContains(response, 'data-pending-badge')
