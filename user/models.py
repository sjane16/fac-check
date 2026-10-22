from django.db import models, transaction
from django.db.models import F
from django.contrib.auth.models import User
from django.utils import timezone
from datetime import timedelta
from cloudinary.models import CloudinaryField
from .validators import phone_validator
from simple_history.models import HistoricalRecords
from .constants import MED_PERSONNEL_CATEGORY_CHOICES, PRIORITY_SLA_HOURS


REPORT_PREFIX = "MED-JO"


class ReportSequence(models.Model):
    year = models.PositiveIntegerField(unique=True)
    last_number = models.PositiveIntegerField(default=0)

    def __str__(self):
        return f"{self.year} - {self.last_number}"


class Report(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('in_progress', 'In Progress'),
        ('resolved', 'Resolved'),
        ('closed', 'Closed'),
    ]

    CATEGORY_CHOICES = [
        ('electrical', 'Electrical'),
        ('plumbing', 'Plumbing'),
        ('refrigeration_acu', 'Refrigeration / ACUs'),
        ('carpentry_masonry', 'Carpentry / Masonry'),
        ('painting', 'Painting'),
        ('vehicle_checkup', 'Vehicle Check-up'),
        ('others', 'Others'),
    ]

    PRIORITY_CHOICES = [
    ('critical', 'Critical / Urgent'),
    ('high', 'High Priority'),
    ('standard', 'Standard'),
    ]
    SURVEY_METHOD_CHOICES = [
        ('system', 'Online system'),
        ('manual', 'Manual at MED office'),
    ]

    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='reports')
    job_order_id = models.CharField(max_length=30, unique=True, blank=True, null=True)
    photo = CloudinaryField('Photo', folder='reports/', null=True, blank=True)
    completion_photo = CloudinaryField('Completion Photo', folder='reports/completion/', null=True, blank=True)
    category = models.CharField(max_length=100, choices=CATEGORY_CHOICES)
    custom_category = models.CharField(max_length=100, blank=True, null=True)
    location = models.CharField(max_length=100)
    room = models.ForeignKey('adm.Room', on_delete=models.SET_NULL, null=True, blank=True, related_name='reports')
    building_name = models.CharField(max_length=100, blank=True, default='')
    room_name = models.CharField(max_length=100, blank=True, default='')
    description = models.TextField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    survey_method = models.CharField(max_length=10, choices=SURVEY_METHOD_CHOICES, blank=True, default='')
    inventory_tag_no = models.CharField(max_length=100, blank=True, default='')
    property_inventory_no = models.CharField(max_length=100, blank=True, default='')
    submitted_at = models.DateTimeField(auto_now_add=True)
    priority = models.CharField(max_length=30, choices=PRIORITY_CHOICES, blank=True, null=True)
    assigned_personnel = models.CharField(max_length=255, blank=True, null=True)
    assigned_personnel_data = models.JSONField(default=list, blank=True)
    accomplished_at = models.DateTimeField(null=True, blank=True)
    in_progress_at = models.DateTimeField(null=True, blank=True)
    pending_awaiting_personnel = models.BooleanField(default=False)
    pending_awaiting_personnel_at = models.DateTimeField(null=True, blank=True)
    pending_awaiting_materials = models.BooleanField(default=False)
    pending_awaiting_materials_at = models.DateTimeField(null=True, blank=True)
    pending_awaiting_approval = models.BooleanField(default=False)
    pending_awaiting_approval_at = models.DateTimeField(null=True, blank=True)
    cnn_raw_prediction = models.CharField(max_length=100, blank=True, null=True)
    cnn_confidence = models.FloatField(blank=True, null=True)
    cnn_margin = models.FloatField(blank=True, null=True)
    flagged_for_review = models.BooleanField(default=False)
    findings = models.TextField(blank=True)
    recommendation = models.TextField(blank=True)
    materials_required = models.JSONField(default=list, blank=True)

    @property
    def materials_rows(self):
        """Laging 4 na row (qty/unit/description) para sa Job Order PDF."""
        rows = []
        for item in (self.materials_required or [])[:4]:
            rows.append({
                'qty': (item.get('qty') or '').strip(),
                'unit': (item.get('unit') or '').strip(),
                'description': (item.get('description') or '').strip(),
            })
        while len(rows) < 4:
            rows.append({'qty': '', 'unit': '', 'description': ''})
        return rows

    @property
    def room_location(self):
        return self.room_name

    def set_room_location(self, room):
        """Capture the selected room's display values without changing them later."""
        self.room = room
        self.building_name = room.floor.building.name
        self.room_name = room.name
        self.location = f"{self.building_name}, {room.floor.label}, {self.room_name}"

    @property
    def reporter_name(self):
        if self.user:
            full_name = self.user.get_full_name()
            return full_name if full_name else self.user.username
        return '—'

    @property
    def assigned_personnel_summary(self):
        if self.assigned_personnel_data:
            return ", ".join(
                item.get('name')
                for item in self.assigned_personnel_data
                if item.get('name')
            )

        return None

    @property
    def assigned_personnel_preview(self):
        """First two assigned names for list views; '...' when there are more."""
        names = [
            item.get('name')
            for item in (self.assigned_personnel_data or [])
            if item.get('name')
        ]
        if not names:
            return None
        preview = ", ".join(names[:2])
        return f"{preview}, ..." if len(names) > 2 else preview

    @property
    def expected_resolution_hours(self):
        """
        Expected resolution time based on Maintenance & Engineering Division policy.

        Critical / Urgent:
            2–3 hours(system uses 3 hours)

        High Priority:
            Within the same day

        Standard:
            Typically resolved within 2–3 days(system uses 72 hours)
        """

        return PRIORITY_SLA_HOURS.get(self.priority)

    @property
    def expected_resolution_at(self):
        if not self.in_progress_at:
            return None
        return self.in_progress_at + timedelta(hours=self.expected_resolution_hours)

    @property
    def actual_resolution_hours(self):
        if not self.in_progress_at or not self.accomplished_at:
            return None
        seconds = (self.accomplished_at - self.in_progress_at).total_seconds()
        return round(seconds / 3600, 1)

    @property
    def met_expected_resolution(self):
        expected_at = self.expected_resolution_at
        if not expected_at or not self.accomplished_at:
            return False
        return self.accomplished_at <= expected_at

    @property
    def closed_at(self):
        try:
            return self.survey.submitted_at
        except SurveyResponse.DoesNotExist:
            return self.accomplished_at

    def save(self, *args, **kwargs):
        if self._state.adding:
            if self.room_id:
                self.set_room_location(self.room)
            else:
                # Legacy/imported reports can still be created without a room link.
                self.building_name = self.building_name or self.location.partition(',')[0].strip()
                self.room_name = self.room_name or self.location.rpartition(',')[-1].strip()
        # Compatibility copy only; JSON remains the editable source of truth.
        self.assigned_personnel = (self.assigned_personnel_summary or '')[:255] or None
        if kwargs.get('update_fields') and 'assigned_personnel_data' in kwargs['update_fields']:
            kwargs['update_fields'] = set(kwargs['update_fields']) | {'assigned_personnel'}
        if self.job_order_id:
            return super().save(*args, **kwargs)

        with transaction.atomic():
            sequence, _ = ReportSequence.objects.select_for_update().get_or_create(
                year=timezone.localtime(self.submitted_at).year,
                defaults={'last_number': 0},
            )
            ReportSequence.objects.filter(pk=sequence.pk).update(
                last_number=F('last_number') + 1
            )
            sequence.refresh_from_db()

            report_date = timezone.localtime(self.submitted_at)
            self.job_order_id = (
                f"{REPORT_PREFIX}-{report_date.year}-"
                f"{report_date.month:02d}-{sequence.last_number:06d}"
            )
            super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.job_order_id or f'#{self.id:04d}'} - {self.get_category_display()} ({self.location})"

    history = HistoricalRecords()


