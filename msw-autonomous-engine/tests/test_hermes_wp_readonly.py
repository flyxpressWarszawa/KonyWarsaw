import io
import json
import unittest
from urllib.error import HTTPError, URLError

from msw_engine.hermes_wp_readonly import inspect_wordpress_hermes, _strict_status

SITE = 'https://mobilny-serwis-warszawa.pl'
NODE = 'readonly-node'
SECRET = 's' * 32  # synthetic test data, not a live secret
GOOD = {'schema': 'msw.hermes.agent-repair.v1',
        'dependencies': {'remote_store': True, 'runtime_bridge': True, 'database_reader': True},
        'automatic_reissue': False}


class Response:
    def __init__(self, body, code=200):
        self.data = io.BytesIO(body)
        self.code = code

    def getcode(self):
        return self.code

    def read(self, n):
        return self.data.read(n)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class Opener:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def open(self, request, timeout):
        self.calls.append((request, timeout))
        if isinstance(self.response, BaseException):
            raise self.response
        return self.response


def probe(data=GOOD):
    return Opener(Response(json.dumps(data).encode()))


class HermesWordPressReadonlyTest(unittest.TestCase):
    def test_single_call_exact_endpoint_no_reissue(self):
        fake = probe()
        result = inspect_wordpress_hermes(SITE, NODE, SECRET, opener=fake)
        self.assertEqual(result['state'], 'OBSERVED_ONLY')
        self.assertFalse(result['hermes_control_channel_verified'])
        self.assertFalse(result['production_execution_authorized'])
        self.assertFalse(result['signed_stop_hold_epoch_verified'])
        self.assertEqual(result['provider_calls'], 0)
        self.assertEqual(result['job_claims'], 0)
        self.assertFalse(result['automatic_reissue'])
        self.assertEqual(len(fake.calls), 1)
        request, timeout = fake.calls[0]
        self.assertEqual(timeout, 12)
        self.assertEqual(request.get_method(), 'POST')
        self.assertEqual(request.full_url, SITE + '/wp-json/msw-hermes-repair/v1/status')
        self.assertEqual(request.data, b'{}')
        self.assertEqual(request.get_header('X-mswue-action'), 'status')
        self.assertNotIn('s' * 32, str(request.headers))

    def test_bad_payload_holds(self):
        cases = [
            {**GOOD, 'schema': 'msw.agent.runtime.v1'},
            {**GOOD, 'automatic_reissue': True},
            {**GOOD, 'automatic_reissue': 'false'},
            {**GOOD, 'dependencies': {**GOOD['dependencies'], 'remote_store': 'true'}},
            {**GOOD, 'dependencies': {'remote_store': True, 'runtime_bridge': True}},
            {**GOOD, 'dependencies': {**GOOD['dependencies'], 'other': True}},
            {**GOOD, 'ready': True},
            {}, [], 'ok', None,
        ]
        for item in cases:
            with self.subTest(case=item):
                fake = probe(item)
                result = inspect_wordpress_hermes(SITE, NODE, SECRET, opener=fake)
                self.assertEqual(result['state'], 'HOLD')
                self.assertFalse(result['production_execution_authorized'])
                self.assertEqual(len(fake.calls), 1)

    def test_network_and_http_errors_hold_once(self):
        for problem in [HTTPError(SITE, 403, 'Forbidden', {}, io.BytesIO(b'secret-body')),
                        HTTPError(SITE, 429, 'Limit', {}, io.BytesIO()),
                        URLError('offline'), TimeoutError('timeout'), OSError('network')]:
            with self.subTest(reason=type(problem).__name__):
                fake = Opener(problem)
                result = inspect_wordpress_hermes(SITE, NODE, SECRET, opener=fake)
                self.assertEqual(result['state'], 'HOLD')
                self.assertEqual(len(fake.calls), 1)
                self.assertNotIn('secret-body', str(result))

    def test_invalid_json_and_large_response_hold(self):
        for body in [b'not-json', b'{' + b'X' * 5000]:
            fake = Opener(Response(body))
            self.assertEqual(inspect_wordpress_hermes(SITE, NODE, SECRET, opener=fake)['state'], 'HOLD')

    def test_invalid_origin_or_credential_never_calls_remote(self):
        for site in ['http://mobilny-serwis-warszawa.pl', SITE + '.example',
                     SITE + ':443', SITE + '/wp-admin', SITE + '?x=1',
                     'https://user:pass@mobilny-serwis-warszawa.pl']:
            fake = probe()
            with self.subTest(site=site), self.assertRaises(ValueError):
                inspect_wordpress_hermes(site, NODE, SECRET, opener=fake)
            self.assertFalse(fake.calls)
        for node, secret in [('bad node', SECRET), (NODE, 'short'), (NODE, 'CHANGE_PLACEHOLDER' * 4)]:
            fake = probe()
            with self.subTest(node=node), self.assertRaises(ValueError):
                inspect_wordpress_hermes(SITE, node, secret, opener=fake)
            self.assertFalse(fake.calls)

    def test_parser_rejects_spoofed_status(self):
        self.assertTrue(_strict_status(GOOD))
        self.assertFalse(_strict_status({'schema': GOOD['schema'], 'dependencies': GOOD['dependencies']}))


if __name__ == '__main__':
    unittest.main()
