from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from .context_processors import get_unread_notifications_count


class UnreadNotificationCountTests(SimpleTestCase):
    @patch("user.context_processors.notifications_for_user")
    def test_count_is_queried_once_per_request(self, filter_notifications):
        filter_notifications.return_value.count.return_value = 3
        request = SimpleNamespace(
            user=SimpleNamespace(is_authenticated=True),
        )

        self.assertEqual(get_unread_notifications_count(request), 3)
        self.assertEqual(get_unread_notifications_count(request), 3)
        filter_notifications.assert_called_once_with(request.user, 'unread')
