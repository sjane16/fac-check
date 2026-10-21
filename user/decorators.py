from django.shortcuts import redirect
from functools  import wraps
from django.views.decorators.cache import never_cache
from django.contrib.auth.views import redirect_to_login

def user_required(view_func):
    @wraps(view_func)
    @never_cache
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(        #remembers the requested page in the URL (selected shortcut key)
                request.get_full_path(),
                login_url='login',
            )
        if request.user.is_staff or request.user.is_superuser:
            return redirect('dashboard')
        
        return view_func(request, *args, **kwargs)
    return wrapper

def admin_required(view_func):
    @wraps(view_func)
    @never_cache
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(
                request.get_full_path(),
                login_url='login',
            )
        
        if not (request.user.is_staff or request.user.is_superuser):
            return redirect('userDashboard')
        
        return view_func(request, *args, **kwargs)
    return wrapper

def is_head_admin(user):
    """The Head Admin (admin of the admins). Regular admins, including ones made
    through User Management, are not Head Admins."""
    if not (user.is_authenticated and user.is_active and (user.is_staff or user.is_superuser)):
        return False
    profile = getattr(user, 'profile', None)
    return bool(profile and profile.is_head_admin)

def head_admin_required(view_func):
    """Restrict user management, ratings and exports to the Head Admin."""
    @wraps(view_func)
    @never_cache
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path(), login_url='login')
        if not (request.user.is_staff or request.user.is_superuser):
            return redirect('userDashboard')
        if not is_head_admin(request.user):
            # JSON endpoints get a clear error instead of a redirect page
            if request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.content_type == 'application/json' \
                    or request.path.startswith('/adm/ajax/') or '/action/' in request.path or '/import/' in request.path \
                    or request.path.endswith(('/pdf/', '/summary/')):
                from django.http import JsonResponse
                return JsonResponse({'ok': False, 'error': "Only the Head Admin can access this feature."}, status=403)
            return redirect('dashboard')
        return view_func(request, *args, **kwargs)
    return wrapper
