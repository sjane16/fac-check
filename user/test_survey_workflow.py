from io import BytesIO

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.template.loader import render_to_string
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from .models import Notification, Report, SurveyResponse, UserProfile
from adm.views import build_rating_analytics_context


@override_settings(STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.InMemoryStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
})
class SurveyMethodWorkflowTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='survey-client',
            email='survey-client@example.com',
            password='test-password',
        )
        UserProfile.objects.create(user=self.user, role='student', department='CCS')
        self.report = Report.objects.create(
            user=self.user,
            job_order_id='SURVEY-WORKFLOW-1',
            category='electrical',
            location='Test Room',
            description='Test resolved issue',
            status='resolved',
        )

    def choose_method(self, method):
        return self.client.post(
            reverse('chooseSurveyMethod', args=[self.report.pk]),
            {'method': method},
        )

    def test_user_can_change_method_until_survey_is_submitted(self):
        self.client.force_login(self.user)

        online = self.choose_method('system')
        self.assertEqual(online.status_code, 200)
        self.assertEqual(online.json()['method'], 'system')

        manual = self.choose_method('manual')
        self.assertEqual(manual.status_code, 200)
        self.report.refresh_from_db()
        self.assertEqual(self.report.survey_method, 'manual')

        SurveyResponse.objects.create(
            report=self.report, quality=5, quantity=4, timeliness=3,
        )
        locked = self.choose_method('system')
        self.assertEqual(locked.status_code, 400)

    def test_manual_choice_notifies_active_admins_once_per_method_change(self):
        admin = User.objects.create_user(username='visit-admin', is_staff=True)
        head = User.objects.create_user(username='visit-head', is_superuser=True)
        User.objects.create_user(username='inactive-admin', is_staff=True, is_active=False)
        self.user.first_name = 'Maria'
        self.user.last_name = 'Santos'
        self.user.save(update_fields=['first_name', 'last_name'])
        self.client.force_login(self.user)
        alerts = Notification.objects.filter(report=self.report)

        self.assertEqual(self.choose_method('system').status_code, 200)
        self.assertFalse(alerts.exists())
        self.assertEqual(self.choose_method('manual').status_code, 200)
        self.assertCountEqual(alerts.values_list('user_id', flat=True), [admin.pk, head.pk])
        for alert in alerts.all():
            self.assertIn('Maria Santos will visit the MED office', alert.message)
            self.assertIn('during office hours', alert.message)
            self.assertIn(self.report.job_order_id, alert.message)
            self.assertEqual(alert.status_snapshot, 'resolved')
            self.assertFalse(alert.is_read)

        self.assertEqual(self.choose_method('manual').status_code, 200)
        self.assertEqual(alerts.count(), 2)
        self.assertEqual(self.choose_method('system').status_code, 200)
        self.assertEqual(alerts.count(), 2)
        self.assertEqual(self.choose_method('manual').status_code, 200)
        self.assertEqual(alerts.count(), 2)

    def test_rejected_manual_choice_does_not_notify_admins(self):
        User.objects.create_user(username='visit-admin', is_staff=True)
        self.client.force_login(self.user)
        self.report.status = 'in_progress'
        self.report.save(update_fields=['status'])
        self.assertEqual(self.choose_method('manual').status_code, 400)
        self.assertFalse(Notification.objects.filter(report=self.report).exists())

    def test_manual_choice_confirmation_has_got_it_and_back_without_print_button(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse('reportedIssue', args=[self.report.pk]))
        self.assertContains(response, 'Got it')
        self.assertContains(response, 'Back — choose another method')
        self.assertContains(response, 'office during office hours')
        self.assertNotContains(response, 'Open / Print Job Order')

    def test_online_survey_requires_online_choice_and_closes_report(self):
        self.client.force_login(self.user)
        payload = {'quality': 5, 'quantity': 4, 'timeliness': 3}

        rejected = self.client.post(reverse('submitSurvey', args=[self.report.pk]), payload)
        self.assertEqual(rejected.status_code, 400)

        self.choose_method('system')
        submitted = self.client.post(reverse('submitSurvey', args=[self.report.pk]), payload)
        self.assertEqual(submitted.status_code, 200)
        self.report.refresh_from_db()
        self.assertEqual(self.report.status, 'closed')
        self.assertEqual(self.report.survey_method, 'system')
        self.assertEqual(self.report.survey.quality, 5)

        details = self.client.get(reverse('reportedIssue', args=[self.report.pk]))
        self.assertContains(details, 'Export')
        self.assertNotContains(details, 'View Scanned Job Order')

    def test_admin_records_manual_ratings_with_jpg_png_scan(self):
        self.client.force_login(self.user)
        self.choose_method('manual')
        self.report.completion_photo = 'reports/completion/resolution-proof'
        self.report.save(update_fields=['completion_photo'])
        admin = User.objects.create_user(username='survey-admin', password='admin-password', is_staff=True)
        self.client.force_login(admin)

        image_data = BytesIO()
        Image.new('RGB', (2, 2), 'white').save(image_data, format='PNG')
        scan = SimpleUploadedFile('signed-job-order.png', image_data.getvalue(), content_type='image/png')
        response = self.client.post(reverse('adm_manual_survey', args=[self.report.pk]), {
            'scan_file': scan,
            'quality': 4,
            'quantity': 5,
            'timeliness': 3,
        }, follow=True)

        self.assertEqual(response.redirect_chain, [(reverse('adm_report_detail', args=[self.report.pk]), 302)])
        self.assertContains(response, 'survey recorded and report closed.')
        self.assertNotContains(response, 'has been Closed.')
        self.assertEqual(response.content.count(b'class="toast-message'), 1)
        self.report.refresh_from_db()
        survey = SurveyResponse.objects.get(report=self.report)
        self.assertEqual(self.report.status, 'closed')
        self.assertEqual(self.report.survey_method, 'manual')
        self.assertEqual((survey.quality, survey.quantity, survey.timeliness), (4, 5, 3))
        self.assertTrue(survey.scan_file.name)
        closed_alert = Notification.objects.get(user=self.user, report=self.report, status_snapshot='closed')
        self.assertIn('is now Closed', closed_alert.message)

        details = self.client.get(reverse('adm_report_detail', args=[self.report.pk]))
        self.assertContains(details, 'View Scanned Job Order')
        self.assertNotContains(details, 'Export')
        self.assertEqual(details.content.count(b'View Scanned Job Order'), 1)
        self.assertContains(details, 'Completion Photo')

        self.client.force_login(self.user)
        user_details = self.client.get(reverse('reportedIssue', args=[self.report.pk]))
        self.assertContains(user_details, 'View Scanned Job Order')
        self.assertNotContains(user_details, 'Export')
        self.assertEqual(user_details.content.count(b'View Scanned Job Order'), 1)
        self.assertContains(user_details, 'Completion Photo')

        self.client.force_login(self.user)
        details = self.client.get(reverse('reportedIssue', args=[self.report.pk]))
        self.assertContains(details, 'View Scanned Job Order')
        self.assertContains(details, survey.scan_file.url)

    def test_manual_scan_requires_jpg_or_png(self):
        self.client.force_login(self.user)
        self.choose_method('manual')
        admin = User.objects.create_user(username='survey-admin', password='admin-password', is_staff=True)
        self.client.force_login(admin)

        scan = SimpleUploadedFile('signed-job-order.gif', b'not an image', content_type='image/gif')
        response = self.client.post(reverse('adm_manual_survey', args=[self.report.pk]), {
            'scan_file': scan,
            'quality': 4,
            'quantity': 5,
            'timeliness': 3,
        })

        self.assertRedirects(response, reverse('adm_report_detail', args=[self.report.pk]))
        self.assertFalse(SurveyResponse.objects.filter(report=self.report).exists())
        self.report.refresh_from_db()
        self.assertEqual(self.report.status, 'resolved')

    def test_admin_report_detail_shows_manual_upload_only_for_manual_choice(self):
        admin = User.objects.create_user(username='survey-admin', password='admin-password', is_staff=True)
        self.client.force_login(admin)

        unselected_page = self.client.get(reverse('adm_report_detail', args=[self.report.pk]))
        self.assertNotContains(unselected_page, 'Scanned Job Order (JPG or PNG)')

        self.client.force_login(self.user)
        manual_choice = self.choose_method('manual')
        self.assertEqual(manual_choice.status_code, 200)
        self.client.force_login(admin)
        manual_page = self.client.get(reverse('adm_report_detail', args=[self.report.pk]))
        self.assertContains(manual_page, 'Scanned Job Order (JPG or PNG)')
        self.assertNotContains(manual_page, 'border border-blue-100 bg-blue-50/60 p-5')
        self.assertContains(manual_page, 'class="space-y-6"')
        self.assertContains(manual_page, 'Record Survey and Close Report')
        self.assertContains(manual_page, 'id="manual-scan-file"')
        self.assertContains(manual_page, 'file:bg-white')
        self.assertContains(manual_page, 'w-full rounded-xl bg-blue-600')
        self.assertEqual(manual_page.content.count(b'data-cdd-width="100%" data-cdd-class="w-full" required'), 3)
        self.assertContains(manual_page, 'data-cdd-class="w-full"')
        self.assertContains(manual_page, '<option value="1">1</option>')
        self.assertNotContains(manual_page, '<option value="1">1 / 5</option>')

        self.client.force_login(self.user)
        online_choice = self.choose_method('system')
        self.assertEqual(online_choice.status_code, 200)
        self.client.force_login(admin)
        online_page = self.client.get(reverse('adm_report_detail', args=[self.report.pk]))
        self.assertNotContains(online_page, 'Scanned Job Order (JPG or PNG)')
        self.assertNotContains(online_page, 'onclick="exportJobOrder()"')
        self.assertContains(online_page, 'Waiting for their submission')

        SurveyResponse.objects.create(
            report=self.report, quality=5, quantity=5, timeliness=5,
        )
        self.report.status = 'closed'
        self.report.save(update_fields=['status'])
        completed_page = self.client.get(reverse('adm_report_detail', args=[self.report.pk]))
        self.assertNotContains(completed_page, 'Survey recorded')
        self.assertNotContains(completed_page, 'Quality <strong>5/5</strong>')

    def test_job_order_pdf_note_depends_on_selected_survey_method(self):
        SurveyResponse.objects.create(
            report=self.report, quality=5, quantity=4, timeliness=3,
        )
        context = {
            'report': self.report,
            'category_checks': {key: False for key in (
                'vehicle_checkup', 'electrical', 'refrigeration_acu', 'painting',
                'carpentry_masonry', 'plumbing', 'others',
            )},
        }
        self.report.survey_method = 'system'
        online_pdf = render_to_string('user/job_order_pdf.html', context)
        online_survey_section = online_pdf.split('Customer Service Survey', 1)[1]
        self.assertIn('This is a system generated document. Signature is not required', online_survey_section)
        self.assertIn('Quality: 5/5', online_survey_section)
        self.assertIn('Signature on Printed Name', online_survey_section)
        self.assertIn('survey-client', online_survey_section)

        self.report.survey_method = 'manual'
        manual_pdf = render_to_string('user/job_order_pdf.html', context)
        manual_survey_section = manual_pdf.split('Customer Service Survey', 1)[1]
        self.assertNotIn('This is a system generated document. Signature is not required', manual_survey_section)
        self.assertIn('Signature on Printed Name', manual_survey_section)

        self.report.survey_method = ''
        self.report.survey.rating_source = 'head_admin'
        med_pdf = render_to_string('user/job_order_pdf.html', {
            **context,
            'is_med_personnel_report': True,
        })
        med_rating_section = med_pdf.split('Head Admin Group QQT Rating', 1)[1]
        self.assertIn('This is a system generated document. Signature is not required', med_pdf)
        self.assertIn('Signature on Printed Name', med_rating_section)
        self.assertIn('survey-client', med_rating_section)

    def test_blank_job_order_survey_scores_print_empty_over_five(self):
        context = {
            'report': self.report,
            'category_checks': {key: False for key in (
                'vehicle_checkup', 'electrical', 'refrigeration_acu', 'painting',
                'carpentry_masonry', 'plumbing', 'others',
            )},
        }
        pdf_html = render_to_string('user/job_order_pdf.html', context)
        survey_section = pdf_html.split('Customer Service Survey', 1)[1]
        self.assertIn('Quality: &nbsp;/5', survey_section)
        self.assertIn('Quantity: &nbsp;/5', survey_section)
        self.assertIn('Timeliness: &nbsp;/5', survey_section)
        self.assertNotIn('-/5', survey_section)


