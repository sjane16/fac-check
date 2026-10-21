from types import SimpleNamespace
from unittest.mock import patch

from django.test import RequestFactory, SimpleTestCase

from user.views import exportJobOrderPDF


class JobOrderExportStatusTests(SimpleTestCase):
    def test_pending_is_blocked_before_pdf_generation(self):
        request = RequestFactory().get('/user/job-order/1/pdf/')
        request.user = SimpleNamespace(is_authenticated=True)
        with patch('user.views.get_object_or_404', return_value=SimpleNamespace(status='pending')) as lookup, patch('user.views.pisa.CreatePDF') as pdf:
            response = exportJobOrderPDF(request, 1)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(lookup.call_args.kwargs['user'], request.user)
        pdf.assert_not_called()

    def test_export_is_available_for_each_started_status(self):
        for status in ('in_progress', 'resolved', 'closed'):
            with self.subTest(status=status):
                request = RequestFactory().get('/user/job-order/1/pdf/')
                request.user = SimpleNamespace(is_authenticated=True)
                report = SimpleNamespace(status=status, category='electrical', job_order_id='TEST', id=1)
                with patch('user.views.get_object_or_404', return_value=report), patch('user.views.render_to_string', return_value='<p>Job order</p>'), patch('user.views.pisa.CreatePDF', return_value=SimpleNamespace(err=0)) as pdf:
                    response = exportJobOrderPDF(request, 1)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response['Content-Type'], 'application/pdf')
                pdf.assert_called_once()
