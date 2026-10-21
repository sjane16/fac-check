# Run these tests from the project root (with the virtual environment activated):
# python manage.py test accounts.tests.LandingNavigationTests --verbosity 2
#
# Scope: landing-page links and signed-out redirects. These tests do not verify
# successful login, OTP delivery/verification, browser styling, or middleware.
#
# urlparse separates a redirect URL into its path and query string.
# parse_qs converts a query string into a dictionary of lists.
from urllib.parse import parse_qs, urlparse
import json
from smtplib import SMTPException
from types import SimpleNamespace
from unittest.mock import patch

# AnonymousUser represents a visitor who has not logged in.
from django.contrib.auth.models import AnonymousUser, User
from django.http import HttpResponse
# RequestFactory builds requests without a browser or running development server.
# SimpleTestCase blocks database queries by default; these checks need no test data.
from django.test import Client, RequestFactory, SimpleTestCase, TestCase, override_settings
from django.utils import timezone
# reverse: route name -> URL. resolve: URL -> registered view.
from django.urls import resolve, reverse

# Import the real landing view so its template and URL tags are rendered.
from accounts.forms import SignUpForm
from accounts.views import contact, splash_screen
from user.models import Passkey, UserProfile


class ContactFormTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.data = {
            'name': 'Test Sender', 'email': 'sender@example.com',
            'phone': '', 'message': 'Please help with my account.',
        }

    @override_settings(CONTACT_EMAIL='team@example.com', DEFAULT_FROM_EMAIL='Fac-Check <no-reply@example.com>')
    @patch('accounts.views.is_ratelimited', return_value=False)
    @patch('accounts.views.EmailMessage')
    def test_valid_message_is_sent_with_reply_to(self, email_message, _limited):
        request = self.factory.post('/auth/contact/', self.data,
                                    HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        response = contact(request)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(json.loads(response.content)['ok'])
        self.assertEqual(email_message.call_args.kwargs['to'], ['team@example.com'])
        self.assertEqual(email_message.call_args.kwargs['reply_to'], ['sender@example.com'])
        email_message.return_value.send.assert_called_once_with(fail_silently=False)

    @patch('accounts.views.is_ratelimited', return_value=False)
    @patch('accounts.views.EmailMessage.send')
    def test_invalid_message_is_not_sent(self, send, _limited):
        request = self.factory.post('/auth/contact/', {**self.data, 'email': 'invalid'},
                                    HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        response = contact(request)
        self.assertEqual(response.status_code, 400)
        send.assert_not_called()

    @patch('accounts.views.is_ratelimited', return_value=True)
    @patch('accounts.views.EmailMessage.send')
    def test_rate_limited_message_is_not_sent(self, send, _limited):
        request = self.factory.post('/auth/contact/', self.data,
                                    HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        response = contact(request)
        self.assertEqual(response.status_code, 429)
        send.assert_not_called()

    @patch('accounts.views.is_ratelimited', return_value=False)
    @patch('accounts.views.EmailMessage.send', side_effect=SMTPException('offline'))
    def test_delivery_failure_is_reported(self, _send, _limited):
        request = self.factory.post('/auth/contact/', self.data,
                                    HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        response = contact(request)
        self.assertEqual(response.status_code, 503)
        self.assertFalse(json.loads(response.content)['ok'])


# Regression tests protect existing navigation against future route changes.
# Django discovers methods whose names start with test_.
class LandingNavigationTests(SimpleTestCase):
    # Django runs setUp before EACH test, giving it a fresh request factory.
    def setUp(self):
        self.factory = RequestFactory()

    # Verify that the landing page renders and includes every expected destination.
    # A removed or misspelled URL name can raise NoReverseMatch here.
    def test_landing_page_renders_with_correct_links(self):
        # Simulate GET /auth/index/. RequestFactory skips middleware, so we
        # explicitly attach the signed-out user normally supplied by middleware.
        request = self.factory.get(reverse('index'))
        request.user = AnonymousUser()

        # Call the real view directly and inspect the HTML response it produces.
        response = splash_screen(request)

        # HTTP 200 means the page was returned successfully.
        self.assertEqual(response.status_code, 200)

        # These are the name= values in urls.py, not template filenames.
        # Include the navigation links and all five feature shortcuts.
        route_names = [
            'login',
            'signup',
            'reportForm',
            'userReports',
            'userSurvey',
            'reports',
            'dashboard',
        ]

        # subTest identifies the failing route and lets other routes be checked.
        for name in route_names:
            with self.subTest(route=name):
                # The rendered HTML must contain a link to this resolved URL.
                self.assertContains(
                    response,
                    f'href="{reverse(name)}"',
                )

    # Verify that protected shortcuts send visitors to the shared login page
    # and preserve the selected destination in the next query parameter.
    def test_signed_out_shortcuts_redirect_to_login(self):
        # Only protected feature pages belong here; login and signup are public.
        route_names = [
            'reportForm',
            'userReports',
            'userSurvey',
            'reports',
            'dashboard',
        ]

        for name in route_names:
            with self.subTest(route=name):
                # Resolve the route name so the test follows the URL configuration.
                destination = reverse(name)

                # Simulate a signed-out visitor opening this shortcut.
                request = self.factory.get(destination)
                request.user = AnonymousUser()

                # Call the URL-registered view, including its role decorator.
                # The decorator must redirect before the feature view runs.
                view = resolve(destination).func
                response = view(request)

                # HTTP 302 tells the browser to visit a different URL.
                self.assertEqual(response.status_code, 302)

                # Example: /auth/login/?next=/user/report_form/
                # Inspect the login path separately from the next parameter.
                redirect_url = urlparse(response.url)

                # Both user and admin shortcuts must use the shared login route.
                self.assertEqual(
                    redirect_url.path,
                    reverse('login'),
                )

                # next must retain the exact page the visitor selected.
                # parse_qs returns lists, so compare against [destination].
                # This checks the redirect value, not the later OTP return flow.
                self.assertEqual(
                    parse_qs(redirect_url.query).get('next'),
                    [destination],
                )


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class SignupMergeTests(TestCase):
    def signup_account(self, role, email):
        session = self.client.session
        session['privacy_viewed'] = True
        session.save()
        with patch('accounts.views.is_ip_flagged', return_value=False), \
             patch('accounts.views.is_ratelimited', return_value=False), \
             patch('accounts.views.send_mail'):
            response = self.client.post(reverse('signup'), {
                'first_name': 'Anna', 'last_name': 'Santos', 'email': email,
                'password': 'Campus!Safe2048', 'confirm_password': 'Campus!Safe2048',
                'role': role, 'department': 'CCS', 'phone': '09123456789',
                'agree_to_terms': 'on',
            })
        self.assertRedirects(response, reverse('verify_otp'), fetch_redirect_response=False)
        return User.objects.get(email=email)

    def test_signup_roles_and_email_domains(self):
        self.assertEqual(
            [value for value, _ in SignUpForm.base_fields['role'].choices if value],
            ['student', 'faculty', 'staff'],
        )
        for role, email, valid in [
            ('staff', 'worker@gmail.com', True),
            ('staff', 'WORKER@GMAIL.COM', True),
            ('staff', 'worker@yahoo.com', False),
            ('staff', 'worker@udm.edu.ph', False),
            ('staff', 'worker@gmail.com.example.com', False),
            ('student', 'student@udm.edu.ph', True),
            ('faculty', 'faculty@udm.edu.ph', True),
            ('student', 'student@gmail.com', False),
            ('faculty', 'faculty@gmail.com', False),
        ]:
            with self.subTest(role=role, email=email):
                form = SignUpForm(data={'role': role, 'email': email})
                form.is_valid()
                self.assertEqual('email' not in form.errors, valid)
        for role in ('admin', 'med_personnel'):
            form = SignUpForm(data={'role': role, 'email': 'worker@gmail.com'})
            self.assertFalse(form.is_valid())
            self.assertIn('role', form.errors)

    def test_staff_can_only_log_in_after_email_verification_and_head_admin_approval(self):
        user = self.signup_account('staff', 'staff-applicant@gmail.com')
    def test_staff_signup_waits_for_head_admin_approval(self):
        session = self.client.session
        session['privacy_viewed'] = True
        session.save()
    def test_staff_can_log_in_as_regular_user_after_signup_otp(self):
        with patch('accounts.views.SignUpForm') as form_class, \
             patch('accounts.views.is_ratelimited', return_value=False), \
             patch('accounts.views.send_mail'), \
             patch('accounts.views._render_signup', return_value=HttpResponse('signup')):
            form_class.return_value.is_valid.return_value = True
            form_class.return_value.cleaned_data = {
                'first_name': 'Staff', 'last_name': 'Applicant', 'email': 'staff-applicant@gmail.com',
                'password': 'new-password', 'role': 'staff',
                'department': '', 'phone': '09123456789',
            }
            self.client.post(reverse('signup'))

        user = User.objects.get(email='staff-applicant@gmail.com')
        profile = UserProfile.objects.get(user=user)
        self.assertEqual(profile.role, 'staff')
        self.assertEqual(profile.approval_status, UserProfile.APPROVAL_PENDING)
        self.assertFalse(user.is_active)
        with patch('accounts.views.is_ratelimited', return_value=False):
            response = self.client.post(reverse('verify_otp'), {'otp': profile.otp})
        self.assertRedirects(response, reverse('login'), fetch_redirect_response=False)
        user.refresh_from_db()
        self.assertTrue(user.is_active)
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        profile.refresh_from_db()
        self.assertEqual(profile.approval_status, UserProfile.APPROVAL_PENDING)
        self.assertIn(f'fc_td_{user.pk}', self.client.cookies)

        with patch('accounts.views.is_ip_flagged', return_value=False), \
             patch('accounts.views.is_ratelimited', return_value=False), \
             patch('accounts.views.send_mail') as send_mail:
            response = self.client.post(reverse('login'), {
                'email': user.email, 'password': 'Campus!Safe2048',
            })
        self.assertContains(response, 'Head Admin')
        self.assertNotIn('_auth_user_id', self.client.session)
        send_mail.assert_not_called()
        self.assertEqual(self.client.get(reverse('userDashboard')).status_code, 302)

        admin = User.objects.create_user(username='regular-admin', password='test', is_staff=True)
        admin_client = Client()
        admin_client.force_login(admin)
        response = admin_client.post(reverse('user_management_action', args=[user.pk]), {
            'action': 'approve',
        }, content_type='application/json')
        self.assertEqual(response.status_code, 403)
        profile.refresh_from_db()
        self.assertEqual(profile.approval_status, UserProfile.APPROVAL_PENDING)

        head = User.objects.create_user(username='head-admin', password='test', is_staff=True)
        UserProfile.objects.update_or_create(user=head, defaults={'role': 'admin', 'is_head_admin': True})
        head_client = Client()
        head_client.force_login(head)
        self.assertContains(head_client.get(reverse('user_management')), user.email)
        with patch('adm.user_management._notify') as notify:
            response = head_client.post(reverse('user_management_action', args=[user.pk]), {
                'action': 'approve',
            }, content_type='application/json')
        self.assertEqual(response.status_code, 200)
        notify.assert_called_once()
        profile.refresh_from_db()
        self.assertEqual(profile.approval_status, UserProfile.APPROVAL_APPROVED)
        with patch('accounts.views.is_ip_flagged', return_value=False), \
             patch('accounts.views.is_ratelimited', return_value=False):
            response = self.client.post(reverse('login'), {'email': user.email, 'password': 'Campus!Safe2048'})
        self.assertRedirects(response, reverse('userDashboard'), fetch_redirect_response=False)
        self.assertEqual(int(self.client.session['_auth_user_id']), user.pk)
        self.assertEqual(self.client.get(reverse('userDashboard')).status_code, 200)

    def test_students_and_faculty_do_not_need_staff_approval(self):
        for role in ('student', 'faculty'):
            with self.subTest(role=role):
                self.client = Client()
                user = self.signup_account(role, f'{role}@udm.edu.ph')
                profile = UserProfile.objects.get(user=user)
                self.assertEqual(profile.approval_status, UserProfile.APPROVAL_APPROVED)
                with patch('accounts.views.is_ratelimited', return_value=False):
                    self.client.post(reverse('verify_otp'), {'otp': profile.otp})
                with patch('accounts.views.is_ip_flagged', return_value=False), \
                     patch('accounts.views.is_ratelimited', return_value=False):
                    response = self.client.post(reverse('login'), {
                        'email': user.email, 'password': 'Campus!Safe2048',
                    })
                self.assertRedirects(response, reverse('userDashboard'), fetch_redirect_response=False)
                self.assertEqual(int(self.client.session['_auth_user_id']), user.pk)

    def test_existing_approved_staff_can_still_log_in(self):
        user = User.objects.create_user(username='existing@gmail.com', email='existing@gmail.com', password='test')
        UserProfile.objects.update_or_create(user=user, defaults={'role': 'staff'})
        with patch('accounts.views.is_ip_flagged', return_value=False), \
             patch('accounts.views.is_ratelimited', return_value=False), \
             patch('accounts.views._is_trusted_device', return_value=True):
            response = self.client.post(reverse('login'), {'email': user.email, 'password': 'test'})
        self.assertRedirects(response, reverse('userDashboard'), fetch_redirect_response=False)
        self.assertEqual(int(self.client.session['_auth_user_id']), user.pk)

    def test_approved_staff_can_log_in_with_a_fresh_email_code(self):
        user = User.objects.create_user(username='approved@gmail.com', email='approved@gmail.com', password='test')
        UserProfile.objects.update_or_create(user=user, defaults={'role': 'staff'})
        with patch('accounts.views.is_ip_flagged', return_value=False), \
             patch('accounts.views.is_ratelimited', return_value=False), \
             patch('accounts.views.send_mail'):
            response = self.client.post(reverse('login'), {'email': user.email, 'password': 'test'})
        self.assertRedirects(response, reverse('verify_login_otp'), fetch_redirect_response=False)
        self.assertNotIn('_auth_user_id', self.client.session)
        with patch('accounts.views.is_ratelimited', return_value=False):
            response = self.client.post(reverse('verify_login_otp'), {
                'otp': UserProfile.objects.get(user=user).otp,
            })
        self.assertRedirects(response, reverse('userDashboard'), fetch_redirect_response=False)
        self.assertEqual(int(self.client.session['_auth_user_id']), user.pk)

    def test_head_admin_cannot_approve_staff_before_email_verification(self):
        user = self.signup_account('staff', 'unverified@gmail.com')
        head = User.objects.create_user(username='head-admin', password='test', is_staff=True)
        UserProfile.objects.update_or_create(user=head, defaults={'role': 'admin', 'is_head_admin': True})
        head_client = Client()
        head_client.force_login(head)
        response = head_client.post(reverse('user_management_action', args=[user.pk]), {
            'action': 'approve',
        }, content_type='application/json')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(UserProfile.objects.get(user=user).approval_status, UserProfile.APPROVAL_PENDING)
        user.refresh_from_db()
        self.assertFalse(user.is_active)

    def test_pending_staff_cannot_use_stale_login_code_or_resend_session(self):
        user = User.objects.create_user(username='pending@gmail.com', is_active=True)
        UserProfile.objects.update_or_create(user=user, defaults={
            'role': 'staff', 'approval_status': UserProfile.APPROVAL_PENDING,
            'otp': '123456', 'otp_created_at': timezone.now(),
        })
        for route in ('verify_login_otp', 'resend_login_otp'):
            with self.subTest(route=route):
                session = self.client.session
                session['pending_login_user_id'] = user.pk
                session['pending_login_backend'] = 'django.contrib.auth.backends.ModelBackend'
                session.save()
                with patch('accounts.views.is_ratelimited', return_value=False), \
                     patch('accounts.views.send_mail') as send_mail:
                    response = self.client.post(reverse(route), {'otp': '123456'})
                self.assertRedirects(response, reverse('login'), fetch_redirect_response=False)
                self.assertNotIn('_auth_user_id', self.client.session)
                self.assertNotIn('pending_login_user_id', self.client.session)
                self.assertNotIn('pending_login_backend', self.client.session)
                send_mail.assert_not_called()

    def test_passkey_login_waits_for_staff_approval(self):
        user = User.objects.create_user(username='passkey@gmail.com', is_active=True)
        UserProfile.objects.update_or_create(user=user, defaults={
            'role': 'staff', 'approval_status': UserProfile.APPROVAL_PENDING,
        })
        passkey = Passkey.objects.create(
            user=user, name='Test device', credential_id='staff-credential', public_key=b'test-key',
        )
        with patch('accounts.passkeys.is_ip_flagged', return_value=False), \
             patch('accounts.passkeys.is_ratelimited', return_value=False), \
             patch('accounts.passkeys._pop_challenge', return_value=b'test-challenge'), \
             patch('accounts.passkeys.verify_authentication_response', return_value=SimpleNamespace(new_sign_count=1)):
            response = self.client.post(reverse('passkey_login_verify'), {
                'credential': {'rawId': passkey.credential_id, 'response': {}},
            }, content_type='application/json')
            self.assertEqual(response.status_code, 403)
            self.assertIn('Head Admin', response.json()['error'])
            self.assertNotIn('_auth_user_id', self.client.session)
            passkey.refresh_from_db()
            self.assertIsNone(passkey.last_used_at)

            UserProfile.objects.filter(user=user).update(approval_status=UserProfile.APPROVAL_APPROVED)
            response = self.client.post(reverse('passkey_login_verify'), {
                'credential': {'rawId': passkey.credential_id, 'response': {}},
            }, content_type='application/json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['redirect'], reverse('userDashboard'))
        self.assertEqual(int(self.client.session['_auth_user_id']), user.pk)

    def test_staff_signup_reuse_and_email_failure_cleanup(self):
        for reuse, mail_fails in [(False, False), (False, True), (True, True)]:
            with self.subTest(reuse=reuse, mail_fails=mail_fails):
                email = f'staff-{reuse}-{mail_fails}@gmail.com'
                existing = None
                if reuse:
                    existing = User.objects.create_user(
                        username=email, email=email, password='old-password', is_active=False,
                    )
                    UserProfile.objects.update_or_create(user=existing, defaults={'role': 'staff'})

                with patch('accounts.views.SignUpForm') as form_class, \
                     patch('accounts.views.is_ratelimited', return_value=False), \
                     patch('accounts.views.send_mail', side_effect=SMTPException('offline') if mail_fails else None), \
                     patch('accounts.views._render_signup', return_value=HttpResponse('signup')):
                    form_class.return_value.is_valid.return_value = True
                    form_class.return_value.cleaned_data = {
                        'first_name': 'Test', 'last_name': 'Staff', 'email': email,
                        'password': 'new-password', 'role': 'staff',
                        'department': 'Facilities', 'phone': '09123456789',
                    }
                    response = self.client.post(reverse('signup'))

                saved = User.objects.filter(email=email).first()
                if mail_fails and not reuse:
                    self.assertIsNone(saved)
                else:
                    self.assertIsNotNone(saved)
                    self.assertEqual(UserProfile.objects.get(user=saved).approval_status, UserProfile.APPROVAL_PENDING)
                    if reuse:
                        self.assertEqual(saved.pk, existing.pk)
                    else:
                        self.assertRedirects(response, reverse('verify_otp'), fetch_redirect_response=False)


class SignupConsentTests(TestCase):
    # The agreement is a normal required checkbox on step 1 of signup (details),
    # not something locked behind opening the Privacy Policy page.

    def test_agree_to_terms_is_required(self):
        form = SignUpForm(data={'role': 'student', 'email': 'student@udm.edu.ph'})
        form.is_valid()
        self.assertIn('agree_to_terms', form.errors)

    def test_terms_checkbox_is_enabled_and_on_the_details_step(self):
        import re

        response = self.client.get(reverse('signup'))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()

        details = html.index('id="signup-step-details"')
        access = html.index('id="signup-step-access"')
        checkbox = html.index('id="id_agree_to_terms"')
        self.assertTrue(details < checkbox < access)

        tag = html[html.rindex('<input', 0, checkbox):html.index('>', checkbox)]
        self.assertIsNone(re.search(r'\sdisabled(\s|=|>|/)', tag))

    def test_privacy_policy_page_is_read_only(self):
        response = self.client.get(reverse('privacy_policy'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'PART I. TERMS OF SERVICE')
        self.assertNotContains(response, 'agree_to_terms')
        self.assertNotIn('privacy_viewed', self.client.session)
