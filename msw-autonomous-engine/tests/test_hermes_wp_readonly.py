import io
import json
import hashlib
import hmac
import os
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from msw_engine.hermes_wp_readonly import inspect_wordpress_hermes, _strict_status, main

from msw_engine.client import signed_headers

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


    def test_status_hmac_matches_existing_php_remote_store_contract(self):
        # RemoteStore.php: HMAC-SHA256 over POST, action, node, stamp, nonce,
        # and lowercase SHA-256 of the exact original request body.
        body = b'{}'
        stamp, nonce = '1794425819', 'a' * 32
        header = signed_headers('status', NODE, SECRET, body, timestamp=stamp, nonce=nonce)
        canonical = '\n'.join(['POST', 'status', NODE, stamp, nonce, hashlib.sha256(body).hexdigest()])
        expected = hmac.new(SECRET.encode(), canonical.encode(), hashlib.sha256).hexdigest()
        self.assertEqual(header['X-MSWUE-Signature'], expected)
        self.assertEqual(header['X-MSWUE-Nonce'], nonce)
        self.assertEqual(header['X-MSWUE-Action'], 'status')

    def test_cli_disabled_is_zero_io(self):
        output = io.StringIO()
        with patch.dict(os.environ, {'MSW_HERMES_WP_STATUS_ONCE': '0'}, clear=True), \
                patch('msw_engine.hermes_wp_readonly.inspect_wordpress_hermes') as remote, \
                redirect_stdout(output):
            exit_code = main()
        self.assertEqual(exit_code, 2)
        remote.assert_not_called()
        self.assertEqual(json.loads(output.getvalue())['code'], 'explicit_opt_in_required')

    def test_cli_invalid_origin_is_local_hold_without_network(self):
        output = io.StringIO()
        env = {'MSW_HERMES_WP_STATUS_ONCE': '1', 'MSW_WP_URL': 'http://invalid.example',
               'MSW_NODE': NODE, 'MSW_SECRET': SECRET}
        with patch.dict(os.environ, env, clear=True), \
                patch('msw_engine.hermes_wp_readonly.build_opener') as opener, \
                redirect_stdout(output):
            exit_code = main()
        self.assertEqual(exit_code, 2)
        opener.assert_not_called()
        payload = json.loads(output.getvalue())
        self.assertEqual(payload['state'], 'HOLD')
        self.assertEqual(payload['code'], 'invalid_local_configuration')
        self.assertNotIn(SECRET, output.getvalue())

    def test_cli_opted_in_sanitized_result_not_production_authority(self):
        env = {'MSW_HERMES_WP_STATUS_ONCE': '1', 'MSW_WP_URL': SITE,
               'MSW_NODE': NODE, 'MSW_SECRET': SECRET}
        for state, exit_code in [('OBSERVED_ONLY', 0), ('HOLD', 2)]:
            output = io.StringIO()
            data = {
                'schema': 'msw.hermes.wordpress.readonly.v1',
                'state': state, 'code': 'fixture', 'production_execution_authorized': False,
                'hermes_control_channel_verified': False, 'signed_stop_hold_epoch_verified': False
            }
            with self.subTest(state=state), patch.dict(os.environ, env, clear=True), \
                    patch('msw_engine.hermes_wp_readonly.inspect_wordpress_hermes', return_value=data) as remote, \
                    redirect_stdout(output):
                actual_exit = main()
            self.assertEqual(actual_exit, exit_code)
            remote.assert_called_once_with(SITE, NODE, SECRET)
            printed = output.getvalue()
            self.assertEqual(json.loads(printed)['state'], state)
            self.assertNotIn(SECRET, printed)
            self.assertFalse(json.loads(printed)['production_execution_authorized'])


if __name__ == '__main__':
    unittest.main()
