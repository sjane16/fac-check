from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from .forms import ProfilePasswordChangeForm, ReportForm, UserForm, UserProfileForm, ProfilePhotoForm
from .forms import form_error_text
import hashlib
import os
from functools import lru_cache
from urllib.parse import unquote
from django.http import JsonResponse, HttpResponse
from xhtml2pdf import pisa
from django.conf import settings
from django.contrib.staticfiles import finders
from django.core.paginator import Paginator
from django.template.loader import render_to_string
from django.db import transaction
from django.db.models import Q
from google import genai
from django_ratelimit.core import is_ratelimited
from security.audit import log_event
from django.contrib.auth.models import User
from .models import Report, SurveyResponse, UserProfile, Notification, invalidate_trusted_devices
from adm.models import Floor, Room, Building 
from .notifications import notify_status_change, notifications_for_user, serialize_notification, NOTIFICATIONS_PAGE_SIZE
from .context_processors import get_unread_notifications_count
from ml.predictor import ImageDecodeError, predict_image, warm_model_async
import logging

logger = logging.getLogger(__name__)

REPORTS_PAGE_SIZE = 5
SURVEY_HISTORY_PAGE_SIZE = 25


@lru_cache(maxsize=1)
def _get_gemini_client(api_key):
    return genai.Client(api_key=api_key)


CNN_PREDICTION_SESSION_KEY = 'cnn_prediction'
CNN_FAILED_SESSION_KEY = 'cnn_prediction_failed'


def _photo_digest(photo):
    """SHA-256 of an uploaded photo, used to tie a stored prediction to the exact file."""
    digest = hashlib.sha256()
    photo.seek(0)
    for chunk in photo.chunks():
        digest.update(chunk)
    photo.seek(0)
    return digest.hexdigest()


def _resolve_cnn_prediction(request, photo):
    """Return the model's prediction for the submitted photo, never trusting browser fields.

    Reuses the result predictCategory stored in the session for this exact file;
    otherwise runs inference again. Returns None if the model cannot classify it.
    """
    stored = request.session.pop(CNN_PREDICTION_SESSION_KEY, None)
    failed_digest = request.session.pop(CNN_FAILED_SESSION_KEY, None)
    digest = _photo_digest(photo)
    if stored and stored.get('photo_sha256') == digest:
        return stored
    if failed_digest == digest:
        # The AI was unavailable when the student picked a category, so their choice stands
        # instead of being silently replaced by a later prediction.
        return None
    try:
        return predict_image(photo)
    except Exception:
        logger.exception("CNN prediction failed during report submission")
        return None
    finally:
        photo.seek(0)

@login_required
def menu(request):
    profile, created = UserProfile.objects.get_or_create(user=request.user)

    user_form = UserForm(instance=request.user)
    profile_form = UserProfileForm(instance=profile)
    photo_form = ProfilePhotoForm(instance=profile)
    is_post_render = request.method == 'POST'

    if request.method == 'POST':
        if 'save_info' in request.POST:
            user_form = UserForm(request.POST, instance=request.user)
            profile_form = UserProfileForm(request.POST, instance=profile)
            # Sanitizes first_name/last_name via UserForm
            if user_form.is_valid() and profile_form.is_valid():
                user_form.save()
                profile_form.save()
                messages.success(request, "Profile Updated Successfully")
                return redirect('menu')
            else:
                # Plain-text error message (avoids raw HTML from form.errors)
                messages.error(request, form_error_text(user_form, profile_form))

        elif 'save_photo' in request.POST:
            photo_form = ProfilePhotoForm(request.POST, request.FILES, instance=profile)
            if photo_form.is_valid():
                photo_form.save()
                messages.success(request, "Profile Photo Updated Successfully")
                return redirect('menu')
            else:
                if 'profile_photo' in photo_form.errors:
                    messages.error(request, photo_form.errors['profile_photo'][0])
                else:
                    messages.error(request, "Please upload a valid photo.")

        elif 'remove_photo' in request.POST:
            profile.profile_photo.delete(save=True)
            messages.success(request, "Profile Photo Removed Successfully")
            return redirect('menu')

        elif 'change_password' in request.POST:
            password_form = ProfilePasswordChangeForm(request.user, request.POST)
            if password_form.is_valid():
                password_form.save()
                invalidate_trusted_devices(request.user)
                log_event(request, 'password_changed', user=request.user, where='Edit Profile')
                update_session_auth_hash(request, request.user)
                messages.success(request, "Password changed successfully.")
                return redirect('menu')
            for error in password_form.non_field_errors():
                messages.error(request, error)

    return render(request, 'user/menu.html', {
        "active_page": "menu",
        "user_form": user_form,
        "profile_form": profile_form,
        "photo_form": photo_form,
        "is_post_render": is_post_render,
    })

