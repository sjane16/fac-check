from django.shortcuts import render, get_object_or_404, redirect
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.urls import reverse
from requests import request
from django.db import transaction
from user.models import Report, SurveyResponse, Notification, invalidate_trusted_devices, UserProfile
from django.db.models import Count, Q, Sum
from django.core.cache import cache
from datetime import timedelta, datetime
import calendar
import json
import re
from django.conf import settings
from collections import defaultdict
from django.contrib import messages
from django.http import JsonResponse, HttpResponse
from django.core.paginator import Paginator
from django.template.loader import render_to_string
from django.views.decorators.http import require_POST
from user.forms import UserForm, ProfilePhotoForm, ProfilePasswordChangeForm, form_error_text
from security.audit import log_event
from django.contrib.auth import update_session_auth_hash
from xhtml2pdf import pisa
from user.views import link_callback
from user.notifications import notify_status_change, notifications_for_user, serialize_notification, NOTIFICATIONS_PAGE_SIZE
from user.context_processors import get_unread_notifications_count
from user.constants import PRIORITY_SLA_HOURS
from user.decorators import is_head_admin
from .models import Building 
from .forms import HeadAdminPersonnelRatingForm, ManualSurveySubmissionForm


REPORTS_PAGE_SIZE = 5
ANALYTICS_CACHE_SECONDS = 60

# Personnel roster: MED Personnel accounts (see adm/personnel.py)
from .personnel import personnel_groups, personnel_options, personnel_name_map


def build_personnel_rows(report):
    saved = report.assigned_personnel_data or []
    saved_names = {
        item.get("name")
        for item in saved
        if item.get("name")
    }

    active_names = {
        name: item['name'] for item in personnel_options()
        for name in [item['name'], *item['previous_names']]
    }
    current_names = {active_names[name] for name in saved_names if name in active_names}
    groups = [
        {
            **group,
            "personnel": [
                {**item, "checked": item["name"] in current_names}
                for item in group["personnel"]
            ],
        }
        for group in personnel_groups()
    ]

    other_names = [
        name
        for name in saved_names
        if name not in active_names
    ]
    others = [{"name": name} for name in other_names]

    return groups, others


def personnel_assignment_context(report):
    groups, others = build_personnel_rows(report)
    selected_groups = []
    for group in groups:
        selected = [item for item in group['personnel'] if item['checked']]
        if selected:
            selected_groups.append({**group, 'personnel': selected})
    return {
        'personnel_groups': groups,
        'selected_personnel_groups': selected_groups,
        'other_personnel': others,
        'has_personnel': bool(report.assigned_personnel_data),
    }


@require_POST
def save_report_personnel(request, report_id):
    with transaction.atomic():
        report = get_object_or_404(Report.objects.select_for_update(), id=report_id)
        if report.status != 'pending':
            return JsonResponse({'ok': False, 'error': 'Personnel assignments can only be changed while the report is Pending.'}, status=409)

        selected = set(request.POST.getlist('personnel_names'))
        options = personnel_options()
        if selected - {item['slug'] for item in options}:
            return JsonResponse({'ok': False, 'error': 'One or more personnel are no longer available. Refresh the page and try again.'}, status=400)
        assignments = [{'name': item['name']} for item in options if item['slug'] in selected]
        if request.POST.get('include_other_personnel') == '1':
            for value in request.POST.getlist('other_personnel'):
                name = value.strip()
                if len(name) > 100:
                    return JsonResponse({'ok': False, 'error': 'Other personnel names must not exceed 100 characters.'}, status=400)
                if name and name.casefold() not in {item['name'].casefold() for item in assignments}:
                    assignments.append({'name': name})
        report.assigned_personnel_data = assignments
        report.save(update_fields=['assigned_personnel_data'])

    context = personnel_assignment_context(report)
    return JsonResponse({
        'ok': True,
        'html': render_to_string('partials/_selected_personnel.html', {
            **context, 'can_edit_personnel': True,
        }, request=request),
        'selected_slugs': [item['slug'] for group in context['selected_personnel_groups'] for item in group['personnel']],
        'other_names': [item['name'] for item in context['other_personnel']],
    })


def is_med_personnel_report(report):
    profile = getattr(report.user, 'profile', None) if report.user_id else None
    return bool(profile and profile.role == 'med_personnel')


def format_hours(hours):
    if hours is None:
        return "-"
    if hours < 24:
        return f"{hours:g} hours"
    days = hours / 24
    if days.is_integer():
        return f"{int(days)} days"
    return f"{days:.1f} days"


RESPONSES_PAGE_SIZE = 25



def _fmt_hours(h):
    """Format oras: N/A, minutes (<1h), hours (<=48h), o days (>48h)."""
    if h is None:
        return 'N/A'
    if h < 1:
        return f"{round(h * 60)} min"
    if h <= 48:
        return f"{h:.1f} h"
    return f"{h / 24:.1f} days"


def _delay_score(delay_hours):
    """Convert an elapsed-delay (in hours, already clamped to >= 0) into a 1-5 score."""
    if delay_hours is None:
        return None
    if delay_hours <= 0:
        return 5
    delay_days = delay_hours / 24
    if delay_days <= 2:
        return 4
    if delay_days <= 5:
        return 3
    if delay_days <= 7:
        return 2
    return 1


def _percent_to_score(pct):
    """Convert a completion percentage into a 1-5 score."""
    if pct >= 100:
        return 5
    if pct >= 90:
        return 4
    if pct >= 80:
        return 3
    if pct >= 70:
        return 2
    return 1


def _known_personnel_names(assignments):
    """Filter assignment dicts down to names that match the official MED personnel roster
    (excludes outsourced/typed-in "other personnel")."""
    known = personnel_name_map()
    return list(dict.fromkeys(known[item.get('name')] for item in assignments if item.get('name') in known))


def _survey_responses_queryset():
    personnel_names = personnel_name_map()
    if not personnel_names:
        # An empty Q() would match every survey; with no MED personnel there's nothing to show
        return SurveyResponse.objects.none()
    assignments = Q()
    for name in personnel_names:
        assignments |= Q(report__assigned_personnel_data__contains=[{"name": name}])

    return (
        SurveyResponse.objects
        .filter(assignments)
        .select_related('report', 'rated_by')
        .order_by('-submitted_at')
    )


def _build_survey_response(survey):
    report = survey.report
    assignments = report.assigned_personnel_data or []
    personnel_names = _known_personnel_names(assignments)
    threshold = PRIORITY_SLA_HOURS.get(report.priority, PRIORITY_SLA_HOURS['standard'])

    expected_resolution_met = None
    resolution_delay_days = None
    if report.in_progress_at and report.accomplished_at:
        sla_hours = (report.accomplished_at - report.in_progress_at).total_seconds() / 3600
        delay_hours = max(0, sla_hours - threshold)
        expected_resolution_met = _delay_score(delay_hours) >= 4
        resolution_delay_days = round(delay_hours / 24) if delay_hours > 0 else 0

    closure_turnaround_met = None
    closure_delay_days = None
    if report.accomplished_at and survey.submitted_at:
        close_hours = (survey.submitted_at - report.accomplished_at).total_seconds() / 3600
        delay_hours = max(0, close_hours)
        closure_turnaround_met = _delay_score(delay_hours) >= 4
        closure_delay_days = round(delay_hours / 24) if delay_hours > 0 else 0

    return {
        "personnel": ", ".join(personnel_names),
        "rating_source": survey.get_rating_source_display(),
        "rated_by": survey.rated_by.get_full_name() if survey.rated_by_id else "Client",
        "category": report.get_category_display(),
        "quality": survey.quality,
        "quantity": survey.quantity,
        "timeliness": survey.timeliness,
        "date_submitted": report.submitted_at,
        "expected_at": report.expected_resolution_at,
        "actual_at": report.accomplished_at,
        "expected_resolution_met": expected_resolution_met,
        "resolution_delay_days": resolution_delay_days,
        "closure_turnaround_met": closure_turnaround_met,
        "closure_delay_days": closure_delay_days,
    }


