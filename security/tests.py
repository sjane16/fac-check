from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, override_settings

from .middleware import ClientIPFromProxyMiddleware, client_ip


def _seen_ip(request):
    return HttpResponse(request.META['REMOTE_ADDR'])


class ClientIPFromProxyTests(SimpleTestCase):
    def setUp(self):
        self.middleware = ClientIPFromProxyMiddleware(_seen_ip)

    def request(self, forwarded=None):
        extra = {'REMOTE_ADDR': '10.0.0.1'}  # the proxy's own address
        if forwarded is not None:
            extra['HTTP_X_FORWARDED_FOR'] = forwarded
        return RequestFactory().get('/', **extra)

    @override_settings(BEHIND_PROXY=True)
    def test_uses_the_address_the_proxy_added(self):
        response = self.middleware(self.request('203.0.113.7'))
        self.assertEqual(response.content.decode(), '203.0.113.7')

    @override_settings(BEHIND_PROXY=True)
    def test_ignores_addresses_typed_in_by_the_visitor(self):
        # A visitor can send their own X-Forwarded-For; the proxy appends the real address last
        response = self.middleware(self.request('1.2.3.4, 203.0.113.7'))
        self.assertEqual(response.content.decode(), '203.0.113.7')

    @override_settings(BEHIND_PROXY=True)
    def test_keeps_remote_addr_without_the_header(self):
        response = self.middleware(self.request())
        self.assertEqual(response.content.decode(), '10.0.0.1')

    @override_settings(BEHIND_PROXY=False)
    def test_header_is_ignored_when_not_behind_a_proxy(self):
        response = self.middleware(self.request('203.0.113.7'))
        self.assertEqual(response.content.decode(), '10.0.0.1')

    def test_axes_uses_the_same_address(self):
        request = self.request('1.2.3.4')
        request.META['REMOTE_ADDR'] = '203.0.113.7'
        self.assertEqual(client_ip(request), '203.0.113.7')
