from django.shortcuts import render, redirect 
from .forms import ContactForm, LoginForm, SignUpForm, OTPVerificationForm, ForgotPasswordForm, ResetPasswordForm, is_unverified_signup
from django.contrib.auth import authenticate, login, update_session_auth_hash
from django.contrib.auth.models import User 
from django.contrib import messages
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST
from django.contrib.messages import get_messages
from axes.utils import reset_request
from django.core.exceptions import PermissionDenied
from django_ratelimit.core import is_ratelimited
from security.models import HoneypotHit
from security.audit import log_event, LOGIN_PASSWORD_OTP, LOGIN_TRUSTED_DEVICE
from user.models import UserProfile, invalidate_trusted_devices
from user.notifications import notify_staff_approval
import secrets
import string
from django.utils.crypto import constant_time_compare
from django.db.models import F
from django.core import signing
from datetime import timedelta
from django.core.mail import EmailMessage, send_mail
from email.utils import formataddr
from django.http import JsonResponse
from django.conf import settings 
from django.utils import timezone
from django.db import transaction
from smtplib import SMTPException
from django.utils.http import url_has_allowed_host_and_scheme #helps with redirecting users together with the next parameter.


def contact(request):
    context = {'contact_email': settings.CONTACT_EMAIL}
    if request.method != 'POST':
        return render(request, 'accounts/contact.html', context)

    if is_ratelimited(request, group='contact-form', key='ip', rate='5/h',
                      method='POST', increment=True):
        result = {'ok': False, 'message': 'Too many messages. Please try again later.'}
        status = 429
    else:
        form = ContactForm(request.POST)
        if not form.is_valid():
            result = {'ok': False, 'message': 'Please check the form and try again.',
                      'errors': {key: errors[0] for key, errors in form.errors.items()}}
            status = 400
        else:
            data = form.cleaned_data
            # Collapse whitespace/newlines so the name is safe to use in email headers.
            sender_name = ' '.join(data['name'].split())[:60]
            sent_at = timezone.localtime().strftime('%b %d, %Y at %I:%M %p')
            # Details first, then the message at the end.
            body = (f"New message from the Fac-Check contact form\n"
                    f"--------------------------------\n"
                    f"Name:  {sender_name}\n"
                    f"Email: {data['email']}\n"
                    f"Phone: {data['phone'] or 'Not provided'}\n"
                    f"Sent:  {sent_at}\n"
                    f"--------------------------------\n\n"
                    f"Message:\n{data['message']}\n\n"
                    f"Hit Reply to answer {sender_name} directly.")
            try:
                # Gmail always sends as the logged-in account (noreply), so the visitor's name goes
                # in the display name; Reply-To sends your answer straight to them.
                EmailMessage(
                    subject=f"Fac-Check Contact: New message from {sender_name}", body=body,
                    from_email=formataddr((f"{sender_name} via Fac-Check", settings.EMAIL_HOST_USER)),
                    to=[settings.CONTACT_EMAIL], reply_to=[formataddr((sender_name, data['email']))],
                ).send(fail_silently=False)
            except (OSError, SMTPException):
                result = {'ok': False, 'message': 'Your message could not be sent. Please try again later.'}
                status = 503
            else:
                result = {'ok': True, 'message': 'Your message was sent successfully.'}
                status = 200

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return JsonResponse(result, status=status)
    if result['ok']:
        messages.success(request, result['message'])
        return redirect('contact')
    context['form_errors'] = result.get('errors', {})
    context['form_error'] = result['message']
    context['submitted'] = request.POST
    return render(request, 'accounts/contact.html', context, status=status)

def _clear_messages(request):
    storage = get_messages(request)
    storage.used = True


def _generate_otp():
    return ''.join(secrets.choice(string.digits) for _ in range(6))


# Every resend issues a fresh code and resets the wrong-guess counter, so resends are
# capped per browser address AND per account (an attacker can switch addresses, but not accounts).
RESEND_RATE_PER_IP = '2/m'
RESEND_RATE_PER_ACCOUNT = '5/h'

def _resend_limited(request, purpose, user_id):
    over_ip = is_ratelimited(request, group=f'otp-resend-ip:{purpose}', key='ip',
                             rate=RESEND_RATE_PER_IP, method='POST', increment=True)
    over_account = is_ratelimited(request, group=f'otp-resend-account:{purpose}',
                                  key=lambda group, req: str(user_id),
                                  rate=RESEND_RATE_PER_ACCOUNT, method='POST', increment=True)
    return over_ip or over_account