class HeadAdminGroupRatingTests(TestCase):
    def setUp(self):
        self.personnel = User.objects.create_user(
            username='med-personnel',
            email='med-personnel@example.com',
            password='test-password',
            first_name='George L.',
            last_name='Castro',
        )
        profile, _ = UserProfile.objects.get_or_create(user=self.personnel)
        profile.role = 'med_personnel'
        profile.med_personnel_slug = 'george-l-castro'
        profile.save(update_fields=['role', 'med_personnel_slug'])
        self.head = User.objects.create_user(
            username='head-admin', password='test-password', is_staff=True,
        )
        profile, _ = UserProfile.objects.get_or_create(user=self.head)
        profile.role = 'admin'
        profile.is_head_admin = True
        profile.save(update_fields=['role', 'is_head_admin'])
        self.report = Report.objects.create(
            user=self.personnel,
            job_order_id='MED-GROUP-RATING-1',
            category='electrical',
            location='Test Room',
            description='MED personnel concern',
            status='resolved',
            assigned_personnel_data=[
                {'name': 'George L. Castro'},
                {'name': 'Percival D. Padilla'},
            ],
        )

    def test_med_reporter_cannot_choose_or_submit_self_rating(self):
        self.client.force_login(self.personnel)

        details = self.client.get(reverse('reportedIssue', args=[self.report.pk]))
        self.assertContains(details, 'Head Admin will provide the QQT rating')
        self.assertNotContains(details, 'Take Survey Online')

        method = self.client.post(reverse('chooseSurveyMethod', args=[self.report.pk]), {'method': 'system'})
        survey = self.client.post(reverse('submitSurvey', args=[self.report.pk]), {
            'quality': 5, 'quantity': 4, 'timeliness': 3,
        })
        self.assertEqual(method.status_code, 403)
        self.assertEqual(survey.status_code, 403)
        self.assertFalse(SurveyResponse.objects.filter(report=self.report).exists())

    def test_head_admin_group_rating_applies_once_and_closes_report(self):
        self.client.force_login(self.head)
        details = self.client.get(reverse('adm_report_detail', args=[self.report.pk]))
        self.assertContains(details, 'id="head-rating-modal"')
        self.assertContains(details, 'id="head-quality-emoji-5"')
        self.assertContains(details, 'id="head-timeliness-emoji-1"')
        self.assertContains(details, 'George L. Castro, Percival D. Padilla')
        self.assertContains(details, 'max-w-3xl max-h-[94vh] overflow-y-auto')
        self.assertNotContains(details, 'Change method')
        self.assertNotContains(details, 'open-head-rating-modal')
        self.assertNotContains(details, 'Close rating modal')
        page_html = details.content.decode()
        self.assertLess(page_html.index('</main>'), page_html.index('id="head-rating-modal"'))

        response = self.client.post(reverse('adm_personnel_rating', args=[self.report.pk]), {
            'quality': 5, 'quantity': 4, 'timeliness': 3,
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('adm_report_detail', args=[self.report.pk]))
        self.report.refresh_from_db()
        rating = SurveyResponse.objects.get(report=self.report)
        self.assertEqual(self.report.status, 'closed')
        self.assertEqual((rating.quality, rating.quantity, rating.timeliness), (5, 4, 3))
        self.assertEqual(rating.rating_source, 'head_admin')
        self.assertEqual(rating.rated_by, self.head)

        analysis = build_rating_analytics_context(include_responses=False)
        self.assertEqual(analysis['total_surveys'], 0)
        performance = {row['name']: row for row in analysis['personnel_rows']}
        for name in ('George L. Castro', 'Percival D. Padilla'):
            self.assertEqual(performance[name]['quality'], 5)
            self.assertEqual(performance[name]['quantity'], 4)
            self.assertEqual(performance[name]['timeliness'], 3)

        success = self.client.get(reverse('adm_report_detail', args=[self.report.pk]))
        self.assertContains(success, 'id="personnel-rating-success-modal"')
        self.assertContains(success, 'The group CSS rating was recorded for')
        self.assertContains(success, 'George L. Castro, Percival D. Padilla')
        self.assertNotContains(self.client.get(reverse('adm_report_detail', args=[self.report.pk])), 'id="personnel-rating-success-modal"')

    def test_regular_admin_cannot_submit_head_rating(self):
        admin = User.objects.create_user(username='co-admin', password='test-password', is_staff=True)
        profile, _ = UserProfile.objects.get_or_create(user=admin)
        profile.role = 'admin'
        profile.save(update_fields=['role'])
        self.client.force_login(admin)

        details = self.client.get(reverse('adm_report_detail', args=[self.report.pk]))
        self.assertNotContains(details, 'id="head-rating-modal"')
        self.assertContains(details, 'Waiting for the Head Admin')

        response = self.client.post(reverse('adm_personnel_rating', args=[self.report.pk]), {
            'quality': 5, 'quantity': 5, 'timeliness': 5,
        })
        self.assertEqual(response.status_code, 302)
        self.assertFalse(SurveyResponse.objects.filter(report=self.report).exists())