def build_rating_responses_list(page_number=1):
    """Return one SQL-paginated page of personnel-assigned survey responses."""
    paginator = Paginator(_survey_responses_queryset(), RESPONSES_PAGE_SIZE)
    page = paginator.get_page(page_number)
    return [_build_survey_response(survey) for survey in page.object_list], page


def build_rating_analytics_context(response_page=1, include_responses=True):
    aggregates = {
        "total_surveys": Count('id'),
        "quality_sum": Sum('quality'),
        "quantity_sum": Sum('quantity'),
        "timeliness_sum": Sum('timeliness'),
        "quality_count": Count('quality'),
        "quantity_count": Count('quantity'),
        "timeliness_count": Count('timeliness'),
    }
    for rating in range(1, 6):
        aggregates[f"rating_{rating}"] = (
            Count('id', filter=Q(quality=rating))
            + Count('id', filter=Q(quantity=rating))
            + Count('id', filter=Q(timeliness=rating))
        )
    survey_stats = SurveyResponse.objects.filter(rating_source='client').aggregate(**aggregates)
    score_count = sum(survey_stats[f"{field}_count"] for field in ('quality', 'quantity', 'timeliness'))
    score_sum = sum(survey_stats[f"{field}_sum"] or 0 for field in ('quality', 'quantity', 'timeliness'))
    total_surveys = survey_stats['total_surveys']
    avg_rating = round(score_sum / score_count, 1) if score_count else 0
    rating_counts = {
        rating: survey_stats[f"rating_{rating}"]
        for rating in range(1, 6)
    }
    max_rating_count = max(rating_counts.values()) if rating_counts else 0
    rating_rows = []
    for rating in range(5, 0, -1):
        count = rating_counts.get(rating, 0)
        percent = round((count / max_rating_count) * 100) if max_rating_count else 0
        rating_rows.append({"rating": rating, "count": count, "percent": percent})

    # ---- Personnel Performance (QQT) — official personnel only, based on ALL assigned reports ----
    personnel_stats = {
        option["name"]: {
            "name": option["name"],
            "total_assigned": 0,
            "closed_count": 0,
            "quality_total": 0,
            "quantity_total": 0,
            "timeliness_total": 0,
            "quality_count": 0,
            "quantity_count": 0,
            "timeliness_count": 0,
            "categories": set(),
            "timeliness_scores": [],
            "qqt_reports": 0,
            "qqt_surveyed": 0,
            "qqt_quality_sum": 0,
            "qqt_quantity_sum": 0,
            "qqt_timeliness_sum": 0,
        }
        for option in personnel_options(include_inactive=True)
    }

    for report in Report.objects.select_related('survey').iterator(chunk_size=500):
        assignments = report.assigned_personnel_data or []
        names = _known_personnel_names(assignments)
        if not names:
            continue

        for name in names:
            stats = personnel_stats[name]
            stats["total_assigned"] += 1

            # QQT tally: bilang ng reports = resolved OR closed
            if report.status in ('resolved', 'closed'):
                stats["qqt_reports"] += 1
                qqt_survey = getattr(report, 'survey', None)
                if qqt_survey:
                    stats["qqt_surveyed"] += 1
                    stats["qqt_quality_sum"] += qqt_survey.quality or 0
                    stats["qqt_quantity_sum"] += qqt_survey.quantity or 0
                    stats["qqt_timeliness_sum"] += qqt_survey.timeliness or 0

            if report.status != 'closed':
                continue

            stats["closed_count"] += 1
            stats["categories"].add(report.get_category_display())

            survey = getattr(report, 'survey', None)
            if survey:
                for field in ('quality', 'quantity', 'timeliness'):
                    value = getattr(survey, field)
                    if value is not None:
                        stats[f"{field}_total"] += value
                        stats[f"{field}_count"] += 1

            threshold = PRIORITY_SLA_HOURS.get(report.priority, PRIORITY_SLA_HOURS['standard'])

            sla_score = None
            if report.in_progress_at and report.accomplished_at:
                sla_hours = (report.accomplished_at - report.in_progress_at).total_seconds() / 3600
                sla_score = _delay_score(max(0, sla_hours - threshold))

            close_score = None
            if report.accomplished_at and report.closed_at:
                close_hours = (report.closed_at - report.accomplished_at).total_seconds() / 3600
                close_score = _delay_score(max(0, close_hours))

            report_scores = [s for s in (sla_score, close_score) if s is not None]
            if report_scores:
                stats["timeliness_scores"].append(sum(report_scores) / len(report_scores))

    personnel_rows = []
    for stats in personnel_stats.values():
        quality = round(stats["quality_total"] / stats["quality_count"], 1) if stats["quality_count"] else 0
        quantity_survey = round(stats["quantity_total"] / stats["quantity_count"], 1) if stats["quantity_count"] else 0

        total_assigned = stats["total_assigned"]
        completion_pct = (stats["closed_count"] / total_assigned * 100) if total_assigned else 0
        quantity = quantity_survey or (_percent_to_score(completion_pct) if stats["closed_count"] else 0)

        timeliness_scores = stats["timeliness_scores"]
        timeliness_avg = (sum(timeliness_scores) / len(timeliness_scores)) if timeliness_scores else 0
        timeliness_survey = round(stats["timeliness_total"] / stats["timeliness_count"], 1) if stats["timeliness_count"] else 0
        timeliness = timeliness_survey or round(timeliness_avg, 1)

        scores = [score for score in (quality, quantity, timeliness) if score]
        personnel_avg_rating = round(sum(scores) / len(scores), 2) if scores else 0

        # QQT = Σ score / number of reports (resolved + closed); None -> "-" kapag walang survey
        qqt_reports = stats["qqt_reports"]
        if qqt_reports and stats["qqt_surveyed"]:
            qqt_quality = stats["qqt_quality_sum"] / qqt_reports
            qqt_quantity = stats["qqt_quantity_sum"] / qqt_reports
            qqt_timeliness = stats["qqt_timeliness_sum"] / qqt_reports
            qqt_avg = (qqt_quality + qqt_quantity + qqt_timeliness) / 3
        else:
            qqt_quality = qqt_quantity = qqt_timeliness = qqt_avg = None

        personnel_rows.append({
            "name": stats["name"],
            "avg_rating": qqt_avg,
            "total_score": round(personnel_avg_rating, 1) if scores else None,
            "total_surveys": max(stats["quality_count"], stats["quantity_count"], stats["timeliness_count"]),
            "complete_task": qqt_reports,
            "quality": qqt_quality,
            "quantity": qqt_quantity,
            "timeliness": qqt_timeliness,
        })

    personnel_rows.sort(
        key=lambda row: row["avg_rating"] if row["avg_rating"] is not None else -1,
        reverse=True,
    )
    # (uses the dict key "avg_rating", unaffected by the personnel_avg_rating rename above)

    # ---- Individual Responses — official personnel only (outsourced-only reports are hidden) ----
    responses = []
    responses_page = None
    if include_responses:
        responses, responses_page = build_rating_responses_list(response_page)

    return {
        "active_page": "rating_analytics",
        "avg_rating": avg_rating,
        "total_surveys": total_surveys,
        "rating_counts": rating_counts,
        "rating_rows": rating_rows,
        "max_rating_count": max_rating_count,
        "personnel_rows": personnel_rows,
        "responses": responses,
        "responses_page_number": responses_page.number if responses_page else 1,
        "responses_total_pages": responses_page.paginator.num_pages if responses_page else 1,
        "responses_has_previous": responses_page.has_previous() if responses_page else False,
        "responses_has_next": responses_page.has_next() if responses_page else False,
        "responses_total_count": responses_page.paginator.count if responses_page else 0,
    }