RESET_VERIFIED_WINDOW_SECONDS = 10 * 60
OTP_EXPIRY_MINUTES = 5
OTP_MAX_ATTEMPTS = 5

# Trusted devices: after a successful OTP, the browser gets a signed cookie
# so MFA is skipped on that device until the window runs out.
ADMIN_MFA_WINDOW = timedelta(days=7)
USER_MFA_WINDOW = timedelta(days=30)
TRUSTED_DEVICE_SALT = 'accounts.trusted_device'

def _is_admin(user):
    return user.is_staff or user.is_superuser

def _mfa_window(user):
    return ADMIN_MFA_WINDOW if _is_admin(user) else USER_MFA_WINDOW

def _trusted_cookie_name(user):
    # One cookie per account, so shared computers don't mix users up
    return f'fc_td_{user.pk}'

def _credentials_version(user):
    # Goes up only on a real password change (invalidate_trusted_devices).
    # Not tied to the password hash, because Django rewrites the hash on login
    # whenever a server with a different Django version checks the password.
    version = UserProfile.objects.filter(user=user).values_list('credentials_version', flat=True).first()
    return version or 0

def _trust_device(response, request, user):
    value = signing.dumps({'u': user.pk, 'v': _credentials_version(user)}, salt=TRUSTED_DEVICE_SALT)
    response.set_cookie(
        _trusted_cookie_name(user),
        value,
        max_age=int(_mfa_window(user).total_seconds()),
        httponly=True,
        secure=request.is_secure(),
        samesite='Lax',
    )
    return response

def _is_trusted_device(request, user):
    raw = request.COOKIES.get(_trusted_cookie_name(user))
    if not raw:
        return False
    try:
        # max_age is checked against the role's window at login time,
        # so a user promoted to admin drops to the 7-day rule right away
        data = signing.loads(raw, salt=TRUSTED_DEVICE_SALT, max_age=_mfa_window(user))
    except signing.BadSignature:  # also covers SignatureExpired
        return False
    return data.get('u') == user.pk and data.get('v') == _credentials_version(user)

def _post_login_redirect(request, user):
    next_url = request.session.pop('pending_login_next', '')
    if url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return redirect(next_url)
    if _is_admin(user):
        return redirect('/adm/dashboard/')
    return redirect('/user/user_dashboard/')

def _otp_countdown_context(user):
    profile = UserProfile.objects.filter(user=user).first()
    remaining = 0
    if profile and profile.otp and profile.otp_created_at:
        expires_at = profile.otp_created_at + timedelta(minutes=OTP_EXPIRY_MINUTES)
        remaining = max(0, int((expires_at - timezone.now()).total_seconds() * 1000))
    return {'otp_remaining_ms': remaining}


def _check_otp(profile, submitted_otp, request=None, purpose=''):
    """
    Check a submitted code against the user's current OTP.
    Returns (status, attempts_left) where status is 'ok', 'invalid', 'locked' or 'expired'.
    Each code allows OTP_MAX_ATTEMPTS guesses, then it's locked until a new one is sent.
    """
    if profile.otp_attempts >= OTP_MAX_ATTEMPTS:
        return 'locked', 0

    if not profile.otp or not profile.otp_created_at:
        return 'expired', 0
    if (timezone.now() - profile.otp_created_at).total_seconds() >= OTP_EXPIRY_MINUTES * 60:
        return 'expired', 0

    # Reserve an attempt atomically so parallel requests can't sneak past the limit
    reserved = UserProfile.objects.filter(
        pk=profile.pk, otp_attempts__lt=OTP_MAX_ATTEMPTS
    ).update(otp_attempts=F('otp_attempts') + 1)
    if not reserved:
        return 'locked', 0

    if constant_time_compare(submitted_otp, profile.otp):
        # One-time use: burn the code so it can't be replayed
        profile.otp = ''
        profile.otp_attempts = 0
        profile.save(update_fields=['otp', 'otp_attempts'])
        return 'ok', 0

    profile.refresh_from_db(fields=['otp_attempts'])
    attempts_left = OTP_MAX_ATTEMPTS - profile.otp_attempts
    if attempts_left <= 0:
        # Logged once, on the guess that used up the last attempt
        log_event(request, 'otp_locked', user=profile.user, purpose=purpose)
        return 'locked', 0
    return 'invalid', attempts_left

def _otp_error(request, status, attempts_left):
    _clear_messages(request)
    if status == 'expired':
        messages.error(request, "Your code has expired. Please request a new one.")
    elif status == 'locked':
        messages.error(request, "Too many wrong attempts. This code is now locked. Please request a new one.")
    else:
        plural = '' if attempts_left == 1 else 's'
        messages.error(request, f"Invalid verification code. You have {attempts_left} attempt{plural} left.")

