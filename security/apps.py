from django.apps import AppConfig


class SecurityConfig(AppConfig):
    name = 'security'

    def ready(self):
        # Connects the login / logout / failed-login receivers for the audit trail
        from . import audit  # noqa: F401
