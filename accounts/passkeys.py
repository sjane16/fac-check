"""
Passkey (WebAuthn) sign-in for every user and admin.

Flow for both adding and signing in is two steps:
  1. .../options/  -> server creates a one-time challenge, browser shows the
                      fingerprint / face / screen-lock prompt
  2. .../verify/   -> browser sends back the signed answer, server checks it

A passkey sign-in skips the email OTP: the passkey already proves both
"something you have" (the device) and "something you are/know" (biometric/PIN).
"""
import json
import ipaddress
import time

from django.conf import settings
from django.contrib.auth import login
from django.core.mail import send_mail
from django.db import IntegrityError
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST
from django_ratelimit.core import is_ratelimited

from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from security.audit import log_event, LOGIN_PASSKEY
from user.models import Passkey

RP_NAME = 'Fac-Check'
CHALLENGE_MAX_AGE_SECONDS = 5 * 60
MAX_PASSKEYS_PER_USER = 10
PASSKEY_BACKEND = 'django.contrib.auth.backends.ModelBackend'

REG_SESSION_KEY = 'passkey_registration'
AUTH_SESSION_KEY = 'passkey_authentication'


# ---------- helpers ----------

def _error(message, status=400):
    return JsonResponse({'ok': False, 'error': message}, status=status)

def _relying_party(request):
    """The site the passkey is bound to. Set WEBAUTHN_RP_ID / WEBAUTHN_ORIGIN in
    settings for production; otherwise it follows the address being used."""
    host = request.get_host().split(':')[0]
    rp_id = getattr(settings, 'WEBAUTHN_RP_ID', None) or host
    origin = getattr(settings, 'WEBAUTHN_ORIGIN', None) or f"{request.scheme}://{request.get_host()}"
    return rp_id, origin

def _host_supports_passkeys(rp_id):
    # Browsers refuse passkeys on raw IP addresses (127.0.0.1, 192.168.x.x)
    try:
        ipaddress.ip_address(rp_id)
        return False
    except ValueError:
        return True

def _ip_host_message(request):
    port = request.get_port()
    local = f"http://localhost:{port}" if port not in ("80", "443") else "http://localhost"
    return f"Passkeys don't work on an IP address. Open the site at {local} instead."

def _read_json(request):
    try:
        return json.loads(request.body or b'{}')
    except ValueError:
        return None

def _save_challenge(request, key, challenge):
    request.session[key] = {'challenge': bytes_to_base64url(challenge), 'at': time.time()}

def _pop_challenge(request, key):
    data = request.session.pop(key, None)
    if not data or time.time() - data.get('at', 0) > CHALLENGE_MAX_AGE_SECONDS:
        return None
    return base64url_to_bytes(data['challenge'])

def _notify(user, subject, message):
    # A heads-up so the owner notices if someone else changed their passkeys
    send_mail(
        subject=subject,
        message=message,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
        fail_silently=True,
    )

def _clean_name(raw, fallback):
    name = (raw or '').strip()[:60]
    return name or fallback


# ---------- adding a passkey (logged-in users) ----------