RESET_DECOY_SESSION_KEY = 'pending_reset_decoy'

def _clear_reset_session(request):
    for key in ('pending_reset_user_id', 'password_reset_verified_user_id',
                'password_reset_verified_at', 'password_reset_verified', RESET_DECOY_SESSION_KEY):
        request.session.pop(key, None)

def _start_reset_decoy(request, email):
    """Forgot Password for an email with no usable account. The visitor goes through the same
    screens as a real reset, so the page never reveals which emails are registered.
    No code is sent, and no code will ever be accepted."""
    _clear_reset_session(request)
    request.session[RESET_DECOY_SESSION_KEY] = {
        'email': email, 'sent_at': timezone.now().timestamp(), 'attempts': 0,
    }

def _reset_decoy_context(decoy):
    remaining = decoy['sent_at'] + OTP_EXPIRY_MINUTES * 60 - timezone.now().timestamp()
    return {'user_email': decoy['email'], 'otp_remaining_ms': max(0, int(remaining * 1000))}

def _verify_reset_decoy(request, decoy):
    """Mirrors verify_reset_otp's responses (attempt counts, expiry, lockout) for the decoy flow."""
    context = _reset_decoy_context(decoy)
    if request.method == 'POST':
        if is_ratelimited(request, key='ip', rate='5/m', method='POST', increment=True, fn=verify_reset_otp):
            messages.error(request, "Too many verification attempts. Please try again later")
            return render(request, 'accounts/verify_reset_otp.html', {'form': OTPVerificationForm(), **context})

        form = OTPVerificationForm(request.POST)
        if form.is_valid():
            if decoy['attempts'] >= OTP_MAX_ATTEMPTS:
                status, attempts_left = 'locked', 0
            elif context['otp_remaining_ms'] == 0:
                status, attempts_left = 'expired', 0
            else:
                decoy['attempts'] += 1
                request.session[RESET_DECOY_SESSION_KEY] = decoy
                attempts_left = OTP_MAX_ATTEMPTS - decoy['attempts']
                status = 'invalid' if attempts_left > 0 else 'locked'
            _otp_error(request, status, max(attempts_left, 0))
        else:
            _clear_messages(request)
            messages.error(request, "Please enter a valid 6-digit code")
    else:
        form = OTPVerificationForm()
    return render(request, 'accounts/verify_reset_otp.html', {'form': form, **context})

def _log_honeypot(request):
    HoneypotHit.objects.create(
        trigger_type='form_field',
        path=request.path,
        ip_address=request.META.get('REMOTE_ADDR'),
        user_agent=request.META.get('HTTP_USER_AGENT', ''),
        extra_info=f"Bot filled honeypot field with: {request.POST.get('website')}"
    )

def custom_lockout(request, credentials=None, *args, **kwargs):
    _clear_messages(request)
    messages.error(request, "Access denied. Your account has been temporarily locked due to 5 failed login attempts. Please try again after 30 minutes")
    return redirect('login')  


   
@never_cache
def splash_screen(request):
    return render(request, 'accounts/index.html')

def _approval_block_message(user):
    """Message for a verified account that may not log in yet, or None."""
    profile = getattr(user, 'profile', None)
    if profile and profile.approval_status == UserProfile.APPROVAL_PENDING:
        return ("Your account is waiting for the Head Admin's approval. "
                "You'll receive an email once it's approved.")
    return None

def _inactive_account_message(email, password):
    """Django won't authenticate inactive accounts, so explain why - but only when the
    password is correct, so this can't be used to discover which emails exist."""
    user = User.objects.filter(username=email, is_active=False).select_related('profile').first()
    if not user or not user.check_password(password):
        return None
    profile = getattr(user, 'profile', None)
    if profile and profile.deactivated_at:
        return "This account has been deactivated. Please contact the administrator."
    return "Please verify your email before logging in"

