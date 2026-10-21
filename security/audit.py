"""
Security audit trail.

    log_event(request, 'passkey_added', user=request.user, name='Office PC')

Logins, logouts and failed logins are recorded automatically through Django's
auth signals (below), so every way of signing in is covered. Views that log a
user in can set `request.audit_login_method` first to say how they did it.
"""
import logging

from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.dispatch import receiver

from .models import AuditLog

logger = logging.getLogger(__name__)

# How the person signed in, set by the login views before calling login()
LOGIN_PASSWORD_OTP = 'password + email code'
LOGIN_TRUSTED_DEVICE = 'password + trusted device'
LOGIN_PASSKEY = 'passkey'


def _client_ip(request):
    return request.META.get('REMOTE_ADDR') if request else None


def log_event(request, event_type, user=None, username='', **details):
    """Write one audit row. Never lets a logging problem break the request."""
    try:
        if user is not None and getattr(user, 'is_authenticated', False):
            username = username or user.get_username()
        else:
            user = None
        AuditLog.objects.create(
            event_type=event_type,
            user=user,
            username=(username or '')[:150],
            ip_address=_client_ip(request),
            user_agent=(request.META.get('HTTP_USER_AGENT', '') if request else '')[:500],
            details=details,
        )
    except Exception:
        logger.exception("Could not write audit log entry (%s)", event_type)


@receiver(user_logged_in)
def _on_login(sender, request, user, **kwargs):
    method = getattr(request, 'audit_login_method', None) or 'other (e.g. Django admin site)'
    log_event(request, 'login_success', user=user, method=method)


@receiver(user_logged_out)
def _on_logout(sender, request, user, **kwargs):
    if user is not None:
        log_event(request, 'logout', user=user)


@receiver(user_login_failed)
def _on_login_failed(sender, credentials, request=None, **kwargs):
    # Only the username is kept: Django already strips passwords from `credentials`
    username = credentials.get('username') or credentials.get('email') or ''
    reason = 'account locked (too many failed attempts)' if getattr(request, 'axes_locked_out', False) else 'wrong email or password'
    log_event(request, 'login_failed', username=username, reason=reason, method='password')
