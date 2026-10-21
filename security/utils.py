from django.utils import timezone
from datetime import timedelta
from .models import HoneypotHit

def is_ip_flagged(ip_address, window_minutes=30):
    """
    Returns True if this IP has triggered any honeypot
    within the last `window_minutes`.
    """

    cutoff = timezone.now() - timedelta(minutes=window_minutes)
    return HoneypotHit.objects.filter(
        ip_address=ip_address,
        time_stamp__gte=cutoff
    ).exists()