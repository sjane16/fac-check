"""
User Management (admin): approve staff sign-ups, find users, deactivate /
reactivate accounts and clean up accounts that never verified their email.

Account states (see account_status):
  pending      verified email, staff account waiting for an admin's approval
  active       can log in
  unverified   signed up but never entered the email code   (is_active False, no deactivated_at)
  deactivated  turned off by an admin                       (is_active False, deactivated_at set)
"""
import json

from django.conf import settings
from django.contrib.auth.models import User
from django.core.mail import send_mail
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import render
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.timesince import timesince
from django.views.decorators.http import require_POST
from django_ratelimit.core import is_ratelimited

from security.audit import log_event
from user.models import Notification, UserProfile, invalidate_trusted_devices
from user.constants import (
    MED_PERSONNEL_BY_SLUG,
    MED_PERSONNEL_CATEGORY_BY_SLUG,
    MED_PERSONNEL_CATEGORY_CHOICES,
    MED_PERSONNEL_OPTIONS,
)
from .personnel import clear_personnel_cache

USERS_PAGE_SIZE = 10
STATUSES = ('active', 'pending', 'unverified', 'deactivated')
ROLES = ('student', 'faculty', 'staff', 'admin')

ADMIN_Q = Q(is_staff=True) | Q(is_superuser=True) | Q(profile__role='admin')
STATUS_Q = {
    'pending': Q(is_active=True, profile__approval_status=UserProfile.APPROVAL_PENDING),
    'active': Q(is_active=True) & ~Q(profile__approval_status=UserProfile.APPROVAL_PENDING),
    'unverified': Q(is_active=False, profile__deactivated_at__isnull=True),
    'deactivated': Q(is_active=False, profile__deactivated_at__isnull=False),
}


# ---------- helpers ----------

def is_admin_account(user):
    profile = getattr(user, 'profile', None)
    return user.is_staff or user.is_superuser or (profile is not None and profile.role == 'admin')

def account_status(user):
    profile = getattr(user, 'profile', None)
    if not user.is_active:
        return 'deactivated' if profile and profile.deactivated_at else 'unverified'
    if profile and profile.approval_status == UserProfile.APPROVAL_PENDING:
        return 'pending'
    return 'active'

def role_label(user):
    if getattr(getattr(user, 'profile', None), 'is_head_admin', False):
        return 'Head Admin'
    if is_admin_account(user):
        return 'Admin'
    profile = getattr(user, 'profile', None)
    return profile.get_role_display() if profile and profile.role else '—'

def _initials(user):
    parts = [user.first_name, user.last_name]
    letters = ''.join(p[0] for p in parts if p)
    return (letters or user.username[:2]).upper()

def _decorate(users):
    for u in users:
        u.um_status = account_status(u)
        u.um_role = role_label(u)
        u.um_is_admin = is_admin_account(u)
        # Superusers are developer accounts: never changed from this page
        u.um_is_superuser = u.is_superuser
        u.um_is_head = bool(getattr(getattr(u, 'profile', None), 'is_head_admin', False))
        u.um_base_role = getattr(getattr(u, 'profile', None), 'role', '') or ''
        u.um_med_personnel_slug = getattr(getattr(u, 'profile', None), 'med_personnel_slug', '') or ''
        u.um_med_personnel_category = getattr(getattr(u, 'profile', None), 'med_personnel_category', '') or ''
        u.um_initials = _initials(u)
    return users

def _base_queryset():
    return User.objects.select_related('profile')

def _filtered_users(status, role, search):
    # Only ACTIVE pending staff are hidden here (they have their own "Pending Approval" card).
    # Unverified staff are still pending in the database, but must stay in the list so they can be deleted.
    qs = _base_queryset().exclude(
        is_active=True, profile__role='staff', profile__approval_status=UserProfile.APPROVAL_PENDING,
    )
    if status in STATUS_Q:
        qs = qs.filter(STATUS_Q[status])
    if role == 'admin':
        qs = qs.filter(ADMIN_Q)
    elif role in ROLES:
        qs = qs.filter(profile__role=role).exclude(ADMIN_Q)
    if search:
        qs = qs.filter(
            Q(first_name__icontains=search) | Q(last_name__icontains=search) |
            Q(email__icontains=search) | Q(username__icontains=search)
        )
    return qs.distinct().order_by('-date_joined')

def _stats():
    base = _base_queryset()
    return {key: base.filter(q).count() for key, q in STATUS_Q.items()}

def pending_staff():
    users = _decorate(list(_base_queryset().filter(STATUS_Q['pending'], profile__role='staff').order_by('date_joined')))
    for user in users:
        user.um_signup_age = (
            timesince(user.date_joined)
            .replace('hours', 'hrs')
            .replace('hour', 'hr')
            .replace('minutes', 'mins')
            .replace('minute', 'min')
        )
    return users

def _notify(user, subject, message):
    send_mail(subject=subject, message=message, from_email=settings.DEFAULT_FROM_EMAIL,
              recipient_list=[user.email], fail_silently=True)


# ---------- pages ----------