@login_required
def menuAjax(request):
    profile, created = UserProfile.objects.get_or_create(user = request.user)
    profile_form = UserProfileForm(instance=profile)

    html = render_to_string('user/partials/_menu_content.html', {
        "user_form": UserForm(instance=request.user),
        "profile_form": profile_form,
    }, request=request)

    return JsonResponse({"html":html})

@login_required
def userDashboard(request):
    warm_model_async()
    return render(request, 'user/user_dashboard.html', {
        "active_page": "userDashboard",
    })


@login_required
def dashboardStatsAjax(request):
    from django.db.models import Count, Q

    status_counts = Report.objects.filter(user=request.user).aggregate(
        total_reports=Count('id'),
        pending_reports=Count('id', filter=Q(status='pending')),
        in_progress_reports=Count('id', filter=Q(status='in_progress')),
        resolved_reports=Count('id', filter=Q(status='resolved')),
        closed_reports=Count('id', filter=Q(status='closed')),
        
    )

    total = status_counts['total_reports']

    def pct(count):
        return round((count / total) * 100) if total else 0

    percentages = {
        "pending_pct": pct(status_counts['pending_reports']),
        "in_progress_pct": pct(status_counts['in_progress_reports']),
        "resolved_pct": pct(status_counts['resolved_reports']),
        "closed_pct": pct(status_counts['closed_reports']),
        "total_pct": 100 if total else 0,
    }

    recent_reports = Report.objects.filter(
        user=request.user
    ).order_by('-submitted_at')[:3]

    context = {
        **status_counts,
        **percentages,
        "user": request.user,
        "recent_reports": recent_reports,
    }

    mobile_html = render_to_string('user/partials/_dashboard_stats_mobile.html', context)
    desktop_html = render_to_string('user/partials/_dashboard_stats_desktop.html', context)

    return JsonResponse({
        "mobile_html": mobile_html,
        "desktop_html": desktop_html,
    })

@login_required
def reportForm(request):
    if request.method == 'POST':
        form = ReportForm(request.POST, request.FILES)

        if form.is_valid():
            report = form.save(commit=False)
            report.user = request.user

            selected_room = form.cleaned_data['location']
            report.set_room_location(selected_room)

            report.category = form.cleaned_data['category']
            report.custom_category = form.cleaned_data['custom_category']

            prediction = _resolve_cnn_prediction(request, form.cleaned_data['photo'])
            if prediction:
                # The AI category is locked in the UI, so enforce it here too
                report.category = prediction['category']
                report.cnn_raw_prediction = prediction['raw_label']
                report.cnn_confidence = prediction['confidence']
                report.cnn_margin = prediction['margin']
                report.flagged_for_review = prediction['flagged_for_review']
            else:
                # Manual classification: keep the user's category and send it to an admin
                report.cnn_raw_prediction = None
                report.cnn_confidence = None
                report.cnn_margin = None
                report.flagged_for_review = True

            report.save()

            for admin_user in get_admin_users():
                Notification.objects.create(
                    user=admin_user,
                    report=report,
                    message=f"New report submitted: {report.job_order_id}",
                    status_snapshot=report.status,
                )

            messages.success(request, "Your concern has been submitted successfully.")
            return redirect('userReports')




        if 'photo' in form.errors:
            messages.error(request, "Please upload a photo of the concern.")
        else:
            messages.error(request, "Please complete all required fields.")

        facility_building_ids = list(
            Building.objects.filter(has_floors=False).values_list('id', flat=True)
        )

        return render(request, 'user/report_form.html', {
            "active_page": "reportForm",
            "form": form,
            "facility_building_ids": facility_building_ids,
        })

    warm_model_async()
    return render(request, 'user/report_form.html', {
        "active_page": "reportForm",
    })


