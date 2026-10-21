from django.contrib import admin
from .models import HoneypotHit, AuditLog

@admin.register(HoneypotHit)
class HoneypotHitAdmin(admin.ModelAdmin):
    list_display = ('trigger_type', 'ip_address', 'path', 'time_stamp')
    list_filter = ('trigger_type',)
    search_fields = ('ip_address', 'path', 'user_agent')
    ordering = ('-time_stamp',)
    readonly_fields = ('trigger_type', 'path', 'ip_address', 'user_agent', 'time_stamp', 'extra_info')

    def has_add_permission(self, request):
        return False


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'event_type', 'username', 'summary', 'ip_address')
    list_filter = ('event_type', 'created_at')
    search_fields = ('username', 'ip_address', 'user_agent')
    date_hierarchy = 'created_at'
    ordering = ('-created_at',)
    readonly_fields = ('created_at', 'event_type', 'user', 'username', 'ip_address', 'user_agent', 'details')

    @admin.display(description='Details')
    def summary(self, obj):
        return ', '.join(f"{k}: {v}" for k, v in (obj.details or {}).items())

    # The audit trail is append-only: no one can add, edit or delete entries here
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