@never_cache
@require_POST
def register_options(request):
    if not request.user.is_authenticated:
        return _error("Please log in first.", status=401)

    rp_id, _ = _relying_party(request)
    if not _host_supports_passkeys(rp_id):
        return _error(_ip_host_message(request))

    user = request.user
    if user.passkeys.count() >= MAX_PASSKEYS_PER_USER:
        return _error(f"You can have up to {MAX_PASSKEYS_PER_USER} passkeys. Remove one first.")

    options = generate_registration_options(
        rp_id=rp_id,
        rp_name=RP_NAME,
        user_id=str(user.pk).encode(),
        user_name=user.email or user.username,
        user_display_name=user.get_full_name() or user.email or user.username,
        authenticator_selection=AuthenticatorSelectionCriteria(
            # Discoverable, so sign-in works without typing an email first
            resident_key=ResidentKeyRequirement.REQUIRED,
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
        # Don't let the same device register twice
        exclude_credentials=[
            PublicKeyCredentialDescriptor(id=base64url_to_bytes(pk.credential_id))
            for pk in user.passkeys.all()
        ],
    )
    _save_challenge(request, REG_SESSION_KEY, options.challenge)
    return JsonResponse(json.loads(options_to_json(options)))


@never_cache
@require_POST
def register_verify(request):
    if not request.user.is_authenticated:
        return _error("Please log in first.", status=401)

    body = _read_json(request)
    if not body or 'credential' not in body:
        return _error("Invalid request.")

    challenge = _pop_challenge(request, REG_SESSION_KEY)
    if not challenge:
        return _error("This request expired. Please try adding the passkey again.")

    rp_id, origin = _relying_party(request)
    try:
        verification = verify_registration_response(
            credential=body['credential'],
            expected_challenge=challenge,
            expected_rp_id=rp_id,
            expected_origin=origin,
            require_user_verification=True,
        )
    except Exception:
        return _error("We couldn't verify this passkey. Please try again.")

    user = request.user
    name = _clean_name(body.get('name'), f"Passkey {user.passkeys.count() + 1}")
    transports = body['credential'].get('response', {}).get('transports') or []

    try:
        passkey = Passkey.objects.create(
            user=user,
            name=name,
            credential_id=bytes_to_base64url(verification.credential_id),
            public_key=verification.credential_public_key,
            sign_count=verification.sign_count,
            transports=[t for t in transports if isinstance(t, str)][:10],
        )
    except IntegrityError:
        return _error("This passkey is already registered.")

    _notify(
        user,
        'A passkey was added to your Fac-Check account',
        f'A passkey named "{passkey.name}" was added to your account on '
        f'{timezone.localtime(passkey.created_at):%b %d, %Y %I:%M %p}.\n\n'
        'If this wasn\'t you, log in and remove it under Profile > Passkeys, then change your password.',
    )
    log_event(request, 'passkey_added', user=user, passkey=passkey.name, passkey_id=passkey.id)
    return JsonResponse({'ok': True, 'id': passkey.id, 'name': passkey.name})


# ---------- signing in with a passkey ----------

@never_cache
@require_POST
def login_options(request):
    rp_id, _ = _relying_party(request)
    if not _host_supports_passkeys(rp_id):
        return _error(_ip_host_message(request))

    options = generate_authentication_options(
        rp_id=rp_id,
        # Empty allow list: the browser offers whichever passkeys it has for this site
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    _save_challenge(request, AUTH_SESSION_KEY, options.challenge)
    return JsonResponse(json.loads(options_to_json(options)))


@never_cache
@require_POST
def login_verify(request):
    # Imported here to avoid a circular import with accounts.views
    from .views import _post_login_redirect

    if is_ratelimited(request, key='ip', rate='10/m', method='POST', increment=True, fn=login_verify):
        return _error("Too many sign-in attempts. Please try again later.", status=429)

    body = _read_json(request)
    credential = (body or {}).get('credential')
    if not credential or not credential.get('rawId'):
        return _error("Invalid request.")

    challenge = _pop_challenge(request, AUTH_SESSION_KEY)
    if not challenge:
        return _error("This sign-in request expired. Please try again.")

    passkey = Passkey.objects.select_related('user').filter(credential_id=credential['rawId']).first()
    if not passkey:
        return _error("This passkey isn't linked to any Fac-Check account (it may have been removed). "
                      "Log in with your email and password, then set up a new passkey.")

    # If the browser says which account this passkey belongs to, it must match
    user_handle = credential.get('response', {}).get('userHandle')
    if user_handle:
        try:
            if base64url_to_bytes(user_handle) != str(passkey.user_id).encode():
                return _error("Passkey sign-in failed.")
        except Exception:
            return _error("Passkey sign-in failed.")

    rp_id, origin = _relying_party(request)
    try:
        verification = verify_authentication_response(
            credential=credential,
            expected_challenge=challenge,
            expected_rp_id=rp_id,
            expected_origin=origin,
            credential_public_key=bytes(passkey.public_key),
            credential_current_sign_count=passkey.sign_count,
            require_user_verification=True,
        )
    except Exception:
        log_event(request, 'login_failed', user=passkey.user, method='passkey',
                  reason='passkey verification failed', passkey=passkey.name)
        return _error("Passkey sign-in failed. Please try again.")

    user = passkey.user
    if not user.is_active:
        log_event(request, 'login_failed', user=user, method='passkey', reason='account not active', passkey=passkey.name)
        return _error("This account is not active. Please contact the administrator.", status=403)
    profile = getattr(user, 'profile', None)
    if profile and profile.approval_status == profile.APPROVAL_PENDING:
        log_event(request, 'login_failed', user=user, method='passkey', reason='awaiting admin approval', passkey=passkey.name)
        return _error("Your account is waiting for the Head Admin's approval.", status=403)

    passkey.sign_count = verification.new_sign_count
    passkey.last_used_at = timezone.now()
    passkey.save(update_fields=['sign_count', 'last_used_at'])

    # Clear any half-finished password/OTP login in this browser
    for key in ('pending_login_user_id', 'pending_login_backend'):
        request.session.pop(key, None)

    request.audit_login_method = LOGIN_PASSKEY
    login(request, user, backend=PASSKEY_BACKEND)
    return JsonResponse({'ok': True, 'redirect': _post_login_redirect(request, user).url})


# ---------- managing your passkeys ----------

@never_cache
@require_POST
def rename(request, passkey_id):
    if not request.user.is_authenticated:
        return _error("Please log in first.", status=401)
    passkey = request.user.passkeys.filter(pk=passkey_id).first()
    if not passkey:
        return _error("Passkey not found.", status=404)

    body = _read_json(request) or {}
    name = (body.get('name') or '').strip()
    if not name:
        return _error("Please enter a name.")
    old_name = passkey.name
    passkey.name = name[:60]
    passkey.save(update_fields=['name'])
    log_event(request, 'passkey_renamed', user=request.user, passkey_id=passkey.id, old_name=old_name, new_name=passkey.name)
    return JsonResponse({'ok': True, 'name': passkey.name})


@never_cache
@require_POST
def remove(request, passkey_id):
    if not request.user.is_authenticated:
        return _error("Please log in first.", status=401)
    passkey = request.user.passkeys.filter(pk=passkey_id).first()
    if not passkey:
        return _error("Passkey not found.", status=404)

    name = passkey.name
    removed_id = passkey.id
    passkey.delete()
    log_event(request, 'passkey_removed', user=request.user, passkey=name, passkey_id=removed_id)
    _notify(
        request.user,
        'A passkey was removed from your Fac-Check account',
        f'The passkey "{name}" was removed from your account. It can no longer be used to sign in.\n\n'
        'If this wasn\'t you, change your password right away.',
    )
    return JsonResponse({'ok': True})
