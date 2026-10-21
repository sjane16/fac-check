from io import BytesIO
from unittest.mock import patch

import numpy as np
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from adm.models import Building, Floor, Room
from ml import predictor
from .models import Report, UserProfile


def make_photo(seed=0, name='concern.png'):
    pixels = np.random.default_rng(seed).integers(0, 255, (240, 320, 3), dtype=np.uint8)
    data = BytesIO()
    Image.fromarray(pixels).save(data, format='PNG')
    return SimpleUploadedFile(name, data.getvalue(), content_type='image/png')


@override_settings(STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.InMemoryStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
})
class CnnReportFlowTests(TestCase):
    """Upload photo -> CNN prediction -> category shown -> report saved, using the real model."""

    def setUp(self):
        self.user = User.objects.create_user(username='cnn-reporter', password='test-password')
        UserProfile.objects.create(user=self.user, role='student', department='CCS')
        self.client.force_login(self.user)
        building = Building.objects.create(name='CNN Building', code='CNN')
        self.floor = Floor.objects.create(building=building, floor_number=1, label='First Floor')
        self.room = Room.objects.create(floor=self.floor, name='CNN Room', code='CR')

    def predict(self, photo):
        return self.client.post(reverse('predictCategory'), {'photo': photo})

    def submit(self, photo, **extra):
        data = {
            'building': self.floor.building_id,
            'floor': self.floor.pk,
            'location': self.room.pk,
            'category': 'electrical',
            'description': 'The outlet near the door is broken.',
            'photo': photo,
            **extra,
        }
        return self.client.post(reverse('reportForm'), data)

    def test_cnn_prediction_is_preserved_and_browser_fields_ignored(self):
        with patch('user.views.predict_image', wraps=predictor.predict_image) as predict:
            response = self.predict(make_photo())
            self.assertEqual(response.status_code, 200)
            shown = response.json()
            self.assertTrue(shown['success'])
            self.assertIn(shown['category'], predictor.CATEGORY_MAP.values())

            # Forged CNN metadata and a different category must not reach the saved report
            response = self.submit(
                make_photo(),
                category='electrical' if shown['category'] != 'electrical' else 'plumbing',
                cnn_raw_prediction='Forged',
                cnn_confidence='0.01',
                cnn_margin='0.0',
                flagged_for_review='true' if not shown['flagged_for_review'] else 'false',
            )
        self.assertRedirects(response, reverse('userReports'), fetch_redirect_response=False)
        predict.assert_called_once()  # submission reused the stored result

        report = Report.objects.get(user=self.user)
        self.assertEqual(predictor.MODEL_PATH.name, 'faccheck_cnn_model.keras')
        self.assertEqual(report.category, shown['category'])
        self.assertEqual(report.cnn_raw_prediction, shown['raw_prediction'])
        self.assertAlmostEqual(report.cnn_confidence, shown['confidence'], places=6)
        self.assertAlmostEqual(report.cnn_margin, shown['margin'], places=6)
        self.assertEqual(report.flagged_for_review, shown['flagged_for_review'])
        self.assertNotIn('cnn_prediction', self.client.session)

    def test_submission_reruns_inference_when_photo_was_never_or_differently_predicted(self):
        self.predict(make_photo(seed=1))
        with patch('user.views.predict_image', wraps=predictor.predict_image) as predict:
            self.submit(make_photo(seed=2), cnn_confidence='0.99', cnn_raw_prediction='Forged')
        predict.assert_called_once()
        report = Report.objects.get(user=self.user)
        expected = predictor.predict_image(make_photo(seed=2))
        self.assertEqual(report.cnn_raw_prediction, expected['raw_label'])
        self.assertAlmostEqual(report.cnn_confidence, expected['confidence'], places=6)

    def test_unreadable_photo_returns_clear_error(self):
        bad = SimpleUploadedFile('broken.png', b'not an image', content_type='image/png')
        response = self.predict(bad)
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()['success'])
        self.assertFalse(response.json()['retry'])
        self.assertIn("couldn't read this photo", response.json()['error'])

    def test_inference_failure_allows_retry_and_manual_classification(self):
        with patch('user.views.predict_image', side_effect=RuntimeError('interpreter crashed')):
            response = self.predict(make_photo())
            self.assertEqual(response.status_code, 503)
            self.assertTrue(response.json()['retry'])
            self.assertIn('choose a category manually', response.json()['error'])

            response = self.submit(make_photo(), category='plumbing', cnn_confidence='0.99', flagged_for_review='false')
        self.assertRedirects(response, reverse('userReports'), fetch_redirect_response=False)
        report = Report.objects.get(user=self.user)
        self.assertEqual(report.category, 'plumbing')
        self.assertIsNone(report.cnn_raw_prediction)
        self.assertIsNone(report.cnn_confidence)
        self.assertIsNone(report.cnn_margin)
        self.assertTrue(report.flagged_for_review)

    def test_manual_category_is_kept_when_inference_recovers_before_submit(self):
        with patch('user.views.predict_image', side_effect=RuntimeError('interpreter crashed')):
            self.assertEqual(self.predict(make_photo()).status_code, 503)

        with patch('user.views.predict_image', wraps=predictor.predict_image) as predict:
            response = self.submit(make_photo(), category='plumbing')
        self.assertRedirects(response, reverse('userReports'), fetch_redirect_response=False)
        predict.assert_not_called()
        report = Report.objects.get(user=self.user)
        self.assertEqual(report.category, 'plumbing')
        self.assertIsNone(report.cnn_raw_prediction)
        self.assertTrue(report.flagged_for_review)
        self.assertNotIn('cnn_prediction_failed', self.client.session)

    def test_report_form_exposes_retry_path_and_no_cnn_hidden_fields(self):
        html = self.client.get(reverse('reportFormAjax')).json()['html']
        self.assertIn('id="cnn-retry-btn"', html)
        self.assertNotIn('name="cnn_confidence"', html)
        self.assertNotIn('name="flagged_for_review"', html)
