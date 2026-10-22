import logging
from html import escape
from smtplib import SMTPException
from threading import Thread

from django.conf import settings
from django.contrib.auth.models import User
from django.core.mail import EmailMultiAlternatives
from django.db import connections, transaction
from django.db.models import Q
from django.urls import NoReverseMatch, reverse

from .decorators import is_head_admin
from .models import Notification, UserProfile

logger = logging.getLogger(__name__)

STATUS_LABELS = {
    'in_progress': 'In Progress',
    'resolved': 'Resolved',
    'closed': 'Closed',
}

IMG_WIDTH = 200
NOTIFICATIONS_PAGE_SIZE = 25


def notifications_for_user(user, notif_filter='all'):
    notifications = Notification.objects.filter(user=user).select_related('report')
    if not is_head_admin(user):
        notifications = notifications.filter(staff_account__isnull=True)
    if notif_filter == 'unread':
        notifications = notifications.filter(is_read=False)
    return notifications


def notify_staff_approval(user):
    if not UserProfile.objects.filter(
        user=user, role='staff', approval_status=UserProfile.APPROVAL_PENDING,
        user__is_active=True, deactivated_at__isnull=True,
    ).exists():
        return
    heads = User.objects.filter(
        Q(is_staff=True) | Q(is_superuser=True), is_active=True, profile__is_head_admin=True,
    )
    name = user.get_full_name().strip() or user.email or user.username
    for head in heads:
        Notification.objects.get_or_create(
            user=head, staff_account=user,
            defaults={'message': f'Staff account awaiting approval: {name[:150]} has verified their email.'},
        )


def serialize_notification(notification, include_completion_photo=False):
    data = {
        'id': notification.id,
        'message': notification.message,
        'is_read': notification.is_read,
        'is_recent': notification.is_recent,
        'time_ago': notification.time_ago,
        'report_id': notification.report_id,
        'staff_account_id': notification.staff_account_id,
        'status': notification.account_event or (
            'staff_approval' if notification.staff_account_id else
            notification.status_snapshot or notification.report.status
        ),
    }
    if include_completion_photo:
        data['completion_photo_url'] = (
            notification.report.completion_photo.url
            if notification.report_id and notification.status_snapshot == 'resolved' and notification.report.completion_photo
            else None
        )
    return data


def _photo_url(field):
    if not field:
        return ''
    try:
        return field.url.replace('http://res.cloudinary.com', 'https://res.cloudinary.com')
    except Exception:
        return ''


def _send_status_email(email, report, status, message):
    label = STATUS_LABELS.get(status, status)
    job_order = str(report.job_order_id or '')
    first_name = (report.user.first_name or '').strip()
    greeting = f"Hello {first_name}," if first_name else "Hello,"

    site_url = getattr(settings, 'SITE_URL', '').rstrip('/')
    try:
        link = site_url + reverse('reportedIssue', args=[report.id])
    except NoReverseMatch:
        link = site_url

    show_details = status in ('in_progress', 'resolved')
    concern = (report.description or '').strip() if show_details else ''
    photo = _photo_url(report.photo) if show_details else ''
    proof = _photo_url(report.completion_photo) if status == 'resolved' else ''

    text = [greeting, "", message, "", f"Job Order: {job_order}", f"Status: {label}"]
    html = [
        f"<p>{escape(greeting)}</p>",
        f"<p>{escape(message)}</p>",
        f"<p><b>Job Order:</b> {escape(job_order)}<br><b>Status:</b> {escape(label)}</p>",
    ]

    if concern:
        text += ["", f"Your Concern: {concern}"]
        html.append(f"<p><b>Your Concern:</b><br>{escape(concern).replace(chr(10), '<br>')}</p>")
    if photo:
        html.append(f'<p><b>Submitted Photo:</b><br><img src="{escape(photo)}" width="{IMG_WIDTH}"></p>')
    if proof:
        html.append(f'<p><b>Resolution Proof:</b><br><img src="{escape(proof)}" width="{IMG_WIDTH}"></p>')

    text += ["", f"View report details: {link}", "", "Thank you,", "Fac-Check Team",
             "", "This is an automated message. Please do not reply."]
    html.append(f'<p><a href="{escape(link)}">View report details</a></p>')
    html.append("<p>Thank you,<br>Fac-Check Team</p>")
    html.append("<p><small>This is an automated message. Please do not reply.</small></p>")

    try:
        msg = EmailMultiAlternatives(
            subject=f"[Fac-Check] Report {job_order} is now {label}",
            body="\n".join(text),
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[email],
        )
        msg.attach_alternative("".join(html), "text/html")
        msg.send(fail_silently=False)
    except (SMTPException, OSError):
        logger.exception("Failed to send status email for report %s", job_order)


def _send_status_email_in_background(email, report, status, message):
    """Send the email without making the admin or student wait for Gmail."""
    def run():
        try:
            _send_status_email(email, report, status, message)
        except Exception:
            logger.exception("Status email thread failed for report %s", report.id)
        finally:
            connections.close_all()

    Thread(target=run, name="status-email", daemon=True).start()


def notify_status_change(report, status, message):
    notification = Notification.objects.create(
        user=report.user,
        report=report,
        message=message,
        status_snapshot=status,
    )
    email = (report.user.email or '').strip()
    if email:
        transaction.on_commit(lambda: _send_status_email_in_background(email, report, status, message))
    return notification