@login_required
def reportFormAjax(request):
    warm_model_async()
    form = ReportForm()

    facility_building_ids = list(
        Building.objects.filter(has_floors=False).values_list('id', flat=True)
    )

    html = render_to_string('user/partials/_report_form_content.html', {
        "form": form,
        "facility_building_ids": facility_building_ids,
    }, request=request)

    return JsonResponse({
        "html": html,
        "facility_building_ids": facility_building_ids,
    })

def _filter_user_reports(request, status_filter, search_query):
    reports = Report.objects.filter(user=request.user).order_by('-submitted_at')

    if status_filter != 'all':
        reports = reports.filter(status=status_filter)

    if search_query:
        reports = reports.filter(
            Q(job_order_id__icontains=search_query) |
            Q(location__icontains=search_query) |
            Q(category__icontains=search_query) |
            Q(custom_category__icontains=search_query)
        )

    return reports


@login_required
def userReports(request):
    status_filter = request.GET.get('status', 'all')

    return render(request, 'user/user_reports.html', {
        "active_page": "userReports",
        "status_filter": status_filter,
    })


@login_required
def userReportsAjax(request):
    status_filter = request.GET.get('status', 'all')
    search_query = request.GET.get('search', '').strip()
    page_number = request.GET.get('page', 1)

    reports = _filter_user_reports(request, status_filter, search_query)

    paginator = Paginator(reports, REPORTS_PAGE_SIZE)
    page_obj = paginator.get_page(page_number)

    mobile_html = render_to_string('user/partials/_reports_mobile_rows.html', {
        "reports": page_obj,
    })

    desktop_html = render_to_string('user/partials/_reports_desktop_rows.html', {
        "reports": page_obj,
    })

    return JsonResponse({
        "mobile_html": mobile_html,
        "desktop_html": desktop_html,
        "current_page": page_obj.number,
        "total_pages": paginator.num_pages,
        "has_previous": page_obj.has_previous(),
        "has_next": page_obj.has_next(),
        "total_count": paginator.count,
    })


@login_required
def reportedIssue(request, report_id):
    report = get_object_or_404(
        Report,
        id=report_id,
        user=request.user
    )

    already_surveyed = SurveyResponse.objects.filter(
        report=report
    ).exists()

    is_med_personnel_report = bool(
        getattr(getattr(request.user, 'profile', None), 'role', None) == 'med_personnel'
    )
    show_survey = (
        report.status == 'resolved'
        and not already_surveyed
        and not is_med_personnel_report
    )

    emojis = [
        ('😤', 'Unsatisfactory', 1),
        ('😞', 'Fair', 2),
        ('😐', 'Satisfactory', 3),
        ('😊', 'Very Satisfactory', 4),
        ('😄', 'Outstanding', 5),
    ]

    return render(request, 'user/reported_issue.html', {
        "active_page": "reportedIssue",
        "report": report,
        "already_surveyed": already_surveyed,
        "show_survey": show_survey,
        "is_med_personnel_report": is_med_personnel_report,
        "emojis": emojis,
        "survey_questions": (
            ('quality', 'Quality of Work'),
            ('quantity', 'Quantity of Work'),
            ('timeliness', 'Timeliness of Service'),
        ),
    })

def link_callback(uri, rel):
    """
    Convert HTML URIs to absolute system paths so xhtml2pdf
    can access Django static and media files.
    """

    uri = unquote(uri)

    if uri.startswith(settings.MEDIA_URL):
        path = os.path.join(
            settings.MEDIA_ROOT,
            uri.replace(settings.MEDIA_URL, "")
        )

    elif uri.startswith(settings.STATIC_URL):
        path = finders.find(
            uri.replace(settings.STATIC_URL, "")
        )

    else:
        return uri

    if not path or not os.path.isfile(path):
        raise Exception(
            f"Media URI must start with {settings.STATIC_URL} or {settings.MEDIA_URL}. "
            f"Could not find file: {uri}"
        )

    return path

