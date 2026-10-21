from io import BytesIO
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import patch

from django.apps import apps
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.template.loader import render_to_string
from django.test import TestCase
from django.urls import reverse
from PIL import Image

from adm.models import Building, Floor, Room
from adm.views import _build_dashboard_data
from .forms import ReportForm
from .models import Report, UserProfile


class ReportLocationTests(TestCase):
    def setUp(self):
        self.building = Building.objects.create(name='Original Building', code='OB')
        self.floor = Floor.objects.create(building=self.building, floor_number=1, label='First Floor')
        self.room = Room.objects.create(floor=self.floor, name='Original Room', code='OR')

    def create_report(self, key, **kwargs):
        return Report.objects.create(job_order_id=key, category='electrical', description='Test', **kwargs)

    def backfill(self):
        migration = import_module('user.migrations.0034_backfill_report_rooms')
        migration.backfill_report_rooms(apps, SimpleNamespace(connection=SimpleNamespace(alias='default')))

    def test_new_reports_capture_location_and_survive_room_rename_and_deletion(self):
        report = self.create_report('ROOM-LINK', room=self.room)
        original = report.location
        self.room.name = 'Renamed Room'
        self.room.save()
        self.building.name = 'Renamed Building'
        self.building.save()
        report.save()
        report.refresh_from_db()
        self.assertEqual(report.location, original)
        self.assertEqual(report.room_location, 'Original Room')
        self.assertEqual(report.building_name, 'Original Building')
        self.room.delete()
        report.refresh_from_db()
        self.assertIsNone(report.room_id)
        self.assertEqual(report.location, original)
        dashboard = _build_dashboard_data('all')
        index = dashboard['building_labels'].index('Original Building')
        self.assertEqual(dashboard['building_status_data']['pending'][index], 1)

    def test_backfill_links_unique_matches_but_not_ambiguous_or_missing_rooms(self):
        label = 'Original Building, First Floor, Original Room'
        report = self.create_report('BACKFILL', location=label)
        self.backfill()
        self.backfill()
        report.refresh_from_db()
        self.assertEqual(report.room_id, self.room.pk)
        self.assertEqual(report.location, label)
        self.assertEqual(report.history.first().room_id, self.room.pk)
        Room.objects.create(floor=self.floor, name=self.room.name, code='DUP')
        ambiguous = self.create_report('AMBIGUOUS', location=label)
        unknown = self.create_report('UNKNOWN', location='Old Building, Old Floor, Old Room')
        self.backfill()
        for row in (ambiguous, unknown):
            row.refresh_from_db()
            self.assertIsNone(row.room_id)
        self.assertEqual(unknown.room_location, 'Old Room')
        self.assertEqual(unknown.building_name, 'Old Building')

    def test_room_selector_accepts_only_rooms_on_selected_floor(self):
        form = ReportForm(data={'building': self.building.pk, 'floor': self.floor.pk})
        self.assertEqual(list(form.fields['location'].queryset), [self.room])
        other_floor = Floor.objects.create(building=self.building, floor_number=2, label='Second')
        other_room = Room.objects.create(floor=other_floor, name='Other', code='OTHER')
        self.assertFalse(form.fields['location'].queryset.filter(pk=other_room.pk).exists())

    @patch('user.views.warm_model_async')
    def test_opening_report_form_starts_cnn_model_warmup(self, warm_model):
        user = User.objects.create_user(username='cnn-owner', password='test-password')
        UserProfile.objects.create(user=user, role='student', department='CCS')
        self.client.force_login(user)

        response = self.client.get(reverse('reportForm'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="cnn-loading"')
        self.assertContains(response, 'aria-modal="true"')
        self.assertContains(response, 'Analyzing Concern')
        self.assertContains(response, 'grid-template-columns: minmax(0, 45fr) minmax(0, 55fr)')
        warm_model.assert_called_once_with()
        form_html = self.client.get(reverse('reportFormAjax')).json()['html']
        self.assertIn('id="photo-upload"', form_html)
        self.assertIn('grid-cols-1 lg:grid-cols-2', form_html)

    def test_report_form_accepts_and_saves_optional_inventory_fields(self):
        image_data = BytesIO()
        Image.new('RGB', (2, 2), 'white').save(image_data, format='JPEG')
        form = ReportForm(data={
            'building': self.building.pk,
            'floor': self.floor.pk,
            'location': self.room.pk,
            'category': 'electrical',
            'description': 'A clear description of the issue.',
            'inventory_tag_no': 'TAG-123',
            'property_inventory_no': 'PROPERTY-456',
        }, files={
            'photo': SimpleUploadedFile('report.jpg', image_data.getvalue(), content_type='image/jpeg'),
        })

        self.assertTrue(form.is_valid(), form.errors)
        self.assertFalse(form.fields['inventory_tag_no'].required)
        self.assertFalse(form.fields['property_inventory_no'].required)
        html = render_to_string('user/partials/_report_form_content.html', {'form': form})
        self.assertIn('Inventory Tag No.', html)
        self.assertIn('Property Inventory No.', html)
        self.assertNotIn('(Optional)', html)
        self.assertIn('name="inventory_tag_no"', html)
        self.assertIn('name="property_inventory_no"', html)
        self.assertNotIn('(Optional)', form.fields['inventory_tag_no'].widget.attrs['placeholder'])
        self.assertNotIn('(Optional)', form.fields['property_inventory_no'].widget.attrs['placeholder'])
        for required_label in (
            'Click to upload photo <span class="text-red-500"',
            'AI Recommended Category <span class="ml-1 text-red-500"',
            'Building / Facility <span class="ml-1 text-red-500"',
            'Floor <span class="ml-1 text-red-500"',
            'Room / Area <span class="ml-1 text-red-500"',
            'Description <span class="ml-1 text-red-500"',
        ):
            self.assertIn(required_label, html)
        self.assertNotIn('data-cdd-size="lg"', html)
        self.assertIn('px-3 py-2 text-sm text-gray-700', html)
        report = form.save(commit=False)
        report.set_room_location(self.room)
        report.photo = None
        report.save()
        report.refresh_from_db()
        self.assertEqual(report.inventory_tag_no, 'TAG-123')
        self.assertEqual(report.property_inventory_no, 'PROPERTY-456')

    def test_admin_and_pending_user_details_display_inventory_fields(self):
        user = User.objects.create_user(username='inventory-owner', password='test-password')
        UserProfile.objects.create(user=user, role='student', department='CCS')
        report = self.create_report(
            'INVENTORY-DISPLAY',
            user=user,
            room=self.room,
            inventory_tag_no='TAG-123',
            property_inventory_no='PROPERTY-456',
        )

        admin = User.objects.create_user(username='inventory-admin', password='test-password', is_staff=True)
        self.client.force_login(admin)
        admin_response = self.client.get(reverse('adm_report_detail', args=[report.pk]))
        self.assertContains(admin_response, 'TAG-123')
        self.assertContains(admin_response, 'PROPERTY-456')

        self.client.force_login(user)
        pending_response = self.client.get(reverse('reportedIssue', args=[report.pk]))
        self.assertContains(pending_response, 'TAG-123')
        self.assertContains(pending_response, 'PROPERTY-456')

        report.status = 'in_progress'
        report.save(update_fields=['status'])
        in_progress_response = self.client.get(reverse('reportedIssue', args=[report.pk]))
        self.assertNotContains(in_progress_response, 'TAG-123')
        self.assertNotContains(in_progress_response, 'PROPERTY-456')

        report.inventory_tag_no = ''
        report.property_inventory_no = ''
        report.status = 'closed'
        report.save(update_fields=['inventory_tag_no', 'property_inventory_no', 'status'])
        self.client.force_login(admin)
        closed_admin_response = self.client.get(reverse('adm_report_detail', args=[report.pk]))
        self.assertContains(closed_admin_response, 'Inventory Tag No.')
        self.assertContains(closed_admin_response, 'Property Inventory No.')
        self.assertContains(closed_admin_response, 'N/A')

        report.status = 'pending'
        report.save(update_fields=['status'])
        self.client.force_login(user)
        blank_pending_response = self.client.get(reverse('reportedIssue', args=[report.pk]))
        self.assertContains(blank_pending_response, 'Inventory Tag No.')
        self.assertContains(blank_pending_response, 'Property Inventory No.')
        self.assertContains(blank_pending_response, 'N/A')

    def test_department_and_status_filters_use_custom_dropdowns(self):
        user = User.objects.create_user(
            username='dropdown-user',
            email='dropdown-user@example.com',
            first_name='Dropdown',
            last_name='User',
            password='test-password',
        )
        UserProfile.objects.create(user=user, role='student', department='CCS')
        self.client.force_login(user)

        menu_html = self.client.get(reverse('menuAjax')).json()['html']
        self.assertIn('id="id_department"', menu_html)
        self.assertIn('data-cdd-size="lg"', menu_html)
        self.assertIn(user.email, menu_html)
        self.assertNotIn(f'<span class="text-sm text-gray-600">{user.username}</span>', menu_html)

        reports_response = self.client.get(reverse('userReports'))
        self.assertContains(reports_response, 'id="mobileStatusFilter"')
        self.assertContains(reports_response, 'id="desktopStatusFilter"')
        self.assertEqual(reports_response.content.count(b'data-cdd-width='), 2)
        self.assertContains(reports_response, 'cdd/custom-dropdown.js')
