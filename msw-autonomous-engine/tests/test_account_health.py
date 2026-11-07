import io
import json
import unittest

from msw_engine.account_health import SCHEMA, read_account_health


class Response:
    def __init__(self, value): self.value = value
    def __enter__(self): return self
    def __exit__(self, *_): pass
    def read(self, _): return self.value


class Client:
    wordpress_url = 'https://mobilny-serwis-warszawa.pl'
    node = 'msw-render-central-1'
    secret = 'x' * 40
    def __init__(self, value): self.value, self.opener = value, self
    def open(self, request, timeout):
        assert request.full_url.startswith(self.wordpress_url + '/') and timeout == 12
        assert request.headers['X-mswue-action'] == 'status'
        return Response(self.value)


class AccountHealthTests(unittest.TestCase):
    def check(self, value):
        return read_account_health(Client(json.dumps(value).encode()), clock=lambda: 1000)

    def test_fresh_scoped_metrics(self):
        value = {'schema': SCHEMA, 'state': 'fresh', 'measured_at': 999,
                 'cpu_percent': 69, 'physical_memory_percent': 30}
        self.assertEqual(self.check(value)['state'], 'fresh')
        for key, bad in [('measured_at', 909), ('measured_at', 1001), ('cpu_percent', True),
                         ('cpu_percent', -1), ('physical_memory_percent', 101), ('state', 'unavailable')]:
            with self.subTest(key=key, bad=bad):
                self.assertEqual(self.check({**value, key: bad})['state'], 'unavailable')

    def test_bad_data_is_unknown(self):
        for value in [b'{', b'x' * 8193, b'[]', b'null']:
            self.assertEqual(read_account_health(Client(value), clock=lambda: 1000)['state'], 'unavailable')


if __name__ == '__main__': unittest.main()
