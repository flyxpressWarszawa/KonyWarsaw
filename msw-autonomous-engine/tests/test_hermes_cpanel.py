import unittest

from msw_engine.hermes_cpanel import HOSTS, once


class HermesCpanelTest(unittest.TestCase):
    def test_complete_readback(self):
        receipt = {'schema': 'msw.hermes.cpanel.receipt.v1', 'request_id': None,
                   'checked_at': '2026-11-12T00:00:00Z', 'sha256': 'a'*64,
                   'results': {host: {'verified': True, 'main_domain': 'example.pl'} for host in HOSTS}}

        def call(endpoint, body, secret):
            self.assertEqual(secret, 'b'*64)
            if endpoint == 'status':
                receipt['request_id'] = body['request_id']
            return dict(receipt)

        self.assertTrue(once('b'*64, call=call)['verified'])

    def test_missing_host_is_not_success(self):
        receipt = {'schema': 'msw.hermes.cpanel.receipt.v1', 'request_id': None,
                   'checked_at': '2026-11-12T00:00:00Z', 'results': {}}

        def call(endpoint, body, secret):
            receipt['request_id'] = body['request_id']
            return dict(receipt)

        with self.assertRaisesRegex(ValueError, 'broker_receipt_mismatch'):
            once('b'*64, call=call)


if __name__ == '__main__':
    unittest.main()
