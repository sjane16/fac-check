from unittest.mock import patch

from django.test import SimpleTestCase
from django.urls import reverse


class PublicPageRegressionTests(SimpleTestCase):
    def test_forgot_password_has_labeled_email_without_empty_image(self):
        response = self.client.get(reverse('forgot_password'))
        self.assertContains(response, '<label for="id_email"')
        self.assertNotContains(response, '<img />')

    @patch('accounts.views.is_ratelimited', return_value=True)
    def test_forgot_password_keeps_email_after_rate_limit(self, _limited):
        response = self.client.post(reverse('forgot_password'), {
            'email': 'student@udm.edu.ph',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'value="student@udm.edu.ph"')

    def test_landing_skip_link_is_above_header(self):
        response = self.client.get(reverse('index'))
        self.assertContains(response, 'fc-skip-link')
        self.assertContains(response, 'Awaiting review')