def dashboard(request):
    period = request.GET.get('period', '30')

    return render(request, 'dashboard.html', {
        'period': period,
    })


def _build_resolution_buckets(period, now, start_date=None, end_date=None):
    """
    Return ordered resolution buckets as tuples: (label, bucket_start, bucket_end).
    """
    local_now = timezone.localtime(now)
    buckets = []

    # Month picker mode: split selected month into fixed week ranges.
    if period is None and start_date is not None and end_date is not None:
        local_start = timezone.localtime(start_date)
        year, month = local_start.year, local_start.month
        last_day = calendar.monthrange(year, month)[1]
        month_periods = _pdf_week_periods(
            datetime(year, month, 1).date(),
            datetime(year, month, last_day).date(),
        )
        for p in month_periods:
            buckets.append((_fmt_period_label_short(p['start'], p['end']), p['start'], p['end']))
        return buckets

    # Last 7 Days: daily buckets.
    if period == '7':
        start_day = (local_now - timedelta(days=6)).date()
        for day_offset in range(7):
            day = start_day + timedelta(days=day_offset)
            bucket_start = timezone.make_aware(datetime(day.year, day.month, day.day, 0, 0, 0))
            bucket_end = timezone.make_aware(datetime(day.year, day.month, day.day, 23, 59, 59))
            buckets.append((day.strftime('%b %d'), bucket_start, bucket_end))
        return buckets

    # Last 30 Days: 4 weekly buckets going backward from today.
    if period == '30':
        for week_index in range(4, 0, -1):
            start_delta = week_index * 7 - 1
            end_delta = (week_index - 1) * 7
            start_day = (local_now - timedelta(days=start_delta)).date()
            end_day = (local_now - timedelta(days=end_delta)).date()
            bucket_start = timezone.make_aware(datetime(start_day.year, start_day.month, start_day.day, 0, 0, 0))
            bucket_end = timezone.make_aware(datetime(end_day.year, end_day.month, end_day.day, 23, 59, 59))
            buckets.append((_fmt_period_label_short(bucket_start, bucket_end), bucket_start, bucket_end))
        return buckets

    # All Time: monthly buckets (last 12 months).
    if period == 'all':
        year = local_now.year
        month = local_now.month
        month_pairs = []

        for _ in range(12):
            month_pairs.append((year, month))
            month -= 1
            if month == 0:
                month = 12
                year -= 1
        month_pairs.reverse()

        for bucket_year, bucket_month in month_pairs:
            last_day = calendar.monthrange(bucket_year, bucket_month)[1]
            bucket_start = timezone.make_aware(datetime(bucket_year, bucket_month, 1, 0, 0, 0))
            bucket_end = timezone.make_aware(datetime(bucket_year, bucket_month, last_day, 23, 59, 59))
            label = datetime(bucket_year, bucket_month, 1).strftime('%b %Y')
            buckets.append((label, bucket_start, bucket_end))
        return buckets

    # Fallback: one bucket covering the explicit range if present.
    if start_date is not None and end_date is not None:
        buckets.append(('Range', start_date, end_date))
    return buckets


def _reports_for_resolution_buckets(buckets):
    reports_by_bucket = [[] for _ in buckets]
    if not buckets:
        return reports_by_bucket

    finished_reports = Report.objects.filter(
        status__in=('resolved', 'closed'),
        accomplished_at__range=(buckets[0][1], buckets[-1][2]),
        submitted_at__isnull=False,
    ).values_list('priority', 'submitted_at', 'accomplished_at')

    for priority, submitted_at, accomplished_at in finished_reports.iterator(chunk_size=500):
        for index, (_, bucket_start, bucket_end) in enumerate(buckets):
            if bucket_start <= accomplished_at <= bucket_end:
                reports_by_bucket[index].append((priority, submitted_at, accomplished_at))
                break

    return reports_by_bucket


def _build_dashboard_data(period, start_date=None, end_date=None):
    now = timezone.now()

    # If caller didn't provide explicit start/end dates, derive from period
    if start_date is None and end_date is None:
        if period == '7':
            start_date = now - timedelta(days=7)
            end_date = now
        elif period == '30':
            start_date = now - timedelta(days=30)
            end_date = now
        else:
            start_date = None
            end_date = None

    def filter_periods(qs):
        # Support both open-ended (>= start) and closed ranges (start..end)
        if start_date and end_date:
            return qs.filter(submitted_at__range=(start_date, end_date))
        if start_date:
            return qs.filter(submitted_at__gte=start_date)
        return qs.all()

    survey_aggregates = {
        f'{field}_sum': Sum(field)
        for field in ('quality', 'quantity', 'timeliness')
    }
    survey_aggregates.update({
        f'{field}_count': Count(field)
        for field in ('quality', 'quantity', 'timeliness')
    })
    for rating in range(1, 6):
        survey_aggregates[f'rating_{rating}'] = (
            Count('id', filter=Q(quality=rating))
            + Count('id', filter=Q(quantity=rating))
            + Count('id', filter=Q(timeliness=rating))
        )
    survey_scores = SurveyResponse.objects.filter(rating_source='client').aggregate(**survey_aggregates)
    score_count = sum(survey_scores[f'{field}_count'] for field in ('quality', 'quantity', 'timeliness'))
    score_sum = sum(survey_scores[f'{field}_sum'] or 0 for field in ('quality', 'quantity', 'timeliness'))
    avg_satisfaction = round(score_sum / score_count, 1) if score_count else 0

    status_counts_qs = (
        filter_periods(Report.objects)
        .values('status')
        .annotate(count=Count('id'))
    )
    status_map = dict(Report.STATUS_CHOICES)
    status_counts = {status_map[s['status']]: s['count'] for s in status_counts_qs}

    status_labels = [status_map[code] for code, _ in Report.STATUS_CHOICES]
    status_data = [status_counts.get(label, 0) for label in status_labels]

    satisfaction_labels = [
        "5-Outstanding",
        "4-Very Satisfactory",
        "3-Satisfactory",
        "2-Fair",
        "1-Unsatisfactory"
    ]   
    # Per-score tally across Quality, Quantity, Timeliness (same pooling as avg_satisfaction)
    satisfaction_counts = [survey_scores[f'rating_{rating}'] for rating in range(5, 0, -1)]

    categories_data = (
        filter_periods(Report.objects)
        .values('category')
        .annotate(count=Count('id'))
        .order_by('-count')
        # [:6]
    )
    # label_map = dict(Report.CATEGORY_CHOICES)
    # categories = [label_map.get(c['category'], c['category']) for c in categories_data]
    # categories_counts = [c['count'] for c in categories_data]

    # Look up the report count for each category that has reports.
    category_count_map = {
        item['category']: item['count']
        for item in categories_data
    }

    # Include every category, then sort by report count.
    sorted_categories = sorted(
        Report.CATEGORY_CHOICES,
        key=lambda category: category_count_map.get(category[0], 0),
        reverse=True,
    )

    categories = [
        label for code, label in sorted_categories
    ]

    categories_counts = [
        category_count_map.get(code, 0)
        for code, label in sorted_categories
    ]

    building_status_counts = defaultdict(
        lambda: {code: 0 for code, _ in Report.STATUS_CHOICES}
    )

    # Include every building, even if it has no reports.
    for name in Building.objects.order_by('name').values_list('name', flat=True):
        building_status_counts[name.strip()] = {
            code: 0 for code, _ in Report.STATUS_CHOICES
        }

    for status, building in filter_periods(Report.objects).values_list('status', 'building_name').iterator(chunk_size=500):
        if building:
            building_status_counts[building][status] += 1

    sorted_buildings = sorted(
        building_status_counts.items(),
        key=lambda item: sum(item[1].values()),
        reverse=True,
    )
    # [:8]
    building_labels = [building for building, _ in sorted_buildings]
    building_status_data = {
        code: [counts[code] for _, counts in sorted_buildings]
        for code, _ in Report.STATUS_CHOICES
    }

    resolution_weeks = []
    resolution_by_priority = {
        code: {
            'label': label,
            'target': PRIORITY_SLA_HOURS[code],
            'expected': [],
            'actual': [],
            'counts': [],
        }
        for code, label in Report.PRIORITY_CHOICES
    }
    sla_total = 0
    sla_met = 0

    resolution_buckets = _build_resolution_buckets(
        period,
        now,
        start_date=start_date,
        end_date=end_date,
    )

    reports_by_bucket = _reports_for_resolution_buckets(resolution_buckets)
    for (bucket_label, _, _), finished in zip(resolution_buckets, reports_by_bucket):
        resolution_weeks.append(bucket_label)
        priority_actuals = {code: [] for code in resolution_by_priority}
        for priority, submitted_at, accomplished_at in finished:
            diff = (accomplished_at - submitted_at).total_seconds() / 3600
            if diff < 0:
                continue  # invalid: natapos bago ma-submit
            target = PRIORITY_SLA_HOURS.get(priority, PRIORITY_SLA_HOURS['standard'])
            sla_total += 1
            if diff <= target:
                sla_met += 1
            if priority in priority_actuals:
                priority_actuals[priority].append(diff)

        for code, series in resolution_by_priority.items():
            values = priority_actuals[code]
            series['expected'].append(series['target'])
            series['actual'].append(
                round(sum(values) / len(values), 2) if values else None
            )
            series['counts'].append(len(values))

    sla_within_pct = round(sla_met / sla_total * 100, 1) if sla_total else None

    return {
        'avg_satisfaction': avg_satisfaction,
        'satisfaction_labels': satisfaction_labels,
        'satisfaction_counts': satisfaction_counts,
        'categories': categories,
        'categories_counts': categories_counts,
        'building_labels': building_labels,
        'building_status_data': building_status_data,
        'resolution_weeks': resolution_weeks,
        'resolution_by_priority': resolution_by_priority,
        'sla_total': sla_total,
        'sla_met': sla_met,
        'sla_within_pct': sla_within_pct,
        'status_labels': status_labels,
        'status_data': status_data,
    }