class UserProfile(models.Model):
    ROLE_CHOICES = [
        ('student', 'Student'),
        ('faculty', 'Faculty'),
        ('staff', 'Staff'),
        # MED personnel accounts can submit concerns and appear in assignments.
        ('med_personnel', 'MED Personnel'),
        ('admin', 'Admin'),
    ]

    DEPARTMENT_CHOICES = [
        ('CCS', 'College of Computing Studies'),
        ('CHS', 'College of Health Sciences'),
        ('CCJ', 'College of Criminal Justice'),
        ('CED', 'College of Education'),
        ('NSTP', 'National Service Training Program'),
        ('GenED', 'General Education'),
        ('CBPM', 'College of Business and Public Management'),
        ('COL', 'College of Law'),
        ('CAS', 'College of Arts and Sciences'),
    ]

    # Staff who pick this option type their own office; the typed text is what gets saved.
    OTHER_OFFICE_CODE = 'OTH'

    # Offices and areas for the Staff role, taken from the rooms in adm/management/commands/seed.py
    # that have staff working in them (no classrooms, labs, comfort rooms, storage or lounges).
    # Written out the same way as the colleges above and not attached to the model field,
    # so adding or renaming one needs no migration. Only the code is saved in `department`.
    # A code that is also in the college list (only NSTP) must keep the same name as there.
    OFFICE_CHOICES = [
        # Administrative Building
        ('UHS', 'University Health Services'),
        ('MEO', 'Maintenance and Engineering Office'),
        ('SEO', 'Supply and Equipment Office'),
        ('REG', 'Registrar'),
        ('CMD', 'Cash Management Division'),
        ('ADMIN', 'Administrative Offices'),
        ('LIB', 'Library'),
        ('NSTP', 'National Service Training Program'),
        ('ACED', 'Athletics and Cultural Engagement Division'),
        ('REC', 'Records Office'),
        # New Building
        ('ROTC', 'ROTC'),
        ('ACCTG', 'Accounting Division'),
        ('PBD', 'Planning and Budget Division'),
        ('PAYROLL', 'Payroll'),
        ('PROC', 'Procurement'),
        ('HRMDD', 'Human Resources Management and Development Division'),
        ('VPC', 'Office of the VP for Comptrollership'),
        ('VPA', 'Office of the VP for Administration'),
        ('ICTO', 'Information and Communications Technology Office'),
        # Main Building
        ('PRES', 'Office of the University President'),
        ('VPAA', 'Office of the VP for Academic Affairs'),
        ('VPSSSS', 'Office of the VP for Student Success and Support Services'),
        ('QAA', 'Quality Assurance and Accreditation'),
        ('GCPS', 'Guidance Counseling and Psychological Services'),
        ('URELA', 'University Research Extension Linkages'),
        ('IGPS', 'Institute of Graduate and Professional Studies'),
        ('ADMIT', 'Admission Office'),
        ('SEC', 'Security Office'),
        ('OSA', 'Office of Student Affairs'),
        ('OLA', 'Office of Legal Aid'),
        ('OSIPP', 'Office of Student Internship and Placement'),
        # Campus areas
        ('CANT', 'Canteen'),
        ('CCRT', 'Covered Court'),
        ('IGDN', 'Inner Garden'),
        ('PARK', 'Parking Lot'),
        ('OTH', 'Other Office or Area'),
    ]

    @classmethod
    def department_choices_for(cls, role):
        """Staff pick an office or area; students and faculty pick a college.
        Other roles (MED personnel, admin) can hold either."""
        if role == 'staff':
            return cls.OFFICE_CHOICES
        if role in ('student', 'faculty'):
            return cls.DEPARTMENT_CHOICES
        return cls.DEPARTMENT_CHOICES + cls.OFFICE_CHOICES

    @property
    def department_name(self):
        """Full name of the saved department or office code (the code itself if unknown)."""
        names = dict(self.DEPARTMENT_CHOICES + self.OFFICE_CHOICES)
        return names.get(self.department, self.department)

    @property
    def department_field_label(self):
        """Staff belong to an office, everyone else to a department."""
        return 'Office' if self.role == 'staff' else 'Department'

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    role = models.CharField(max_length=20, choices=ROLE_CHOICES)
    med_personnel_slug = models.CharField(max_length=64, blank=True, default='')
    med_personnel_category = models.CharField(max_length=40, blank=True, default='', choices=MED_PERSONNEL_CATEGORY_CHOICES)
    department = models.CharField(max_length=100, choices=DEPARTMENT_CHOICES, blank=True)
    phone = models.CharField(max_length=20, blank=True, validators=[phone_validator])
    profile_photo = models.ImageField(upload_to='profile_photos/', blank=True, null=True)

    otp = models.CharField(max_length=6, blank=True, default='')
    otp_created_at = models.DateTimeField(null=True, blank=True)
    otp_attempts = models.PositiveSmallIntegerField(default=0, db_default=0)
    # Bumped on every real password change; trusted-device cookies carry it,
    # so a password change logs out every trusted device (see accounts/views.py)
    credentials_version = models.PositiveIntegerField(default=0, db_default=0)

    # New Staff signups also need Head Admin approval after email verification.
    APPROVAL_PENDING = 'pending'
    APPROVAL_APPROVED = 'approved'
    APPROVAL_CHOICES = [
        (APPROVAL_PENDING, 'Pending approval'),
        (APPROVAL_APPROVED, 'Approved'),
    ]
    approval_status = models.CharField(max_length=10, choices=APPROVAL_CHOICES,
                                       default=APPROVAL_APPROVED, db_default=APPROVAL_APPROVED)
    # Set when an admin deactivates the account; tells "deactivated" apart from
    # "never verified their email" (both have user.is_active = False)
    deactivated_at = models.DateTimeField(null=True, blank=True)
    # Accounts created by an admin (Excel import) get an emailed temporary password and
    # must pick their own at first login (accounts.middleware.ForcePasswordChangeMiddleware)
    must_change_password = models.BooleanField(default=False, db_default=False)
    # The Head Admin (the "admin of the admins") is the only one who can open User Management.
    # Set it from the terminal: python manage.py set_head_admin <email>
    is_head_admin = models.BooleanField(default=False, db_default=False)

    def __str__(self):
        return f"{self.user.username} - Profile"

    history = HistoricalRecords()