@never_cache
def login_view(request):
    # remembers the selected page before directing to login
    if request.method == 'GET':
        next_url = request.GET.get('next', '')

        if url_has_allowed_host_and_scheme(
            next_url,
            allowed_hosts={request.get_host()}, #only allows redirects to its host 
            require_https=request.is_secure(),
        ):
            request.session['pending_login_next'] = next_url
        else:
            request.session.pop('pending_login_next', None)

    if request.method == 'POST':
       # Honeypot hits are still logged, but no longer block sign-in for the whole IP:
       # on campus Wi-Fi one address is shared by everyone, so one curious visitor
       # would lock every student out. Axes, rate limits and the email code protect login.
       if request.POST.get('website'):
           _log_honeypot(request)
           _clear_messages(request)
           messages.error(request, "Invalid email or password")
           return render(request, 'accounts/login.html', {'form': LoginForm()})

       if is_ratelimited(request, key='ip', rate='5/m', method='POST', increment=True, fn=login_view):
           messages.error(request, "Too many login attempts. Please try again later")
           return render(request, 'accounts/login.html', {'form': LoginForm()})

       form = LoginForm(request.POST)
       if form.is_valid():
           email = form.cleaned_data.get('email')
           password = form.cleaned_data.get('password')

           try:
               user = authenticate(request, username=email, password=password)

               if user is not None:
                   if not user.is_active:
                       _clear_messages(request)
                       messages.error(request, "Please verify your email before logging in")
                       return render(request, 'accounts/login.html', {'form': LoginForm()})

                   blocked = _approval_block_message(user)
                   if blocked:
                       _clear_messages(request)
                       messages.error(request, blocked)
                       return render(request, 'accounts/login.html', {'form': LoginForm()})

                   reset_request(request)

                   if _is_trusted_device(request, user):
                       request.audit_login_method = LOGIN_TRUSTED_DEVICE
                       login(request, user)
                       _clear_messages(request)
                       return _post_login_redirect(request, user)

                   request.session['pending_login_backend'] = user.backend

                   
                   otp = _generate_otp()
                   UserProfile.objects.update_or_create(
                       user=user,
                       defaults={
                           'otp': otp,
                           'otp_created_at': timezone.now(),
                           'otp_attempts': 0
                       }
                   )

                   try:
                       send_mail(
                           subject='Your Fac-Check Login Code',
                           message=f'Your login verification code is: {otp}\n This code will expire in {OTP_EXPIRY_MINUTES} minutes. Please do not share this code with anyone.',
                           from_email=settings.DEFAULT_FROM_EMAIL,
                           recipient_list=[user.email],
                           fail_silently=False,
                       )
                   except (SMTPException, OSError):
                       _clear_messages(request)
                       messages.error(request, "We couldn't send your login code right now. Please try again in a moment.")
                       return render(request, 'accounts/login.html', {'form': LoginForm()})

                   request.session['pending_login_user_id'] = user.id
                   _clear_messages(request)
                   return redirect('verify_login_otp')

               else:
                   _clear_messages(request)
                   messages.error(request, _inactive_account_message(email, password) or "Invalid email or password")

           except PermissionDenied:
               _clear_messages(request)
               messages.error(request, "Access denied. Your account has been temporarily locked due to 5 failed login attempts. Please try again after 30 minutes")

           except Exception as e:
               _clear_messages(request)
               messages.error(request, "An authentication gateway failure occured. Please contact system administrator")
       else:
           _clear_messages(request)
           messages.error(request, "Please fill up all required fields correctly")
    else:
        form = LoginForm()

    return render(request, 'accounts/login.html', {'form': form})

@never_cache
def verify_login_otp(request):
    user_id = request.session.get('pending_login_user_id')

    if not user_id:
        messages.error(request, "No pending login found. Please log in again.")
        return redirect('login')

    try:
        user = User.objects.get(id=user_id, is_active=True)
    except User.DoesNotExist:
        messages.error(request, "Invalid login session. Please log in again.")
        return redirect('login')

    blocked = _approval_block_message(user)
    if blocked:
        request.session.pop('pending_login_user_id', None)
        request.session.pop('pending_login_backend', None)
        messages.error(request, blocked)
        return redirect('login')

    if request.method == 'POST':

       if is_ratelimited(request, key='ip', rate='5/m', method='POST', increment=True, fn=verify_login_otp):
           messages.error(request, "Too many verification attempts. Please try again later")
           return render(request, 'accounts/verify_login.html', {'form': OTPVerificationForm(), 'user_email': user.email, **_otp_countdown_context(user)})

       form = OTPVerificationForm(request.POST)
       if form.is_valid():
           submitted_otp = form.cleaned_data['otp']

           try:
               profile = UserProfile.objects.get(user=user)
           except UserProfile.DoesNotExist:
                _clear_messages(request)
                messages.error(request, "Verification failed. Please log in again.")
                return redirect('login')

           status, attempts_left = _check_otp(profile, submitted_otp, request, 'login')

           if status != 'ok':
               _otp_error(request, status, attempts_left)
           else:
                del request.session['pending_login_user_id']

                user.backend = request.session.pop('pending_login_backend', 'django.contrib.auth.backends.ModelBackend')
                request.audit_login_method = LOGIN_PASSWORD_OTP
                login(request, user)

                return _trust_device(_post_login_redirect(request, user), request, user)
       else:
            _clear_messages(request)
            messages.error(request, "Please enter a valid 6-digit code")
    else:
        form = OTPVerificationForm()

    return render(request, 'accounts/verify_login.html', {'form': form, 'user_email': user.email, **_otp_countdown_context(user)})