def _cached_dashboard_data(period, start_date=None, end_date=None):
    key = 'dashboard-data:' + ':'.join((
        str(period),
        start_date.isoformat() if start_date else '',
        end_date.isoformat() if end_date else '',
    ))
    return cache.get_or_set(
        key,
        lambda: _build_dashboard_data(period, start_date, end_date),
        ANALYTICS_CACHE_SECONDS,
    )


def adm_dashboard_ajax(request):
    period = request.GET.get('period', '30')
    report_month = (request.GET.get('report_month') or '').strip()

    if report_month:
        try:
            start_dt, end_dt = _parse_report_month(report_month)
        except ValueError:
            return JsonResponse({'error': 'Invalid report_month format. Expected YYYY-MM.'}, status=400)
        data = _cached_dashboard_data(None, start_date=start_dt, end_date=end_dt)
    else:
        data = _cached_dashboard_data(period)

    html = render_to_string(
        'partials/_dashboard_content.html',
        {
            'avg_satisfaction': data['avg_satisfaction'],
            'period': period,
            'sla_within_pct': data['sla_within_pct'],
            'sla_met': data['sla_met'],
            'sla_total': data['sla_total'],
        },
        request=request,
    )

    today = timezone.localtime(timezone.now()).date()
    last_day = today.replace(day=calendar.monthrange(today.year, today.month)[1])

    return JsonResponse({
        'html': html,
        'can_generate_report': is_head_admin(request.user),
        'report_available_on': last_day.strftime('%b %d, %Y'),
        **data,
    })


def _filter_admin_reports(status_filter, search_query):
    reports_qs = Report.objects.all().order_by('-submitted_at')

    if status_filter:
        reports_qs = reports_qs.filter(status=status_filter)

    if search_query:
        query = search_query.strip()

        q_filter = (
            Q(job_order_id__icontains=query)
            | Q(location__icontains=query)
            | Q(category__icontains=query)
            | Q(custom_category__icontains=query)
            | Q(assigned_personnel_data__icontains=query)
        )

        matching_statuses = [
            code for code, label in Report.STATUS_CHOICES
            if query.lower() in label.lower()
        ]
        if matching_statuses:
            q_filter |= Q(status__in=matching_statuses)

        matching_priorities = [
            code for code, label in Report.PRIORITY_CHOICES
            if query.lower() in label.lower()
        ]
        if matching_priorities:
            q_filter |= Q(priority__in=matching_priorities)

        matching_categories = [
            code for code, label in Report.CATEGORY_CHOICES
            if query.lower() in label.lower()
        ]
        if matching_categories:
            q_filter |= Q(category__in=matching_categories)

        reports_qs = reports_qs.filter(q_filter)

    return reports_qs


def reports(request):
    status_filter = request.GET.get('status', '')
    search_query = request.GET.get('search', '').strip()

    reports_qs = _filter_admin_reports(status_filter, search_query)

    paginator = Paginator(reports_qs, REPORTS_PAGE_SIZE)
    page_obj = paginator.get_page(1)

    return render(request, 'reports.html', {
        "active_page": "reports",
        "reports": page_obj,
        "status_filter": status_filter,
        "search_query": search_query,
        "total_count": Report.objects.count(),
        "filtered_count": paginator.count,
    })


def adm_reports_ajax(request):
    status_filter = request.GET.get('status', '')
    search_query = request.GET.get('search', '').strip()
    page_number = request.GET.get('page', 1)

    reports_qs = _filter_admin_reports(status_filter, search_query)

    paginator = Paginator(reports_qs, REPORTS_PAGE_SIZE)
    page_obj = paginator.get_page(page_number)

    mobile_html = render_to_string('partials/_reports_mobile_rows.html', {
        'reports': page_obj,
    })

    desktop_html = render_to_string('partials/_reports_desktop_rows.html', {
        'reports': page_obj,
    })

    return JsonResponse({
        'mobile_html': mobile_html,
        'desktop_html': desktop_html,
        'current_page': page_obj.number,
        'total_pages': paginator.num_pages,
        'has_previous': page_obj.has_previous(),
        'has_next': page_obj.has_next(),
        'total_count': paginator.count,
    })


def report_detail(request, report_id):
    report = get_object_or_404(
        Report.objects.select_related('user__profile', 'survey__rated_by'),
        id=report_id,
    )
    personnel_context = personnel_assignment_context(report)
    med_report = is_med_personnel_report(report)
    has_med_assignees = bool(_known_personnel_names(report.assigned_personnel_data or []))
    rating_success_personnel = request.session.pop(f'med_personnel_rating_success_{report.id}', '')
    is_locked = report.status in ('resolved', 'closed')

    if report.status == 'closed':
        session_key = f'closed_seen_{report.id}'
        if not request.session.get(session_key):
            messages.success(request, f"Report {report.job_order_id} has been Closed.")
            request.session[session_key] = True

    return render(request, 'report_detail.html', {
        'report': report,
        'active_page': 'reports',
        **personnel_context,
        'can_edit_personnel': report.status == 'pending',
        'is_locked': is_locked,
        'is_med_personnel_report': med_report,
        'has_med_assignees': has_med_assignees,
        'can_rate_med_personnel': med_report and has_med_assignees and report.status == 'resolved' and is_head_admin(request.user) and not hasattr(report, 'survey'),
        'qqt_emojis': [
            ('😤', 'Unsatisfactory', 1),
            ('😞', 'Fair', 2),
            ('😐', 'Satisfactory', 3),
            ('😊', 'Very Satisfactory', 4),
            ('😄', 'Outstanding', 5),
        ],
        'qqt_questions': (
            ('quality', 'Quality of Work'),
            ('quantity', 'Quantity of Work'),
            ('timeliness', 'Timeliness of Service'),
        ),
        'rating_success_personnel': rating_success_personnel,
        'priority_sla_hours': PRIORITY_SLA_HOURS,
    })


