from django.conf import settings
from django.http import HttpResponseNotFound
from .models import HoneypotHit


class ClientIPFromProxyMiddleware:
    """Behind Render's proxy every request arrives from the proxy's address, so rate limits,
    lockouts, the honeypot and the audit log would treat all visitors as one person.
    When BEHIND_PROXY is on, put the visitor's real address in REMOTE_ADDR.

    The proxy appends the address it saw to X-Forwarded-For, so the last entry is the
    trustworthy one; earlier entries can be typed in by the visitor and are ignored."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if settings.BEHIND_PROXY:
            forwarded = request.META.get('HTTP_X_FORWARDED_FOR', '')
            client_ip = forwarded.split(',')[-1].strip()
            if client_ip:
                request.META['REMOTE_ADDR'] = client_ip
        return self.get_response(request)


def client_ip(request):
    """Used by django-axes (AXES_CLIENT_IP_CALLABLE) so lockouts use the same address as
    everything else, instead of axes reading the spoofable first X-Forwarded-For entry."""
    return request.META.get('REMOTE_ADDR')

PROBE_PATHS = [
    '.env',
    '.env.bak',
    '.git/config',
    'wp-login.php',
    'wp-admin/',
    'config.php.bak',
    'phpinfo.php',
    'xmlrpc.php',
    '.aws/credentials',
    'server-status',
    'debug.log',
]

class HoneypotProbeMiddleware:

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path.lstrip('/')

        if any(path.startswith(probe) for probe in PROBE_PATHS):
            HoneypotHit.objects.create(
                trigger_type='probe_path',
                path=request.path,
                ip_address=request.META.get('REMOTE_ADDR'),
                user_agent=request.META.get('HTTP_USER_AGENT', ''),
                extra_info=f"Method: {request.method}"
            )
            return HttpResponseNotFound()
        
        return self.get_response(request)