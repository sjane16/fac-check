from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from user.models import Report, ReportSequence, REPORT_PREFIX


class Command(BaseCommand):
    help = "Assign MED job order numbers to existing reports without report numbers."

    @transaction.atomic
    def handle(self, *args, **options):
        reports = Report.objects.filter(
            job_order_id__isnull=True
        ).order_by('submitted_at', 'id')

        assigned = 0

        for report in reports:
            report_date = timezone.localtime(report.submitted_at)
            sequence, _ = ReportSequence.objects.select_for_update().get_or_create(
                year=report_date.year,
                defaults={'last_number': 0},
            )
            sequence.last_number += 1
            sequence.save(update_fields=['last_number'])

            report.job_order_id = (
                f"{REPORT_PREFIX}-{report_date.year}-"
                f"{report_date.month:02d}-{sequence.last_number:06d}"
            )
            Report.objects.filter(pk=report.pk).update(
                job_order_id=report.job_order_id
            )
            assigned += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Assigned MED report numbers to {assigned} existing report(s)."
            )
        )