def record_manual_survey(request, report_id):
    get_object_or_404(Report, id=report_id)
    if request.method == 'POST':
        form = ManualSurveySubmissionForm(request.POST, request.FILES)
        # Lock the report like submitSurvey / record_med_personnel_rating do, so an admin recording the
        # paper survey and a client submitting online at the same moment can't both get through
        # (the loser used to hit an IntegrityError and a 500 page).
        with transaction.atomic():
            report = Report.objects.select_for_update().get(id=report_id)
            if is_med_personnel_report(report):
                messages.error(request, 'MED personnel reports must be rated by the Head Admin.')
            elif report.status != 'resolved' or report.survey_method != 'manual' or SurveyResponse.objects.filter(report=report).exists():
                messages.error(request, 'A manual survey can only be recorded once for a resolved report selected for manual submission.')
            elif form.is_valid():
                SurveyResponse.objects.create(report=report, **form.cleaned_data)
                report.status = 'closed'
                report.save(update_fields=['status'])
                request.session[f'closed_seen_{report.id}'] = True
                notify_status_change(report, 'closed', f'Your report {report.job_order_id} is now Closed. Thank you for your feedback!')
                messages.success(request, f'Report {report.job_order_id} survey recorded and report closed.')
            else:
                messages.error(request, 'Please upload a valid JPG or PNG scan and select all three ratings.')
    return redirect('adm_report_detail', report_id=report_id)


def record_med_personnel_rating(request, report_id):
    if request.method == 'POST':
        form = HeadAdminPersonnelRatingForm(request.POST)
        with transaction.atomic():
            report = get_object_or_404(
                Report.objects.select_for_update(of=('self',)).select_related('user__profile'),
                id=report_id,
            )
            assigned_names = _known_personnel_names(report.assigned_personnel_data or [])
            if not is_med_personnel_report(report):
                messages.error(request, 'Only reports submitted by MED personnel use Head Admin ratings.')
            elif report.status != 'resolved' or SurveyResponse.objects.filter(report=report).exists():
                messages.error(request, 'A Head Admin rating can only be recorded once for a resolved MED personnel report.')
            elif not assigned_names:
                messages.error(request, 'Assign at least one MED personnel before recording the group rating.')
            elif form.is_valid():
                SurveyResponse.objects.create(
                    report=report,
                    rated_by=request.user,
                    rating_source='head_admin',
                    **form.cleaned_data,
                )
                report.status = 'closed'
                report.save(update_fields=['status'])
                request.session[f'closed_seen_{report.id}'] = True
                request.session[f'med_personnel_rating_success_{report.id}'] = report.assigned_personnel_summary or ''
                notify_status_change(report, 'closed', f'Report {report.job_order_id} is now Closed after Head Admin QQT evaluation.')
            else:
                messages.error(request, 'Please select a valid 1–5 score for all three QQT ratings.')
    return redirect('adm_report_detail', report_id=report_id)