@login_required
def exportJobOrderPDF(request, report_id):
    report = get_object_or_404(
        Report.objects.select_related('user__profile', 'survey'),
        id=report_id,
        user=request.user,
    )

    if report.status not in ('in_progress', 'resolved', 'closed'):
        return HttpResponse(
            "Job order export is available once maintenance is in progress.",
            status=403,
        )

    category_checks = {
        'vehicle_checkup': report.category == 'vehicle_checkup',
        'electrical': report.category == 'electrical',
        'refrigeration_acu': report.category == 'refrigeration_acu',
        'painting': report.category == 'painting',
        'carpentry_masonry': report.category == 'carpentry_masonry',
        'plumbing': report.category == 'plumbing',
        'others': report.category == 'others',
    }

    html = render_to_string('user/job_order_pdf.html', {
        "report": report,
        "category_checks": category_checks,
        "is_med_personnel_report": getattr(getattr(request.user, 'profile', None), 'role', None) == 'med_personnel',
    })

    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = f'inline; filename="Job_Order_{report.job_order_id or report.id}.pdf"'
    response['X-Frame-Options'] = 'SAMEORIGIN'
    response['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response['Pragma'] = 'no-cache'
    response['Expires'] = '0'

    pisa_status = pisa.CreatePDF(
        html,
        dest=response,
        link_callback=link_callback
)   

    if pisa_status.err:
        return HttpResponse("Something went wrong generating the Job Order PDF.", status=500)

    return response


@login_required
def userSurvey(request):
    return render(request, 'user/user_survey.html', {
        "active_page": "userSurvey",
    })


@login_required
def userSurveyAjax(request):
    submitted_surveys = SurveyResponse.objects.filter(
        report__user=request.user
    ).select_related('report', 'rated_by').order_by('-submitted_at')
    page_obj = Paginator(submitted_surveys, SURVEY_HISTORY_PAGE_SIZE).get_page(
        request.GET.get('page', 1)
    )

    html = render_to_string('user/partials/_survey_content.html', {
        "submitted_surveys": page_obj,
    })

    return JsonResponse({
        "html": html,
    })


@login_required
def chooseSurveyMethod(request, report_id):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Invalid request.'}, status=400)

    method = request.POST.get('method')
    if method not in dict(Report.SURVEY_METHOD_CHOICES):
        return JsonResponse({'success': False, 'error': 'Choose a valid survey method.'}, status=400)

    with transaction.atomic():
        report = get_object_or_404(
            Report.objects.select_for_update(),
            id=report_id,
            user=request.user,
        )
        if report.status != 'resolved' or SurveyResponse.objects.filter(report=report).exists():
            return JsonResponse({
                'success': False,
                'error': 'Survey method can only be selected for a resolved report without a submitted survey.',
            }, status=400)
        if getattr(getattr(request.user, 'profile', None), 'role', None) == 'med_personnel':
            return JsonResponse({
                'success': False,
                'error': 'The Head Admin will provide the QQT rating for MED personnel reports.',
            }, status=403)
        notify_manual_visit = method == 'manual' and report.survey_method != 'manual'
        report.survey_method = method
        report.save(update_fields=['survey_method'])
        if notify_manual_visit:
            visit_message = (
                f"{report.reporter_name[:100]} will visit the MED office during office hours "
                f"to complete the manual CSS for report {report.job_order_id}."
            )
            for admin_user in get_admin_users().filter(is_active=True):
                # Switching methods back and forth must not notify the admins again.
                if Notification.objects.filter(user=admin_user, report=report, message=visit_message).exists():
                    continue
                Notification.objects.create(
                    user=admin_user,
                    report=report,
                    message=visit_message,
                    status_snapshot=report.status,
                )
    return JsonResponse({'success': True, 'method': method})


@login_required
def submitSurvey(request, report_id):
    if request.method != 'POST':
        return JsonResponse({
            'success': False,
            'error': 'Invalid request.'
        }, status=400)

    ratings = {}
    for field in ('quality', 'quantity', 'timeliness'):
        try:
            ratings[field] = int(request.POST.get(field))
        except (TypeError, ValueError):
            return JsonResponse({
                'success': False,
                'error': f'Please select a valid {field} rating.'
            }, status=400)

        if ratings[field] not in range(1, 6):
            return JsonResponse({
                'success': False,
                'error': f'{field.title()} rating must be between 1 and 5.'
            }, status=400)

    with transaction.atomic():
        report = get_object_or_404(
            Report.objects.select_for_update(),
            id=report_id,
            user=request.user,
        )
        if getattr(getattr(request.user, 'profile', None), 'role', None) == 'med_personnel':
            return JsonResponse({
                'success': False,
                'error': 'The Head Admin provides ratings for MED personnel reports.',
            }, status=403)
        if SurveyResponse.objects.filter(report=report).exists():
            return JsonResponse({
                'success': False,
                'error': 'You have already submitted a survey for this report.'
            }, status=400)

        if report.status != 'resolved':
            return JsonResponse({
                'success': False,
                'error': 'Survey is available only after the report is resolved.'
            }, status=400)

        if report.survey_method != 'system':
            return JsonResponse({
                'success': False,
                'error': 'Choose the online survey method before submitting ratings.'
            }, status=400)

        SurveyResponse.objects.create(
            report=report,
            **ratings,
        )

        report.status = 'closed'
        report.save(update_fields=['status'])

    messages.success(request, f"Thank you for your feedback! Report {report.job_order_id} is now Closed.")

    notify_status_change(report, 'closed', f"Your report {report.job_order_id} is now Closed. Thank you for your feedback!")

    for admin_user in get_admin_users():
        Notification.objects.create(
            user=admin_user,
            report=report,
            message=f"Report {report.job_order_id} is now closed — client survey received.",
            status_snapshot=report.status,
        )

    return JsonResponse({
        'success': True
    })

UDM_FAQ_DATA = """
OFFICIAL FAQ KNOWLEDGE BASE FOR FAC-CHECK SYSTEM (UDM):
1. What is Fac-Check?
- Fac-Check is an automated facility concern reporting system for Universidad de Manila (UDM). It allows students, faculty, and staff to report campus defects
using AI-powered image classification to streamline university maintenance.
2. Who can use this system?
- All official UDM stakeholders, including active students, professors, and university administrative staff can log in and submit facility reports.
3. How do I report a broken facility or equipment?
- Go to the report submission page, upload a clear photo of the defect, and let Fac-Check suggest a category. Select the location, describe the concern, then submit the report.
4. Why did my report get rejected?
- The system requires a clear image of the actual defect for the CNN Classification Engine to analyze it properly. Ensure your photo is not blurry, dark,
or unrelated to a facility concern. The file size must also be within the allowed system limit.
5. Can I edit or delete my report after submission?
- No. Once a report is submitted, it is locked to preserve data integrity and immediately routed to the Maintenance and Engineering Division. If you made
a critical mistake, please contact the administrator to cancel the report.
6. How do I check the status of my report?
- You can monitor your submitted concerns in real-time through your Dashboard page. Each report will display its current stage in the maintenance workflow.
7. What do the different report statuses mean?
- "Pending" means your report has been successfully submitted and is waiting for administrator validation. It includes requests awaiting approval, materials, or personnel assignment. "In Progress" indicates that the maintenance team has acknowledged the issue and is actively
working on repair. "Resolved" means the defect has been fixed or addressed. "Closed" means the end-user has confirmed the resolution and completed the Client Satisfaction Survey.
8. What should I do if my account gets locked?
- For security reasons, our system utilizes security throttling that temporarily locks an account after multiple failed login attempts
to prevent unauthorized access. Please wait a few minutes before trying again, or contact IT support to reset your credentials.
9. Is my personal information safe in the system?
- Yes. The system employs HTTPS data encryption and secure Role-Based Access Controls to ensure that your personal profile and submitted data are strictly protected
and only accessible to authorized university personnel.
10. How does the AI classify my image?
- Fac-Check uses a Convolutional Neural Network (CNN) Classification Engine that automatically analyzes the photo you upload. It scans visual patterns in the image to determine the type of facility concern and assigns it to the correct maintenance category. 
This removes the need for you to manually select a category.
11. What categories can the system detect?
- The CNN model can classify facility concerns into the following categories: Electrical, Plumbing, Refrigeration / ACUs, Carpentry / Masonry, Painting, and Vehicle Check-up. 
If the image does not clearly belong to any defined category, it will be categorized to "Others" and will require the user to provide additional details.
12. What if the AI classifies my report incorrectly?
-The system currently does not allow end-users to override the AI classification to preserve data integrity. However, each report is reviewed by the Maintenance and Engineering Division, and if the AI misclassifies your report, it will be corrected during the review process.
13. What is the Client Satisfaction Survey?
- The Client Satisfaction Survey is a short feedback form that appears once your report has been marked as Resolved. It asks you to rate the quality and timeliness of the maintenance service you received on a scale of 1 to 5.
14. Is the Client Satisfaction Survey required?
- Yes. Completing the survey is required to officially close your report. Your feedback helps the university monitor the performance of the Maintenance and Engineering Division and improve service quality.
15. When do I fill out the Client Satisfaction Survey?
- The survey becomes available once the administrator marks your report as Resolved. It can be seen when you open the details of your report. You will need to submit it before the report status changes to Closed.
16. Will I be notified when my report status changes?
- Yes. You can monitor real-time status updates directly from your Dashboard. Each report card displays its current status so you can track progress from Pending all the way to Closed. You will also be notified via email for major status changes, such as when your report is marked as Resolved or Closed.
17. Who reviews and validates my submitted report?
- All submitted reports are routed to the university Administrator, who is part of the Maintenance and Engineering Division. The administrator validates the report, assigns the appropriate maintenance personnel, and updates the status as work progresses.
18. How long does it take for a report to be resolved?
- Resolution time varies depending on the severity and category of the concern. Critical and High Priority reports are addressed first. You can track the expected resolution time on your My Reports page, once you click on the specific report to view its details.
19. What is your name?
- I am Checky, the official automated chat assistant for the Fac-Check system at Universidad de Manila (UDM). I can answer questions about how to use the system and provide guidance on reporting facility concerns.
20. Ano pangalan mo?
- Ako si Checky, ang opisyal na chatbot ng Fac-Check sa Universidad de Manila (UDM). Maaari akong sumagot sa mga tanong tungkol sa paggamit ng sistema at gumabay sa iyo sa pag-uulat ng mga problema sa mga pasilidad.
"""


CHATBOT_MAX_MESSAGE_LENGTH = 500
# Each message is a paid Gemini call, so cap it per user (and per IP, in case
# several accounts are used from one machine).
CHATBOT_RATE_LIMITS = [
    ('chatbot-user-minute', 'user', '10/m', "You're sending messages too quickly. Please wait a minute and try again."),
    ('chatbot-user-day', 'user', '100/d', "You've reached today's chatbot limit. Please try again tomorrow."),
    ('chatbot-ip-minute', 'ip', '30/m', "Too many chatbot requests from this network. Please wait a minute and try again."),
]

def _chatbot_rate_limited(request):
    for group, key, rate, message in CHATBOT_RATE_LIMITS:
        if is_ratelimited(request, group=group, key=key, rate=rate, method='POST', increment=True):
            return message
    return None

def chatbot_response(request):
    if request.method == 'POST':
        user_message = request.POST.get('message', '').strip()
        if not user_message:
            return JsonResponse({'reply': "Please enter a message."})
        if len(user_message) > CHATBOT_MAX_MESSAGE_LENGTH:
            return JsonResponse({'reply': f"Your message is too long. Please keep it under {CHATBOT_MAX_MESSAGE_LENGTH} characters."}, status=400)

        limited = _chatbot_rate_limited(request)
        if limited:
            return JsonResponse({'reply': limited}, status=429)
        try:
            api_key = getattr(settings, "GEMINI_API_KEY", os.environ.get("GEMINI_API_KEY"))
            system_instruction = (
                "You are Checky, the official automated chat assistant for the Fac-Check system at Universidad de Manila (UDM). \n\n"
                "STRICT RULES: \n"
                "1. Answer the query strictly using the facts stated inside the OFFICIAL FAQ KNOWLEDGE BASE FOR FAC-CHECK SYSTEM (UDM) provided below. \n"
                "2. Do NOT use outside knowledge. If the answer cannot be found in the text document below, "
                "you are strictly prohibited from guessing or fabricating details. Instead, reply word-for-word: "
                "'Sorry, I don't have the information you're looking for.' \n\n"
                "3. Keep responses concise, helpful, and match the language used by the user (Tagalog, English, or a mix of both). \n\n"
                f"OFFICIAL FAQ KNOWLEDGE BASE FOR FAC-CHECK SYSTEM (UDM): \n{UDM_FAQ_DATA}"
            )
            response = _get_gemini_client(api_key).models.generate_content(
                model="gemini-2.5-flash",
                contents=user_message,
                config={
                    'system_instruction': system_instruction,
                    'temperature': 0.1,
                }
            )
            return JsonResponse({'reply': response.text})
        except Exception:
            logger.exception("Chatbot response generation failed")
            return JsonResponse({'reply': "Sorry, I'm having trouble processing your request right now."})
    return JsonResponse({'error': 'Invalid request'}, status=400)

#AJAX VIEWS FOR THE DROPDOWN
def load_floors(request):
    building_id = request.GET.get('building', '')
    if not building_id.isdigit():
        # A non-numeric id used to crash the lookup (500); there is simply nothing to list
        return JsonResponse([], safe=False)
    floors = Floor.objects.filter(building_id=building_id).order_by('floor_number')
    return JsonResponse(list(floors.values('id', 'label')), safe=False)

def load_rooms(request):
    floor_id = request.GET.get('floor', '')
    if not floor_id.isdigit():
        return JsonResponse([], safe=False)
    rooms = Room.objects.filter(floor_id=floor_id).order_by('order')
    return JsonResponse(list(rooms.values('id','name', 'code')), safe=False)

@login_required
def predictCategory(request):
    if request.method != 'POST' or 'photo' not in request.FILES:
        return JsonResponse({'success': False, 'error': 'Please upload a photo.'}, status=400)

    photo = request.FILES['photo']
    request.session.pop(CNN_PREDICTION_SESSION_KEY, None)
    request.session.pop(CNN_FAILED_SESSION_KEY, None)
    try:
        prediction = predict_image(photo)
    except ImageDecodeError:
        logger.warning("CNN could not decode uploaded photo", exc_info=True)
        return JsonResponse({
            'success': False,
            'error': "We couldn't read this photo. Please upload a clear JPG or PNG image.",
            'retry': False,
        }, status=400)
    except Exception:
        logger.exception("CNN prediction failed")
        request.session[CNN_FAILED_SESSION_KEY] = _photo_digest(photo)
        return JsonResponse({
            'success': False,
            'error': "AI categorization is unavailable right now. Try again or choose a category manually.",
            'retry': True,
        }, status=503)

    # Keep the model output server-side; reportForm reuses it for this exact file
    request.session[CNN_PREDICTION_SESSION_KEY] = {**prediction, 'photo_sha256': _photo_digest(photo)}

    return JsonResponse({
        'success': True,
        'category': prediction['category'],
        'confidence': prediction['confidence'],
        'margin': prediction['margin'],
        'flagged_for_review': prediction['flagged_for_review'],
        'raw_prediction': prediction['raw_label']
    })


def _filter_user_notifications(request, notif_filter):
    return notifications_for_user(request.user, notif_filter)


@login_required
def userNotifications(request):
    notif_filter = request.GET.get('filter', 'all')

    return render(request, 'user/user_notifications.html', {
        "active_page": "userNotifications",
        "notif_filter": notif_filter,
    })


@login_required
def get_notifications_page_ajax(request):
    notif_filter = request.GET.get('filter', 'all')
    page_obj = Paginator(
        _filter_user_notifications(request, notif_filter),
        NOTIFICATIONS_PAGE_SIZE,
    ).get_page(request.GET.get('page', 1))

    html = render_to_string('user/partials/_notifications_rows.html', {
        "notifications": page_obj,
    })

    return JsonResponse({
        "html": html,
        "current_page": page_obj.number,
        "total_pages": page_obj.paginator.num_pages,
        "total_count": page_obj.paginator.count,
    })


@login_required
def get_notifications_ajax(request):
    notif_filter = request.GET.get('filter', 'all')
    notifications = _filter_user_notifications(request, notif_filter)[:10]

    data = [
        serialize_notification(n, include_completion_photo=True)
        for n in notifications
    ]

    unread_count = get_unread_notifications_count(request)

    return JsonResponse({
        "notifications": data,
        "unread_count": unread_count,
    })


@login_required
def view_notification(request, notification_id):
    notification = get_object_or_404(notifications_for_user(request.user), id=notification_id)

    if not notification.is_read:
        notification.is_read = True
        notification.save(update_fields=['is_read'])

    return redirect('reportedIssue', report_id=notification.report.id)


@login_required
def mark_all_notifications_read(request):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Invalid request.'}, status=400)

    Notification.objects.filter(user=request.user, is_read=False).update(is_read=True)

    return JsonResponse({'success': True})


def get_admin_users():
    return User.objects.filter(Q(is_staff=True) | Q(is_superuser=True)).distinct()
