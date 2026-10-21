def admin_role(request):
    if request.user.is_authenticated and (request.user.is_staff or request.user.is_superuser):
        from user.decorators import is_head_admin
        head = is_head_admin(request.user)
        return {"role": "Head Admin" if head else "Admin", "is_head_admin": head}
    return {}