def export_job_order_pdf_admin(request, report_id):
    report = get_object_or_404(
        Report.objects.select_related('user__profile', 'survey'),
        id=report_id,
    )

    if report.status not in ('in_progress', 'resolved', 'closed'):
        return HttpResponse(
            "Job order printing is available once maintenance is in progress.",
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
        "is_med_personnel_report": is_med_personnel_report(report),
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


def export_personnel_performance_pdf(request):
    context = build_rating_analytics_context(include_responses=False)
    context.pop("responses", None)

    html = render_to_string('personnel_performance_pdf.html', context)

    response = HttpResponse(content_type='application/pdf')
    filename = f"Personnel_Performance_QQT_{timezone.now().strftime('%Y-%m-%d')}.pdf"
    response['Content-Disposition'] = f'inline; filename="{filename}"'
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
        return HttpResponse("Something went wrong generating the Personnel Performance PDF.", status=500)

    return response


DASHBOARD_CHART_PATTERN = re.compile(r'^data:image/png;base64,[A-Za-z0-9+/=]+$')
DASHBOARD_CHART_MAX_LEN = 1_500_000
DASHBOARD_SUMMARY_MAX_LEN = 3000


def _is_last_day_of_month(day):
    return day.day == calendar.monthrange(day.year, day.month)[1]


def _clean_chart_image(value):
    """Accept only a small PNG data URI, so pisa never fetches arbitrary paths/URLs."""
    if not value or len(value) > DASHBOARD_CHART_MAX_LEN:
        return None
    return value if DASHBOARD_CHART_PATTERN.match(value) else None


def _join_and(items):
    items = list(items)
    if len(items) <= 1:
        return ''.join(items)
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return f"{', '.join(items[:-1])}, and {items[-1]}"


def _build_report_summary(data, period_start, period_end, priority_tables=None):
    """Plain-text summary paragraph (system-suggested; admin can edit before PDF)."""
    fmt = lambda d: f"{d:%B} {d.day}, {d:%Y}"
    parts = [
        "This report summarizes the corrective maintenance reports received by the "
        "Maintenance and Engineering Division of Universidad de Manila from "
        f"{fmt(period_start)} to {fmt(period_end)}, based on the analytics of the Admin Dashboard."
    ]

    total = sum(data['status_data'])
    if total:
        status_bits = [
            f"{count} ({count / total * 100:.1f}%) {label.lower()}"
            for label, count in zip(data['status_labels'], data['status_data'])
        ]
        parts.append(
            f"During this period, a total of {total} report{'' if total == 1 else 's'} "
            f"{'was' if total == 1 else 'were'} received. The reports are distributed by "
            f"status as follows: {_join_and(status_bits)}, as shown in Figure 1."
        )
    else:
        parts.append("No reports were received during this period.")

    category_total = sum(data['categories_counts'])
    active_categories = [
        (label, count) for label, count in zip(data['categories'], data['categories_counts'])
        if count > 0
    ]
    if active_categories:
        pct = lambda c: f"{c / category_total * 100:.1f}"
        top_label, top_count = active_categories[0]
        text = (
            f"By category, the {top_label} category accounted for the largest share of "
            f"reports with {top_count} ({pct(top_count)}%)"
        )
        rest = active_categories[1:3]
        if rest:
            text += ", followed by " + _join_and(
                f"the {label} category with {count} ({pct(count)}%)" for label, count in rest
            )
        text += "."
        others = active_categories[3:]
        if others:
            text += " The remaining reports were classified under " + _join_and(
                f"{label} ({count})" for label, count in others
            ) + "."
        text += " The distribution of reports by category is shown in Figure 2."
        parts.append(text)

    building_totals = []
    for index, name in enumerate(data['building_labels']):
        b_total = sum(
            data['building_status_data'][code][index]
            for code, _ in Report.STATUS_CHOICES
        )
        if b_total > 0:
            building_totals.append((name, b_total))
    if building_totals:
        text = (
            f"By location, {building_totals[0][0]} recorded the highest number of reports "
            f"with {building_totals[0][1]}"
        )
        rest = building_totals[1:3]
        if rest:
            text += ", followed by " + _join_and(f"{n} with {t}" for n, t in rest)
        text += ". The breakdown of reports per building by status is presented in Table 1."
        parts.append(text)

    if priority_tables is None:
        priority_tables = _cached_pdf_priority_tables(period_start, period_end)

    text = (
        "With respect to resolution time, the Division's target turnaround depends on the "
        f"priority of the report: {PRIORITY_SLA_HOURS['critical']} hours for Critical/Urgent, "
        f"{PRIORITY_SLA_HOURS['high']} hours for High Priority, "
        f"and {PRIORITY_SLA_HOURS['standard']} hours for Standard."
    )
    completed_tables = [t for t in priority_tables if t['completed']]
    if completed_tables:
        bits = [
            f"{t['label']} reports averaged {_fmt_hours(t['avg_actual'])} against the "
            f"{t['target_hours']}-hour target, with {t['met']} of {t['completed']} "
            f"meeting the target time"
            for t in completed_tables
        ]
        text += " During the reporting period, " + _join_and(bits) + "."
        text += (
            " The comparison of expected and actual resolution time per priority level "
            "and per period is shown in Tables 2 to 4."
        )
    else:
        text += (
            " No reports were completed during the reporting period; hence, there is no actual "
            "resolution time available for comparison, as shown in Tables 2 to 4."
        )
    parts.append(text)

    return "\n\n".join(parts)


def _parse_report_month(report_month):
    parsed = datetime.strptime(report_month, '%Y-%m')
    first_day = datetime(parsed.year, parsed.month, 1)
    last_day = calendar.monthrange(parsed.year, parsed.month)[1]
    last_dt = datetime(parsed.year, parsed.month, last_day, 23, 59, 59)
    return timezone.make_aware(first_day), timezone.make_aware(last_dt)


def _fmt_period_label(start, end):
    if start == end:
        return f"{start.strftime('%B')} {start.day}"
    if start.month == end.month:
        return f"{start.strftime('%B')} {start.day} - {end.day}"
    return f"{start.strftime('%B')} {start.day} - {end.strftime('%B')} {end.day}"


def _fmt_period_label_short(start, end):
    """Maikling label para sa web charts: 'Sep 10-16' o 'Sep 28 - Oct 4'."""
    if start.date() == end.date() if hasattr(start, 'date') else start == end:
        return f"{start.strftime('%b')} {start.day}"
    if start.month == end.month:
        return f"{start.strftime('%b')} {start.day}-{end.day}"
    return f"{start.strftime('%b')} {start.day} - {end.strftime('%b')} {end.day}"


def _pdf_week_periods(period_start, period_end):
    """Calendar weeks (Sunday-Saturday) na naka-clip sa period_start..period_end."""
    periods = []
    cursor = period_start
    while cursor <= period_end:
        days_to_sat = (5 - cursor.weekday()) % 7
        end_day = min(cursor + timedelta(days=days_to_sat), period_end)
        periods.append({
            'label': _fmt_period_label(cursor, end_day),
            'start': timezone.make_aware(datetime(cursor.year, cursor.month, cursor.day, 0, 0, 0)),
            'end': timezone.make_aware(datetime(end_day.year, end_day.month, end_day.day, 23, 59, 59)),
        })
        cursor = end_day + timedelta(days=1)
    return periods


PDF_PRIORITY_STYLES = [
    # (code, header color, row tint)
    ('critical', '#dc2626', '#fee2e2'),
    ('high', '#ea580c', '#ffedd5'),
    ('standard', '#2563eb', '#dbeafe'),
]


def _build_pdf_priority_tables(period_start, period_end):
    """Isang table kada priority: rows = periods, may Expected (target) at Actual (average)."""
    periods = _pdf_week_periods(period_start, period_end)
    priority_labels = dict(Report.PRIORITY_CHOICES)
    actuals_by_priority = {
        code: [[] for _ in periods]
        for code, _, _ in PDF_PRIORITY_STYLES
    }
    if periods:
        finished_reports = Report.objects.filter(
            status__in=('resolved', 'closed'),
            accomplished_at__range=(periods[0]['start'], periods[-1]['end']),
            submitted_at__isnull=False,
        ).values_list('priority', 'submitted_at', 'accomplished_at')

        for priority, submitted_at, accomplished_at in finished_reports.iterator(chunk_size=500):
            if priority not in actuals_by_priority:
                continue
            diff = (accomplished_at - submitted_at).total_seconds() / 3600
            if diff < 0:
                continue
            for index, period in enumerate(periods):
                if period['start'] <= accomplished_at <= period['end']:
                    actuals_by_priority[priority][index].append(diff)
                    break

    tables = []
    for code, color, tint in PDF_PRIORITY_STYLES:
        target = PRIORITY_SLA_HOURS[code]
        rows = []
        actual_lists = actuals_by_priority[code]
        all_actual = [hours for values in actual_lists for hours in values]
        for period, actual_list in zip(periods, actual_lists):
            avg = sum(actual_list) / len(actual_list) if actual_list else None
            rows.append({
                'period': period['label'],
                'expected_label': _fmt_hours(target),
                'actual_label': _fmt_hours(avg),
            })
        tables.append({
            'code': code,
            'label': priority_labels.get(code, code),
            'color': color,
            'tint': tint,
            'target_hours': target,
            'target_label': _fmt_hours(target),
            'rows': rows,
            'completed': len(all_actual),
            'met': sum(1 for h in all_actual if h <= target),
            'avg_actual': sum(all_actual) / len(all_actual) if all_actual else None,
        })
    return tables


def _cached_pdf_priority_tables(period_start, period_end):
    key = f'pdf-priority-tables:{period_start.isoformat()}:{period_end.isoformat()}'
    return cache.get_or_set(
        key,
        lambda: _build_pdf_priority_tables(period_start, period_end),
        ANALYTICS_CACHE_SECONDS,
    )


def export_dashboard_report_pdf(request):
    if request.method != 'POST':
        return HttpResponse("Invalid request.", status=405)

    now = timezone.localtime(timezone.now())
    today = now.date()

    # Allow admins to explicitly request any month via the "report_month" POST field (format: YYYY-MM).
    # If no month is specified, keep the original restriction: only allow generation on the last day
    # of the current month (unless DEBUG).
    report_month = (request.POST.get('report_month') or '').strip()
    start_dt = None
    end_dt = None
    if report_month:
        try:
            start_dt, end_dt = _parse_report_month(report_month)
        except ValueError:
            return HttpResponse("Invalid report_month format. Expected YYYY-MM.", status=400)

    if not settings.DEBUG and not report_month and not _is_last_day_of_month(today):
        return HttpResponse(
            "The monthly report can only be generated on the last day of the month.",
            status=403,
        )

    if report_month:
        data = _cached_dashboard_data(None, start_date=start_dt, end_date=end_dt)
        period_start = start_dt.date()
        period_end = end_dt.date()
    else:
        data = _cached_dashboard_data('30')
        period_end = today
        period_start = today - timedelta(days=30)

    priority_tables = _cached_pdf_priority_tables(period_start, period_end)

    # Admin's edited text wins (even if blank -> no SUMMARY section).
    # If the field wasn't sent at all, fall back to the system-generated summary.
    if 'summary_text' in request.POST:
        summary_text = request.POST.get('summary_text', '').strip()[:DASHBOARD_SUMMARY_MAX_LEN]
    else:
        summary_text = _build_report_summary(data, period_start, period_end, priority_tables)

    total = sum(data['status_data'])
    status_colors = ['#f59e0b', '#6366f1', '#10b981', '#64748b']
    category_colors = [
        '#3b82f6', '#8b5cf6', '#14b8a6', '#f59e0b',
        '#f43f5e', '#64748b', '#06b6d4',
    ]

    status_rows = [
        {
            'label': label,
            'count': count,
            'pct': round(count / total * 100, 1) if total else 0,
            'color': status_colors[index % len(status_colors)],
        }
        for index, (label, count) in enumerate(
            zip(data['status_labels'], data['status_data'])
        )
    ]

    category_total = sum(data['categories_counts'])
    category_rows = [
        {
            'label': label,
            'count': count,
            'pct': round(count / category_total * 100, 1) if category_total else 0,
            'color': category_colors[index % len(category_colors)],
        }
        for index, (label, count) in enumerate(
            zip(data['categories'], data['categories_counts'])
        )
    ]

    status_tints = ['#fef3c7', '#e0e7ff', '#d1fae5', '#e2e8f0']
    status_columns = [
        {
            'label': label,
            'color': status_colors[index % len(status_colors)],
        }
        for index, label in enumerate(data['status_labels'])
    ]

    building_rows = []
    for index, name in enumerate(data['building_labels']):
        counts = [
            data['building_status_data'][code][index]
            for code, _ in Report.STATUS_CHOICES
        ]
        cells = [
            {'value': count, 'tint': status_tints[i % len(status_tints)]}
            for i, count in enumerate(counts)
        ]
        building_rows.append({
            'name': name,
            'counts': counts,
            'cells': cells,
            'total': sum(counts),
        })

    active_categories = [row for row in category_rows if row['count'] > 0]
    top_categories = active_categories[:3]
    other_categories = active_categories[3:]
    top_buildings = [row for row in building_rows if row['total'] > 0][:3]

    html = render_to_string('dashboard_report_pdf.html', {
        'period_start': period_start,
        'period_end': period_end,
        'generated_at': now,
        'generated_by': request.user.get_full_name() or request.user.get_username(),
        'total_reports': total,
        'category_active_count': len(active_categories),
        'top_categories': top_categories,
        'other_categories': other_categories,
        'top_buildings': top_buildings,
        'summary_text': summary_text,
        'summary_paragraphs': [
            p.strip()
            for p in re.split(r'\n\s*\n', summary_text.replace('\r\n', '\n'))
            if p.strip()
        ],
        'status_labels': data['status_labels'],
        'status_columns': status_columns,
        'status_rows': status_rows,
        'category_rows': category_rows,
        'building_rows': building_rows,
        'priority_tables': priority_tables,
        'chart_status': _clean_chart_image(request.POST.get('chart_status')),
        'chart_categories': _clean_chart_image(request.POST.get('chart_categories')),
        'footer_text': f"Report Period: {period_start.strftime('%b %d, %Y')} to {period_end.strftime('%b %d, %Y')}  |  Generated: {now.strftime('%b %d, %Y %I:%M %p')}",
    })

    response = HttpResponse(content_type='application/pdf')
    filename = f"Dashboard_Report_{period_end.strftime('%Y-%m')}.pdf"
    response['Content-Disposition'] = f'inline; filename="{filename}"'
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
        return HttpResponse("Something went wrong generating the Dashboard Report PDF.", status=500)

    return response


def dashboard_report_summary(request):
    """Returns the system-suggested summary paragraph to prefill the editable textarea."""
    if request.method != 'GET':
        return JsonResponse({'error': 'Invalid request.'}, status=405)

    now = timezone.localtime(timezone.now())
    today = now.date()

    report_month = (request.GET.get('report_month') or '').strip()
    start_dt = None
    end_dt = None
    if report_month:
        try:
            start_dt, end_dt = _parse_report_month(report_month)
        except ValueError:
            return JsonResponse({'error': 'Invalid report_month format. Expected YYYY-MM.'}, status=400)

    if not settings.DEBUG and not report_month and not _is_last_day_of_month(today):
        return JsonResponse(
            {'error': 'The monthly report can only be generated on the last day of the month.'},
            status=403,
        )

    if report_month:
        data = _cached_dashboard_data(None, start_date=start_dt, end_date=end_dt)
        period_start = start_dt.date()
        period_end = end_dt.date()
    else:
        data = _cached_dashboard_data('30')
        period_end = today
        period_start = today - timedelta(days=30)

    return JsonResponse({'summary': _build_report_summary(data, period_start, period_end)})


@transaction.atomic
def report_save(request, report_id):
    report = get_object_or_404(Report.objects.select_for_update(), id=report_id)

    if request.method == 'POST':
        if report.status in ('resolved', 'closed'):
            messages.error(request, "This report can no longer be edited.")
            return redirect('adm_report_detail', report_id=report_id)

        category = request.POST.get('category', '').strip()
        custom_category = request.POST.get('custom_category', '').strip()
        priority = request.POST.get('priority', '').strip()

        awaiting_personnel = (
            request.POST.get('pending_awaiting_personnel') == '1'
            and bool(report.assigned_personnel_data)
        )
        awaiting_materials = request.POST.get('pending_awaiting_materials') == '1'
        awaiting_approval = request.POST.get('pending_awaiting_approval') == '1'
        now = timezone.now()

        valid_categories = dict(Report.CATEGORY_CHOICES)
        if category in valid_categories:
            report.category = category
            report.custom_category = custom_category if category == "others" else None

        report.findings = request.POST.get('findings', '').strip()
        report.recommendation = request.POST.get('recommendation', '').strip()

        report.priority = priority if priority else None
        # Personnel are saved independently by the Add/Edit modal.
        assignments = report.assigned_personnel_data or []

        # ── MATERIALS REQUIREMENT (Phase 3: Phase 1 save logic) ─────────────────
        mat_qtys = request.POST.getlist('mat_qty')
        mat_units = request.POST.getlist('mat_unit')
        mat_descs = request.POST.getlist('mat_desc')

        materials_rows = []
        incomplete_materials_row = False
        for qty, unit, desc in zip(mat_qtys, mat_units, mat_descs):
            qty = qty.strip()
            unit = unit.strip()
            desc = desc.strip()

            # Skip fully blank rows, and cap to 4 items.
            if not qty and not unit and not desc:
                continue

            if len(materials_rows) >= 4:
                break

            if not (qty and unit and desc):
                incomplete_materials_row = True
                continue

            materials_rows.append({
                'qty': qty,
                'unit': unit,
                'description': desc,
            })

        report.materials_required = materials_rows

        if awaiting_personnel and not report.pending_awaiting_personnel_at:
            report.pending_awaiting_personnel_at = now
        if awaiting_materials and not report.pending_awaiting_materials_at:
            report.pending_awaiting_materials_at = now
        if awaiting_approval and not report.pending_awaiting_approval_at:
            report.pending_awaiting_approval_at = now

        report.pending_awaiting_personnel = awaiting_personnel
        report.pending_awaiting_materials = awaiting_materials
        report.pending_awaiting_approval = awaiting_approval

        if not awaiting_personnel:
            report.pending_awaiting_personnel_at = None
        if not awaiting_materials:
            report.pending_awaiting_materials_at = None
        if not awaiting_approval:
            report.pending_awaiting_approval_at = None

        became_in_progress = False
        materials_ready = not incomplete_materials_row and (awaiting_materials or bool(materials_rows))

        if (
            report.status == 'pending'
            and report.priority
            and assignments
            and awaiting_personnel
            and report.findings
            and report.recommendation
            and materials_ready
        ):
            report.status = 'in_progress'
            became_in_progress = True

            if not report.in_progress_at:
                report.in_progress_at = now

        report.save()

        if became_in_progress:
            notify_status_change(report, 'in_progress', f"Your report {report.job_order_id} is now In Progress.")
            messages.success(request, f"Report {report.job_order_id} is now In Progress.")
        elif report.status == 'pending':
            missing = []
            if not report.priority:
                missing.append("Priority Level")
            if not assignments:
                missing.append("Assign Personnel (at least one)")
            if not awaiting_personnel:
                missing.append('"With Assigned Personnel"')
            if not report.findings:
                missing.append('Findings')
            if not report.recommendation:
                missing.append('Recommendation')
            if not awaiting_materials and not materials_rows:
                missing.append('Materials Requirement (at least one complete row)')
            if incomplete_materials_row:
                missing.append('all used Materials Requirement rows must include quantity, unit and item description')

            if missing:
                messages.warning(
                    request,
                    f"Report {report.job_order_id} is still Pending — missing: {', '.join(missing)}."
                )

    return redirect('adm_report_detail', report_id=report_id)



def report_status(request, report_id):
    report = get_object_or_404(Report, id=report_id)
    if request.method == 'POST':
        new_status = request.POST.get('status')
        # This endpoint only handles "In Progress -> Resolved". Pending -> In Progress happens in report_save
        # (once every requirement is filled in) and Closed happens through the survey flow, so any other
        # value is rejected instead of being saved.
        if new_status != 'resolved':
            messages.error(request, "Invalid status update.")
            return redirect('adm_report_detail', report_id=report_id)
        if new_status == 'resolved':
            # Only work that is in progress can be resolved. Without this, a pending report could skip
            # assignment, and a closed one could be re-resolved (overwriting its completion photo and
            # sending the client another survey request).
            if report.status != 'in_progress':
                messages.error(request, "Only reports that are In Progress can be marked Resolved.")
                return redirect('adm_report_detail', report_id=report_id)

            completion_photo = request.FILES.get('completion_photo')
            if not completion_photo:
                messages.error(request, "Please upload a photo proof of the completed repair.")
                return redirect('adm_report_detail', report_id=report_id)

            accomplished_at_raw = request.POST.get('accomplished_at')
            accomplished_at = parse_datetime(accomplished_at_raw) if accomplished_at_raw else None

            if accomplished_at is None:
                messages.error(request, "Please enter the Date and Time Completed.")
                return redirect('adm_report_detail', report_id=report_id)

            if timezone.is_naive(accomplished_at):
                accomplished_at = timezone.make_aware(accomplished_at)

            if accomplished_at < report.submitted_at:
                messages.error(request, "Date and Time Completed cannot be earlier than the Date Submitted.")
                return redirect('adm_report_detail', report_id=report_id)

            if report.in_progress_at and accomplished_at < report.in_progress_at.replace(second=0, microsecond=0):
                messages.error(request, "Date and Time Completed cannot be earlier than the Date In Progress.")
                return redirect('adm_report_detail', report_id=report_id)

            if accomplished_at > timezone.now():
                messages.error(request, "Date and Time Completed cannot be a future date/time.")
                return redirect('adm_report_detail', report_id=report_id)

            report.status = new_status
            report.accomplished_at = accomplished_at
            report.completion_photo = completion_photo
        report.save()

        notify_status_change(report, 'resolved', f"Your report {report.job_order_id} has been Resolved. Please take the Client Satisfaction Survey.")
        messages.success(request, f"Report {report.job_order_id} has been marked Resolved.")
    return redirect('adm_report_detail', report_id=report_id)

def ProfileMenu(request):
    is_post_render = request.method == 'POST'

    if request.method == 'POST':
        # Now uses UserForm to sanitize first_name/last_name
        if 'save_info' in request.POST:
            user_form = UserForm(request.POST, instance=request.user)
            if user_form.is_valid():
                user_form.save()
                messages.success(request, "Profile Updated Successfully")
                return redirect('ProfileMenu')
            else:
                # Plain-text error message (avoids raw HTML from form.errors)
                messages.error(request, form_error_text(user_form))

        elif 'save_photo' in request.POST:
            if 'profile_photo' not in request.FILES:
                messages.error(request, "Please select a photo to upload.")
            else:
                profile, _ = UserProfile.objects.get_or_create(user=request.user)
                photo_form = ProfilePhotoForm(request.POST, request.FILES, instance=profile)

                if photo_form.is_valid():
                    photo_form.save()
                    messages.success(request, "Profile Photo Updated Successfully")
                    return redirect('ProfileMenu')

                for errors in photo_form.errors.values():
                    for error in errors:
                        messages.error(request, error)

        elif 'remove_photo' in request.POST:
            profile = UserProfile.objects.filter(user=request.user).first()
            if profile and profile.profile_photo:
                profile.profile_photo.delete(save=True)
                messages.success(request, "Profile photo removed successfully")

            return redirect('ProfileMenu')

        elif 'change_password' in request.POST:
            password_form = ProfilePasswordChangeForm(request.user, request.POST)
            if password_form.is_valid():
                password_form.save()
                invalidate_trusted_devices(request.user)
                log_event(request, 'password_changed', user=request.user, where='Edit Profile')
                update_session_auth_hash(request, request.user)
                messages.success(request, "Password changed successfully.")
                return redirect('ProfileMenu')
            for error in password_form.non_field_errors():
                messages.error(request, error)

    return render(request, 'menu-admin.html', {
        "active_page": "ProfileMenu",
        "role": "Head Admin" if is_head_admin(request.user) else "Admin",
        "is_post_render": is_post_render,
    })

def adm_menu_ajax(request):
    html = render_to_string('partials/_menu_content.html', {
        "role": "Head Admin" if is_head_admin(request.user) else "Admin",
    }, request = request)

    return JsonResponse({"html": html})

def rating_analytics(request):
    return render(request, 'rating_analytics.html', {
        "active_page": "rating_analytics",
    })


def adm_rating_analytics_ajax(request):
    context = build_rating_analytics_context(
        response_page=request.GET.get('page', 1)
    )

    html = render_to_string('partials/_rating_analytics_content.html', context)

    return JsonResponse({
        'html': html,
    })


def adm_rating_responses_ajax(request):
    responses, page_obj = build_rating_responses_list(request.GET.get('page', 1))

    html = render_to_string('partials/_survey_responses_rows.html', {
        'responses': responses,
    })

    return JsonResponse({
        'html': html,
        'current_page': page_obj.number,
        'total_pages': page_obj.paginator.num_pages,
        'has_previous': page_obj.has_previous(),
        'has_next': page_obj.has_next(),
        'total_count': page_obj.paginator.count,
    })


def _filter_admin_notifications(request, notif_filter):
    return notifications_for_user(request.user, notif_filter)


def adminNotifications(request):
    notif_filter = request.GET.get('filter', 'all')

    return render(request, 'notifications.html', {
        "active_page": "adminNotifications",
        "notif_filter": notif_filter,
    })


def get_notifications_page_ajax_admin(request):
    notif_filter = request.GET.get('filter', 'all')
    page_obj = Paginator(
        _filter_admin_notifications(request, notif_filter),
        NOTIFICATIONS_PAGE_SIZE,
    ).get_page(request.GET.get('page', 1))

    html = render_to_string('partials/_notifications_rows.html', {
        "notifications": page_obj,
    })

    return JsonResponse({
        "html": html,
        "current_page": page_obj.number,
        "total_pages": page_obj.paginator.num_pages,
        "total_count": page_obj.paginator.count,
    })


def get_notifications_ajax_admin(request):
    notif_filter = request.GET.get('filter', 'all')
    queryset = _filter_admin_notifications(request, notif_filter)
    # Unread staff approvals are always included, even beyond the 10-item limit.
    pinned = list(queryset.filter(staff_account__isnull=False, is_read=False))
    others = queryset.exclude(id__in=[n.id for n in pinned])[:10]
    notifications = pinned + list(others)

    data = [
        serialize_notification(n)
        for n in notifications
    ]

    unread_count = get_unread_notifications_count(request)

    return JsonResponse({
        "notifications": data,
        "unread_count": unread_count,
    })


def view_notification_admin(request, notification_id):
    notification = get_object_or_404(notifications_for_user(request.user), id=notification_id)

    if not notification.is_read:
        notification.is_read = True
        notification.save(update_fields=['is_read'])

    if notification.account_event in (Notification.ADMIN_ACCESS_GRANTED, Notification.ADMIN_ACCESS_REMOVED):
        if notification.account_event == Notification.ADMIN_ACCESS_REMOVED:
            return redirect('userDashboard')
        return redirect('dashboard')
    if notification.staff_account_id:
        still_pending = UserProfile.objects.filter(
            user_id=notification.staff_account_id, user__is_active=True,
            role='staff', approval_status=UserProfile.APPROVAL_PENDING,
        ).exists()
        anchor = f'staff-{notification.staff_account_id}' if still_pending else 'pendingCard'
        return redirect(reverse('user_management') + '#' + anchor)
    return redirect('adm_report_detail', report_id=notification.report_id)


def mark_all_notifications_read_admin(request):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Invalid request.'}, status=400)

    Notification.objects.filter(user=request.user, is_read=False).update(is_read=True)

    return JsonResponse({'success': True})
