from django.db import models

class HoneypotHit(models.Model):
    TRIGGER_TYPE = [
        ('form_field', 'Hidden Form Field'),
        ('decoy_admin', 'Decoy Admin Page'),
        ('probe_path', 'Known Attack Path'),
        ('honey_token', 'Fake API/Object Access'),
    ]

    trigger_type = models.CharField(max_length=20, choices=TRIGGER_TYPE)
    path = models.CharField(max_length=255)
    ip_address = models.GenericIPAddressField()
    user_agent = models.TextField(blank=True, null=True)
    time_stamp = models.DateTimeField(auto_now_add=True)
    extra_info = models.TextField(blank=True, null=True)

    def __str__(self):
        return f"{self.get_trigger_type_display()} from {self.ip_address} at {self.time_stamp}"
    
    class Meta:
        ordering=['-time_stamp']


class AuditLog(models.Model):
    """Security audit trail: sign-ins, sign-outs, failed attempts, passkey and password changes.
    Rows are written by security.audit.log_event() and are read-only in the admin."""
    EVENT_TYPES = [
        ('login_success', 'Login successful'),
        ('login_failed', 'Login failed'),
        ('logout', 'Logged out'),
        ('otp_locked', 'Verification code locked (too many wrong attempts)'),
        ('passkey_added', 'Passkey added'),
        ('passkey_renamed', 'Passkey renamed'),
        ('passkey_removed', 'Passkey removed'),
        ('password_changed', 'Password changed'),
        ('password_reset', 'Password reset via email code'),
        ('user_approved', 'Staff account approved'),
        ('user_rejected', 'Staff account rejected'),
        ('user_deactivated', 'Account deactivated'),
        ('user_reactivated', 'Account reactivated'),
        ('user_deleted', 'Unverified account deleted'),
        ('user_promoted', 'Made an admin'),
        ('user_demoted', 'Admin access removed'),
        ('users_imported', 'Users imported from Excel'),
        ('role_changed', 'Role changed'),
    ]

    event_type = models.CharField(max_length=30, choices=EVENT_TYPES, db_index=True)
    # Kept even if the account is deleted later, so the history stays readable
    user = models.ForeignKey('auth.User', null=True, blank=True, on_delete=models.SET_NULL, related_name='audit_logs')
    username = models.CharField(max_length=150, blank=True, help_text="Email/username at the time of the event")
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    details = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.get_event_type_display()} - {self.username or 'unknown'} at {self.created_at}"
