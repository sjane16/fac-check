from django.shortcuts import redirect
from django.urls import reverse


class ForcePasswordChangeMiddleware:
    """Accounts created by an admin (Excel import) log in with an emailed temporary
    password. Until they choose their own, every page sends them to the change page."""

    ALLOWED_PREFIXES = ('/static/', '/media/', '/user/logout/', '/adm/logout/')

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, 'user', None)
        if user is not None and user.is_authenticated:
            profile = getattr(user, 'profile', None)
            if profile is not None and profile.must_change_password:
                change_url = reverse('force_password_change')
                if request.path != change_url and not request.path.startswith(self.ALLOWED_PREFIXES):
                    return redirect(change_url)
        return self.get_response(request)