@never_cache
@require_POST
def resend_login_otp(request):
    user_id = request.session.get('pending_login_user_id')

    if not user_id:
        messages.error(request, "No pending login found. Please log in again.")
        return redirect('login')

    if _resend_limited(request, 'login', user_id):
        messages.error(request, "Please wait before requesting another code")
        return redirect('verify_login_otp')

    try:
        user = User.objects.get(id=user_id, is_active=True)
    except User.DoesNotExist:
        messages.error(request, "Invalid login session. Please log in again.")
        return redirect('login')

    blocked = _approval_block_message(user)
    if blocked:
        request.session.pop('pending_login_user_id', None)
        request.session.pop('pending_login_backend', None)
        messages.error(request, blocked)
        return redirect('login')

    otp = _generate_otp()
    UserProfile.objects.update_or_create(
        user=user,
        defaults={
            'otp': otp,
            'otp_created_at': timezone.now(),
            'otp_attempts': 0
        }
    )

    try:
        send_mail(
            subject='Your Fac-Check Login Code',
            message=f'Your login verification code is: {otp}\n This code will expire in {OTP_EXPIRY_MINUTES} minutes. Please do not share this code with anyone.',
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user.email],
            fail_silently=False,
        )
    except (SMTPException, OSError):
        messages.error(request, "We couldn't resend your code right now. Please try again in a moment.")
        return redirect('verify_login_otp')

    messages.success(request, "A new login code has been sent to your email.")
    return redirect('verify_login_otp')

def _render_signup(request, form):
    return render(request, 'accounts/signup.html', {'form': form})

@never_cache
def signup(request):
    if request.method == 'POST':
       if request.POST.get('website'):
           _log_honeypot(request)
           _clear_messages(request)
           messages.error(request, "Unable to process your request. Please contact system administrator")

           return _render_signup(request, SignUpForm())

       if is_ratelimited(request, key='ip', rate='5/m', method='POST', increment=True, fn=signup):
           messages.error(request, "Too many signup attempts. Please try again later")
           return _render_signup(request, SignUpForm())

       form = SignUpForm(request.POST)

       if form.is_valid():
            first_name = form.cleaned_data['first_name']
            last_name = form.cleaned_data['last_name']
            email = form.cleaned_data['email']
            password = form.cleaned_data['password']
            role = form.cleaned_data['role']
            department = form.cleaned_data['department']
            phone = form.cleaned_data['phone']

            with transaction.atomic():
                user = User.objects.filter(email__iexact=email).select_related('profile').first()
                reused_signup = bool(user and is_unverified_signup(user))
                if reused_signup:
                    user.first_name = first_name
                    user.last_name = last_name
                    user.set_password(password)
                    user.save()
                else:
                    user = User.objects.create_user(
                        username=email,
                        email=email,
                        password=password,
                        first_name=first_name,
                        last_name=last_name,
                        is_active=False
                    )

                otp = _generate_otp()
                UserProfile.objects.update_or_create(
                    user=user,
                    defaults={
                        'role': role,
                        'approval_status': (
                            UserProfile.APPROVAL_PENDING if role == 'staff'
                            else UserProfile.APPROVAL_APPROVED
                        ),
                        'department': department,
                        'phone': phone,
                        'otp': otp,
                        'otp_created_at': timezone.now(),
                        'otp_attempts': 0
                    }
                )

            try:
                send_mail(
                    subject='Your OTP for Account Verification',
                    message=f'Your verification code is: {otp}\n This code will expire in {OTP_EXPIRY_MINUTES} minutes. Please do not share this code with anyone.',
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    recipient_list=[email],
                    fail_silently=False,
                )
            except (SMTPException, OSError):
                if not reused_signup:
                    user.delete()
                _clear_messages(request)
                messages.error(request, "We couldn't send your verification email right now. Please try again in a moment.")
                return _render_signup(request, form)

            request.session['pending_verification_user_id'] = user.id
            _clear_messages(request)
            messages.success(request, "Account created successfully! Please check your email for the verification code to verify your account.")
            return redirect('verify_otp')
       else:
           _clear_messages(request)
    else:
        form = SignUpForm()

    return _render_signup(request, form)

