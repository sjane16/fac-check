from unittest.mock import patch

from django.test import RequestFactory, SimpleTestCase
from django.urls import Resolver404, resolve, reverse

from .views import UDM_FAQ_DATA, chatbot_response


class ChatbotResponseTests(SimpleTestCase):
    def test_reporting_faq_starts_from_signed_in_page(self):
        self.assertIn('Go to the report submission page', UDM_FAQ_DATA)
        self.assertNotIn('create an account and log in', UDM_FAQ_DATA)

    @patch('user.views._chatbot_rate_limited', return_value=None)
    @patch('user.views._get_gemini_client', side_effect=RuntimeError('service unavailable'))
    @patch('user.views.logger.exception')
    def test_service_error_is_logged_and_hidden_from_reply(self, log_exception, _client, _limit):
        request = RequestFactory().post('/user/chatbot/response/', {'message': 'How do I report?'})
        response = chatbot_response(request)

        self.assertEqual(response.status_code, 200)
        self.assertNotIn('service unavailable', response.content.decode())
        log_exception.assert_called_once_with('Chatbot response generation failed')


class UnusedPageRouteTests(SimpleTestCase):
    def test_standalone_layout_and_chatbot_pages_are_not_routed(self):
        for path in ('/user/base/', '/user/chatbot/', '/adm/base/'):
            with self.subTest(path=path), self.assertRaises(Resolver404):
                resolve(path)

    def test_chatbot_response_endpoint_remains_routed(self):
        self.assertEqual(reverse('chatbot_response'), '/user/chatbot/response/')