class SurveyResponse(models.Model):
    RATING_SOURCE_CHOICES = [
        ('client', 'Client feedback'),
        ('head_admin', 'Head Admin personnel rating'),
    ]

    report = models.OneToOneField(Report, on_delete=models.CASCADE, related_name='survey')
    quality = models.IntegerField(null=True, blank=True)
    quantity = models.IntegerField(null=True, blank=True)
    timeliness = models.IntegerField(null=True, blank=True)
    scan_file = models.ImageField(upload_to='survey_scans/', null=True, blank=True)
    rating_source = models.CharField(max_length=12, choices=RATING_SOURCE_CHOICES, default='client')
    rated_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='head_ratings')
    submitted_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Survey for Report {self.report.job_order_id or f'#{self.report.id:04d}'}"

    history = HistoricalRecords()


class Notification(models.Model):
    ADMIN_ACCESS_GRANTED = 'admin_access_granted'
    ADMIN_ACCESS_REMOVED = 'admin_access_removed'

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='notifications')
    report = models.ForeignKey(Report, on_delete=models.CASCADE, related_name='notifications', null=True, blank=True)
    staff_account = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name='approval_notifications', null=True, blank=True,
    )
    account_event = models.CharField(
        max_length=20, blank=True, default='',
        choices=[
            (ADMIN_ACCESS_GRANTED, 'Admin access granted'),
            (ADMIN_ACCESS_REMOVED, 'Admin access removed'),
        ],
    )
    message = models.CharField(max_length=255)
    status_snapshot = models.CharField(max_length=20, choices=Report.STATUS_CHOICES, blank=True)
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(fields=['user', 'staff_account'], name='unique_staff_approval_notification'),
            models.CheckConstraint(
                condition=(
                    models.Q(report__isnull=False, staff_account__isnull=True, account_event='')
                    | models.Q(report__isnull=True, staff_account__isnull=False, account_event='')
                    | models.Q(
                        report__isnull=True, staff_account__isnull=True,
                        account_event__in=('admin_access_granted', 'admin_access_removed'), status_snapshot='',
                    )
                ),
                name='notification_has_one_target',
            ),
        ]

    def __str__(self):
        return f"{self.user.username} - {self.message[:40]}"

    @property
    def time_ago(self):
        diff = timezone.now() - self.created_at
        seconds = diff.total_seconds()

        if seconds < 60:
            return "Just now"

        minutes = seconds // 60
        if minutes < 60:
            return f"{int(minutes)}m"

        hours = minutes // 60
        if hours < 24:
            return f"{int(hours)}h"

        days = hours // 24
        return f"{int(days)}d"

    @property
    def is_recent(self):
        """True if this notification was created less than 24 hours ago."""
        return (timezone.now() - self.created_at) < timedelta(hours=24)

def invalidate_trusted_devices(user):
    """Call after a real password change so every trusted device needs MFA again."""
    UserProfile.objects.filter(user=user).update(credentials_version=models.F('credentials_version') + 1)


class Passkey(models.Model):
    """A WebAuthn passkey a user can sign in with (fingerprint, face or screen lock)."""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='passkeys')
    name = models.CharField(max_length=60)
    # base64url-encoded credential ID, as sent by the browser
    credential_id = models.CharField(max_length=512, unique=True)
    public_key = models.BinaryField()
    sign_count = models.PositiveBigIntegerField(default=0)
    transports = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.user.username} - {self.name}"