@never_cache
def verify_otp(request):
    user_id = request.session.get('pending_verification_user_id')


    if not user_id:
        messages.error(request, "No pending verification found. Please sign up first.")
        return redirect('signup')

    try:
        user = User.objects.get(id=user_id, is_active=False)
    except User.DoesNotExist:
        messages.error(request, "Invalid verification session. Please sign up again.")
        return redirect('signup')

    if request.method == 'POST':
        if is_ratelimited(request, key='ip', rate='5/m', method='POST', increment=True, fn=verify_otp):
            messages.error(request, "Too many verification attempts. Please try again later")
            return render(request, 'accounts/verify_otp.html', {'form': OTPVerificationForm(), 'user_email': user.email, **_otp_countdown_context(user)})

        form = OTPVerificationForm(request.POST)
        if form.is_valid():
            submitted_otp = form.cleaned_data['otp']

            try:
                profile = UserProfile.objects.get(user=user)
            except UserProfile.DoesNotExist:
                _clear_messages(request)
                messages.error(request, "Verification failed. Please sign up again.")
                return redirect('signup')

            status, attempts_left = _check_otp(profile, submitted_otp, request, 'signup verification')

            if status != 'ok':
                _otp_error(request, status, attempts_left)
            else:
                with transaction.atomic():
                    user.is_active = True
                    user.save()
                    notify_staff_approval(user)
                del request.session['pending_verification_user_id']
                _clear_messages(request)
                if getattr(user, 'profile', None) and user.profile.approval_status == UserProfile.APPROVAL_PENDING:
                    messages.success(request, "Your email has been verified! Your account is now waiting for the "
                                              "Head Admin's approval. You'll receive an email once it's approved.")
                else:
                    messages.success(request, "Your account has been verified successfully! You can now log in.")
                # The device that verified the signup is trusted, so the first login skips MFA
                return _trust_device(redirect('login'), request, user)
        else:
            _clear_messages(request)
            messages.error(request, "Please enter a valid 6-digit code")
    else:
        form = OTPVerificationForm()

    return render(request, 'accounts/verify_otp.html', {'form': form, 'user_email': user.email, **_otp_countdown_context(user)})

@never_cache
@require_POST
def resend_otp(request):
    user_id = request.session.get('pending_verification_user_id')

    if not user_id:
        messages.error(request, "No pending verification found. Please sign up first.")
        return redirect('signup')

    if _resend_limited(request, 'signup', user_id):
        messages.error(request, "Please wait before requesting another code")
        return redirect('verify_otp')

    try:
        user = User.objects.get(id=user_id, is_active=False)
    except User.DoesNotExist:
        messages.error(request, "Invalid verification session. Please sign up again.")
        return redirect('signup')

    otp = _generate_otp()
    UserProfile.objects.update_or_create(
        user=user, 
        defaults={
            'otp': otp, 
            'otp_created_at': timezone.now(),
            'otp_attempts': 0
        }
    )

    try:
        send_mail(
            subject='Your OTP for Account Verification',
            message=f'Your verification code is: {otp}\n This code will expire in {OTP_EXPIRY_MINUTES} minutes. Please do not share this code with anyone.',
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user.email],
            fail_silently=False,
        )
    except (SMTPException, OSError):
        messages.error(request, "We couldn't resend your code right now. Please try again in a moment.")
        return redirect('verify_otp')

    messages.success(request, "A new verification code has been sent to your email.")
    return redirect('verify_otp')