def user_management(request):
    return render(request, 'user_management.html', {
        'active_page': 'user_management',
        'stats': _stats(),
        'pending_users': pending_staff(),
        'med_personnel_options': MED_PERSONNEL_OPTIONS,
        'med_personnel_categories': MED_PERSONNEL_CATEGORY_CHOICES,
    })


def user_management_ajax(request):
    status = request.GET.get('status', '')
    role = request.GET.get('role', '')
    search = request.GET.get('search', '').strip()[:100]

    paginator = Paginator(_filtered_users(status, role, search), USERS_PAGE_SIZE)
    page_obj = paginator.get_page(request.GET.get('page', 1))
    _decorate(page_obj.object_list)

    context = {'users': page_obj, 'me': request.user}
    return JsonResponse({
        'desktop_html': render_to_string('partials/_users_desktop_rows.html', context, request=request),
        'mobile_html': render_to_string('partials/_users_mobile_rows.html', context, request=request),
        'pending_html': render_to_string('partials/_users_pending.html', {'pending_users': pending_staff()}, request=request),
        'stats': _stats(),
        'current_page': page_obj.number,
        'total_pages': paginator.num_pages,
        'has_previous': page_obj.has_previous(),
        'has_next': page_obj.has_next(),
        'total_count': paginator.count,
    })


# ---------- actions ----------

ACTION_RULES = {
    # action: (status the account must be in, past-tense message)
    'approve': ('pending', 'approved'),
    'reject': ('pending', 'rejected and removed'),
    'deactivate': ('active', 'deactivated'),
    'reactivate': ('deactivated', 'reactivated'),
    'delete': ('unverified', 'deleted'),
}


def _error(message, status=400):
    return JsonResponse({'ok': False, 'error': message}, status=status)


@require_POST
def user_management_action(request, user_id):
    try:
        body = json.loads(request.body or b'{}')
    except ValueError:
        return _error("Invalid request.")
    action = body.get('action')
    reason = (body.get('reason') or '').strip()[:500]
    if action in ('make_admin', 'remove_admin'):
        response = _change_admin_access(request, user_id, action, body)
        clear_personnel_cache()
        return response
    if action not in ACTION_RULES:
        return _error("Unknown action.")

    target = _base_queryset().filter(pk=user_id).first()
    if not target:
        return _error("This account no longer exists.", status=404)
    if target.pk == request.user.pk:
        return _error("You can't change your own account here.", status=403)
    if is_admin_account(target):
        return _error("Admin accounts can't be changed from this page.", status=403)

    required_status, done = ACTION_RULES[action]
    current = account_status(target)
    if current != required_status:
        return _error(f"This account is {current}, so it can't be {done}. Refresh the page and try again.", status=409)

    name = target.get_full_name() or target.email
    email = target.email
    profile, _ = UserProfile.objects.get_or_create(user=target)

    with transaction.atomic():
        if action == 'approve':
            profile.approval_status = UserProfile.APPROVAL_APPROVED
            profile.save(update_fields=['approval_status'])
            Notification.objects.filter(staff_account=target, is_read=False).update(is_read=True)
            log_event(request, 'user_approved', user=request.user, target=email)
            _notify(target, 'Your Fac-Check staff account was approved',
                    'Good news! The Head Admin approved your Fac-Check staff account. You can now log in.')

        elif action == 'reject':
            log_event(request, 'user_rejected', user=request.user, target=email, reason=reason)
            _notify(target, 'Your Fac-Check staff account request',
                    'An administrator reviewed your Fac-Check staff account request and could not approve it.'
                    + (f'\n\nReason: {reason}' if reason else '')
                    + '\n\nIf you think this is a mistake, please contact the Maintenance and Engineering Division.')
            target.delete()

        elif action == 'deactivate':
            target.is_active = False
            target.save(update_fields=['is_active'])
            profile.deactivated_at = timezone.now()
            profile.save(update_fields=['deactivated_at'])
            # Signs them out everywhere: trusted devices stop skipping the code, and
            # Django drops sessions of inactive users on their next request
            invalidate_trusted_devices(target)
            log_event(request, 'user_deactivated', user=request.user, target=email, reason=reason)
            _notify(target, 'Your Fac-Check account was deactivated',
                    'Your Fac-Check account has been deactivated by an administrator. You can no longer log in.'
                    + (f'\n\nReason: {reason}' if reason else '')
                    + '\n\nIf you think this is a mistake, please contact the administrator.')

        elif action == 'reactivate':
            target.is_active = True
            target.save(update_fields=['is_active'])
            profile.deactivated_at = None
            profile.save(update_fields=['deactivated_at'])
            log_event(request, 'user_reactivated', user=request.user, target=email)
            _notify(target, 'Your Fac-Check account was reactivated',
                    'Your Fac-Check account has been reactivated. You can log in again.')

        elif action == 'delete':
            log_event(request, 'user_deleted', user=request.user, target=email)
            target.delete()

    clear_personnel_cache()
    return JsonResponse({'ok': True, 'message': f"{name} was {done}."})


# ---------- admin access (make / remove admin) ----------

def active_admin_count():
    return User.objects.filter(is_active=True).filter(Q(is_staff=True) | Q(is_superuser=True)).count()


