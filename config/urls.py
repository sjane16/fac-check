"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.shortcuts import redirect
from django.urls import path, include, reverse
from django.utils.http import urlencode
from django.conf import settings
from django.conf.urls.static import static


def admin_login_via_fac_check(request, extra_context=None):
    """Django's own admin login only asks for a password. Send every admin sign-in
    through Fac-Check's login instead, so the email code (or a passkey) is required."""
    next_url = request.GET.get('next', reverse('admin:index'))
    return redirect(f"{reverse('login')}?{urlencode({'next': next_url})}")


admin.site.login = admin_login_via_fac_check

urlpatterns = [
    path('admin/', admin.site.urls),
    path('auth/', include('accounts.urls')),
    path('user/', include('user.urls')),
    path('adm/', include('adm.urls')),
    path('', include('security.urls')),   
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)