@never_cache
def forgot_password(request):
    if request.method == 'POST':
        form = ForgotPasswordForm(request.POST)
        if request.POST.get('website'):
            _log_honeypot(request)
            _clear_messages(request)
            messages.error(request, "Unable to process your request. Please contact system administrator")
            return render(request, 'accounts/forgot_password.html', {'form': form})

        if is_ratelimited(request, key='ip', rate='5/m', method='POST', increment=True, fn=forgot_password):
            messages.error(request, "Too many requests. Please try again later")
            return render(request, 'accounts/forgot_password.html', {'form': form})

        if form.is_valid():
            email = form.cleaned_data.get('email', '').strip().lower()

            # filter().first(): two accounts sharing an email used to crash this page (500)
            user = User.objects.filter(email__iexact=email).order_by('id').first()

            # Unknown or not-yet-verified email: same next screen as a real reset,
            # so this page can't be used to find out who has an account.
            if user is None or not user.is_active:
                _start_reset_decoy(request, email)
                _clear_messages(request)
                return redirect('verify_reset_otp')

            otp = _generate_otp()
            UserProfile.objects.update_or_create(
                user=user,
                defaults={
                    'otp': otp,
                    'otp_created_at': timezone.now(),
                    'otp_attempts': 0
                }
            )

            try:
                send_mail(
                    subject='Your Fac-Check Password Reset Code',
                    message=f'Your password reset code is: {otp}\n This code will expire in {OTP_EXPIRY_MINUTES} minutes. Please do not share this code with anyone.',
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    recipient_list=[user.email],
                    fail_silently=False,
                )
            except (SMTPException, OSError):
                _clear_messages(request)
                messages.error(request, "We couldn't send your reset code right now. Please try again in a moment.")
                return render(request, 'accounts/forgot_password.html', {'form': form})

            _clear_reset_session(request)
            request.session['pending_reset_user_id'] = user.id
            _clear_messages(request)
            return redirect('verify_reset_otp')
        else:
            _clear_messages(request)
            messages.error(request, "Please enter a valid email address")
    else:
        form = ForgotPasswordForm()

    return render(request, 'accounts/forgot_password.html', {'form': form})


@never_cache
def verify_reset_otp(request):
    user_id = request.session.get('pending_reset_user_id')

    if not user_id:
        decoy = request.session.get(RESET_DECOY_SESSION_KEY)
        if decoy:
            return _verify_reset_decoy(request, decoy)
        messages.error(request, "No pending password reset found. Please try again.")
        return redirect('forgot_password')

    try:
        user = User.objects.get(id=user_id, is_active=True)
    except User.DoesNotExist:
        messages.error(request, "Invalid reset session. Please try again.")
        return redirect('forgot_password')

    if request.method == 'POST':

        if is_ratelimited(request, key='ip', rate='5/m', method='POST', increment=True, fn=verify_reset_otp):
            messages.error(request, "Too many verification attempts. Please try again later")
            return render(request, 'accounts/verify_reset_otp.html', {'form': OTPVerificationForm(), 'user_email': user.email, **_otp_countdown_context(user)})

        form = OTPVerificationForm(request.POST)
        if form.is_valid():
            submitted_otp = form.cleaned_data['otp']

            try:
                profile = UserProfile.objects.get(user=user)
            except UserProfile.DoesNotExist:
                _clear_messages(request)
                messages.error(request, "Verification failed. Please try again.")
                return redirect('forgot_password')

            status, attempts_left = _check_otp(profile, submitted_otp, request, 'password reset')

            if status != 'ok':
                _otp_error(request, status, attempts_left)
            else:
                # Bind verification to THIS user, not just a True flag
                request.session['password_reset_verified_user_id'] = user.id
                request.session['password_reset_verified_at'] = timezone.now().timestamp()
                _clear_messages(request)
                return redirect('reset_password')
        else:
            _clear_messages(request)
            messages.error(request, "Please enter a valid 6-digit code")
    else:
        form = OTPVerificationForm()

    return render(request, 'accounts/verify_reset_otp.html', {'form': form, 'user_email': user.email, **_otp_countdown_context(user)})


@never_cache
@require_POST
def resend_reset_otp(request):
    user_id = request.session.get('pending_reset_user_id')

    if not user_id:
        decoy = request.session.get(RESET_DECOY_SESSION_KEY)
        if decoy:
            # Same limits and messages as a real resend, but nothing is sent
            if _resend_limited(request, 'reset', f"decoy:{decoy['email']}"):
                messages.error(request, "Please wait before requesting another code")
                return redirect('verify_reset_otp')
            decoy.update(sent_at=timezone.now().timestamp(), attempts=0)
            request.session[RESET_DECOY_SESSION_KEY] = decoy
            messages.success(request, "A new reset code has been sent to your email.")
            return redirect('verify_reset_otp')
        messages.error(request, "No pending password reset found. Please try again.")
        return redirect('forgot_password')

    if _resend_limited(request, 'reset', user_id):
        messages.error(request, "Please wait before requesting another code")
        return redirect('verify_reset_otp')

    try:
        user = User.objects.get(id=user_id, is_active=True)
    except User.DoesNotExist:
        messages.error(request, "Invalid reset session. Please try again.")
        return redirect('forgot_password')

    otp = _generate_otp()
    UserProfile.objects.update_or_create(
        user=user,
        defaults={
            'otp': otp,
            'otp_created_at': timezone.now(),
            'otp_attempts': 0
        }
    )

    try:
        send_mail(
            subject='Your Fac-Check Password Reset Code',
            message=f'Your password reset code is: {otp}\n This code will expire in {OTP_EXPIRY_MINUTES} minutes. Please do not share this code with anyone.',
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user.email],
            fail_silently=False,
        )
    except (SMTPException, OSError):
        messages.error(request, "We couldn't resend your code right now. Please try again in a moment.")
        return redirect('verify_reset_otp')

    messages.success(request, "A new reset code has been sent to your email.")
    return redirect('verify_reset_otp')


