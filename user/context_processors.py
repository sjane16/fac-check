from .notifications import notifications_for_user


def get_unread_notifications_count(request):
    if not request.user.is_authenticated:
        return 0
    if not hasattr(request, "unread_notifications_count"):
        request.unread_notifications_count = notifications_for_user(request.user, 'unread').count()
    return request.unread_notifications_count


def notifications_context(request):
    return {"unread_notifications_count": get_unread_notifications_count(request)}
