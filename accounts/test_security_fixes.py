"""Regression tests for the security fixes: Django admin sign-in, resend-code limits,
the floor/room lookup crash, Forgot Password account probing, read-only profile email,
and re-resolving reports."""
from io import BytesIO

from django.contrib.auth.models import User
from django.core import mail
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from user.models import Report, UserProfile


def _png(name='proof.png'):
    buffer = BytesIO()
    Image.new('RGB', (4, 4), 'white').save(buffer, format='PNG')
    return SimpleUploadedFile(name, buffer.getvalue(), content_type='image/png')


class SecurityFixTestCase(TestCase):
    def setUp(self):
        # Rate-limit counters live in the cache; start every test from zero
        cache.clear()

    def make_user(self, email, role='student', is_staff=False, password='Sample#Pass1'):
        user = User.objects.create_user(username=email, email=email, password=password, is_staff=is_staff)
        # A profile is created automatically with the user (user/signals.py); fill it in
        UserProfile.objects.update_or_create(
            user=user, defaults={'role': role, 'department': 'CCS', 'phone': '09123456789'},
        )
        return user


class AdminLoginRequiresFacCheckLoginTests(SecurityFixTestCase):
    def test_admin_login_page_redirects_to_fac_check_login(self):
        response = self.client.get('/admin/login/?next=/admin/')
        self.assertRedirects(response, reverse('login') + '?next=%2Fadmin%2F', fetch_redirect_response=False)

    def test_password_alone_cannot_sign_in_through_admin(self):
        self.make_user('staff-admin@example.com', role='staff', is_staff=True)
        response = self.client.post('/admin/login/', {'username': 'staff-admin@example.com', 'password': 'Sample#Pass1'})
        self.assertEqual(response.status_code, 302)
        self.assertNotIn('_auth_user_id', self.client.session)


class ResendCodeLimitTests(SecurityFixTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.make_user('resend-client@example.com')
        session = self.client.session
        session['pending_login_user_id'] = self.user.pk
        session.save()

    def test_resend_links_refuse_plain_get(self):
        for name in ('resend_login_otp', 'resend_otp', 'resend_reset_otp'):
            with self.subTest(view=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 405)
        self.assertEqual(len(mail.outbox), 0)

    def test_resend_is_rate_limited(self):
        self.client.post(reverse('resend_login_otp'))
        self.client.post(reverse('resend_login_otp'))
        third = self.client.post(reverse('resend_login_otp'), follow=True)
        self.assertContains(third, "Please wait before requesting another code")
        self.assertEqual(len(mail.outbox), 2)


class LocationLookupTests(SecurityFixTestCase):
    def test_non_numeric_ids_return_empty_list_instead_of_crashing(self):
        self.client.force_login(self.make_user('lookup-client@example.com'))
        for url in (reverse('ajax_load_floors') + '?building=abc', reverse('ajax_load_rooms') + '?floor=abc'):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json(), [])


class ForgotPasswordDoesNotRevealAccountsTests(SecurityFixTestCase):
    def test_unknown_email_looks_like_a_real_reset(self):
        response = self.client.post(reverse('forgot_password'), {'email': 'nobody@example.com'})
        self.assertRedirects(response, reverse('verify_reset_otp'), fetch_redirect_response=False)
        self.assertEqual(len(mail.outbox), 0)

        page = self.client.get(reverse('verify_reset_otp'))
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, 'No account found')

        guess = self.client.post(reverse('verify_reset_otp'), {'otp': '123456'})
        self.assertContains(guess, 'Invalid verification code. You have 4 attempts left.')
        self.assertNotIn('password_reset_verified_user_id', self.client.session)

    def test_real_account_still_gets_a_code(self):
        self.make_user('reset-client@example.com')
        response = self.client.post(reverse('forgot_password'), {'email': 'reset-client@example.com'})
        self.assertRedirects(response, reverse('verify_reset_otp'), fetch_redirect_response=False)
        self.assertEqual(len(mail.outbox), 1)

    def test_duplicate_emails_no_longer_crash(self):
        self.make_user('shared@example.com')
        other = self.make_user('other@example.com')
        User.objects.filter(pk=other.pk).update(email='shared@example.com')
        response = self.client.post(reverse('forgot_password'), {'email': 'shared@example.com'})
        self.assertEqual(response.status_code, 302)


class ProfileEmailIsReadOnlyTests(SecurityFixTestCase):
    def test_profile_save_ignores_a_new_email(self):
        user = self.make_user('profile-client@udm.edu.ph')
        self.client.force_login(user)
        self.client.post(reverse('menu'), {
            'save_info': '1', 'first_name': 'Maria', 'last_name': 'Santos',
            'email': 'attacker@example.com', 'department': 'CCS', 'phone': '09123456789',
        })
        user.refresh_from_db()
        self.assertEqual(user.email, 'profile-client@udm.edu.ph')


@override_settings(STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.InMemoryStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
})
class ResolveOnlyInProgressReportsTests(SecurityFixTestCase):
    def test_closed_report_cannot_be_resolved_again(self):
        admin = self.make_user('report-admin@example.com', role='staff', is_staff=True)
        client_user = self.make_user('report-client@example.com')
        report = Report.objects.create(
            user=client_user, job_order_id='SEC-FIX-1', category='electrical',
            location='Test Room', description='Closed already', status='closed',
        )
        self.client.force_login(admin)
        self.client.post(reverse('adm_report_status', args=[report.pk]), {
            'status': 'resolved',
            'accomplished_at': timezone.localtime().strftime('%Y-%m-%dT%H:%M'),
            'completion_photo': _png(),
        })
        report.refresh_from_db()
        self.assertEqual(report.status, 'closed')
        self.assertFalse(report.completion_photo)