@never_cache
def reset_password(request):
    user_id = request.session.get('pending_reset_user_id')
    verified_user_id = request.session.get('password_reset_verified_user_id')
    verified_at = request.session.get('password_reset_verified_at')

    verification_valid = (
        user_id
        and verified_user_id == user_id
        and verified_at
        and timezone.now().timestamp() - verified_at <= RESET_VERIFIED_WINDOW_SECONDS
    )

    if not verification_valid:
        _clear_reset_session(request)
        messages.error(request, "Please verify your identity before resetting your password.")
        return redirect('forgot_password')

    try:
        user = User.objects.get(id=user_id, is_active=True)
    except User.DoesNotExist:
        messages.error(request, "Invalid reset session. Please try again.")
        return redirect('forgot_password')

    if request.method == 'POST':

        if is_ratelimited(request, key='ip', rate='5/m', method='POST', increment=True, fn=reset_password):
            messages.error(request, "Too many attempts. Please try again later")
            return render(request, 'accounts/reset_password.html', {'form': ResetPasswordForm()})

        if request.POST.get('website'):
            _log_honeypot(request)
            messages.error(request, "Unable to process your request. Please contact system administrator")
            return render(request, 'accounts/reset_password.html', {'form': ResetPasswordForm()})

        form = ResetPasswordForm(request.POST)
        # Same rule as Edit Profile: the new password can't be the current one
        if form.is_valid() and user.check_password(form.cleaned_data['new_password']):
            form.add_error('new_password', "New Password must be different from your current password.")

        if form.is_valid():
            new_password = form.cleaned_data['new_password']

            user.set_password(new_password)
            user.save()
            invalidate_trusted_devices(user)
            log_event(request, 'password_reset', user=user)

            try:
                profile = UserProfile.objects.get(user=user)
                profile.otp = ''
                # They just chose their own password, so an admin-created account
                # doesn't need the first-login password change anymore
                profile.must_change_password = False
                profile.save()
            except UserProfile.DoesNotExist:
                pass

            _clear_reset_session(request)

            _clear_messages(request)
            messages.success(request, "Your password has been reset successfully. You can now log in with your new password.")
            return redirect('login')
        else:
            # Each specific error is listed on the page, like the signup form
            _clear_messages(request)
    else:
        form = ResetPasswordForm()

    return render(request, 'accounts/reset_password.html', {'form': form})

def privacy_policy(request):
    # Read-only reference page. The agreement itself is the checkbox on the signup form.
    return render(request, 'accounts/privacypolicy.html')



@never_cache
def force_password_change(request):
    """First login of an account created by an admin: replace the emailed temporary password."""
    if not request.user.is_authenticated:
        return redirect('login')
    user = request.user
    profile = getattr(user, 'profile', None)
    if not profile or not profile.must_change_password:
        return _post_login_redirect(request, user)

    if request.method == 'POST':
        if request.POST.get('website'):
            _log_honeypot(request)
            messages.error(request, "Unable to process your request. Please contact system administrator")
            return render(request, 'accounts/force_password_change.html', {'form': ResetPasswordForm()})

        form = ResetPasswordForm(request.POST)
        # Same rule as Edit Profile: the new password can't be the temporary one
        if form.is_valid() and user.check_password(form.cleaned_data['new_password']):
            form.add_error('new_password', "New Password must be different from your current password.")
        if form.is_valid():
            user.set_password(form.cleaned_data['new_password'])
            user.save()
            profile.must_change_password = False
            profile.save(update_fields=['must_change_password'])
            invalidate_trusted_devices(user)
            update_session_auth_hash(request, user)  # stay logged in
            log_event(request, 'password_changed', user=user, where='first login (temporary password)')
            _clear_messages(request)
            messages.success(request, "Your password has been set. Welcome to Fac-Check!")
            return _post_login_redirect(request, user)
        _clear_messages(request)
    else:
        form = ResetPasswordForm()

    return render(request, 'accounts/force_password_change.html', {'form': form})