def _change_admin_access(request, user_id, action, body):
    """Giving or removing admin access always needs the acting admin's own password,
    so an unlocked, unattended computer can't be used to create admins."""
    if is_ratelimited(request, group='um-admin-access', key='user', rate='5/m', method='POST', increment=True):
        return _error("Too many attempts. Please wait a minute and try again.", status=429)
    if not request.user.check_password(body.get('password') or ''):
        return _error("Your password is incorrect.", status=403)

    target = _base_queryset().filter(pk=user_id).first()
    if not target:
        return _error("This account no longer exists.", status=404)
    if target.pk == request.user.pk:
        return _error("You can't change your own admin access.", status=403)
    if target.is_superuser:
        return _error("Superuser accounts can only be changed by a developer.", status=403)
    if getattr(getattr(target, 'profile', None), 'is_head_admin', False):
        return _error("The Head Admin can only be changed by a developer.", status=403)

    name = target.get_full_name() or target.email
    profile, _ = UserProfile.objects.get_or_create(user=target)

    if action == 'make_admin':
        if profile.role != 'staff':
            return _error("Only Staff accounts can be made admins.", status=403)
        if is_admin_account(target):
            return _error(f"{name} is already an admin.", status=409)
        if account_status(target) != 'active':
            return _error("Only active accounts can be made admins.", status=409)
        with transaction.atomic():
            # is_staff only - never is_superuser. Their normal role is kept so
            # removing admin later puts them back where they were.
            target.is_staff = True
            target.save(update_fields=['is_staff'])
            # New admins sign in again with an email code (admins: 7-day trusted devices)
            invalidate_trusted_devices(target)
            log_event(request, 'user_promoted', user=request.user, target=target.email)
        _notify(target, "You've been given admin access to Fac-Check",
                f"{request.user.get_full_name() or request.user.email} gave your Fac-Check account admin access.\n\n"
                "Next time you log in you'll see the admin dashboard. If you weren't expecting this, "
                "please contact the Maintenance and Engineering Division.")
        return JsonResponse({'ok': True, 'message': f"{name} is now an admin."})

    # remove_admin
    if not is_admin_account(target):
        return _error(f"{name} isn't an admin.", status=409)
    if target.is_active and active_admin_count() <= 1:
        return _error("You can't remove the last admin. Make someone else an admin first.", status=409)
    new_role = body.get('role') or ''
    personnel_slug = profile.med_personnel_slug
    personnel_category = profile.med_personnel_category
    if profile.role in ('', 'admin'):
        # Accounts that were created as admins have no normal role to go back to
        if new_role not in CHANGEABLE_ROLES:
            return _error("Choose which role this account should have.", status=400)
        if new_role == 'med_personnel':
            mapping = _med_personnel_mapping(target, body)
            if isinstance(mapping, JsonResponse):
                return mapping
            personnel_slug, personnel_category = mapping
        else:
            personnel_slug = personnel_category = ''
    with transaction.atomic():
        target.is_staff = False
        target.save(update_fields=['is_staff'])
        if profile.role in ('', 'admin'):
            profile.role = new_role
            profile.med_personnel_slug = personnel_slug
            profile.med_personnel_category = personnel_category
            profile.save(update_fields=['role', 'med_personnel_slug', 'med_personnel_category'])
        invalidate_trusted_devices(target)
        log_event(request, 'user_demoted', user=request.user, target=target.email, new_role=profile.role)
    _notify(target, 'Your Fac-Check admin access was removed',
            f"Your admin access to Fac-Check was removed. Your account is now a {profile.get_role_display().lower()} account.\n\n"
            "If you think this is a mistake, please contact the Maintenance and Engineering Division.")
    return JsonResponse({'ok': True, 'message': f"{name} is no longer an admin."})


# Legacy admin accounts can still choose a fallback role when access is removed.

CHANGEABLE_ROLES = ('student', 'faculty', 'staff')


def _med_personnel_mapping(target, body):
    slug = (body.get('med_personnel_slug') or '').strip()
    category = (body.get('med_personnel_category') or '').strip()
    if slug == '__new__':
        if category not in MED_PERSONNEL_CATEGORY_BY_SLUG:
            return _error('Choose the MED section for this new personnel.')
        name = (target.get_full_name() or target.email or target.username).strip().casefold()
        if any(name == option['name'].casefold() for option in MED_PERSONNEL_BY_SLUG.values()):
            return _error('This name is already in the hardcoded roster; select that roster entry instead.')
        existing_names = (
            (profile.user.get_full_name() or profile.user.email or profile.user.username).strip().casefold()
            for profile in UserProfile.objects.filter(role='med_personnel').exclude(user=target).select_related('user')
        )
        if name in existing_names:
            return _error('A MED personnel account with this name is already linked.')
        return f'account-{target.pk}', category
    if slug not in MED_PERSONNEL_BY_SLUG:
        return _error('Choose an existing roster name or add this account as a new personnel.')
    if UserProfile.objects.filter(med_personnel_slug=slug).exclude(user=target).exists():
        return _error('That MED personnel name is already linked to another account.', status=409)
    return slug, ''
