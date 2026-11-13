"""Pure tests: no internet, no account creation and no paid generation."""
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from io import BytesIO

from msw_engine.free_ai_fallback import Router, FailClosed, HORD_BASE


class RouterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.calls = []
        self.now = [20000]

    def router(self, handler, models=None):
        def capture(*args):
            self.calls.append(args)
            return handler(*args)
        return Router(Path(self.tmp.name), call=capture, clock=lambda: self.now[0], models=models or {})

    def test_public_horde_submission_and_poll(self):
        def handler(method, url, body, headers):
            if method == 'POST':
                self.assertEqual(headers['apikey'], '0000000000')
                return {'id': '12345678-1234-1234-1234-123456789abc'}
            return {'done': True, 'generations': [{'text': 'MSW-OK'}]}
        router = self.router(handler)
        result = router.submit('msw-horde-0001', 'Wiadomosc publiczna', classification='public')
        self.assertEqual(result['state'], 'provider_pending')
        self.assertEqual(router.poll('msw-horde-0001')['text'], 'MSW-OK')
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(router.submit('msw-horde-0001', 'Wiadomosc publiczna', classification='public')['state'], 'completed')
        self.assertEqual(len(self.calls), 2)

    def test_private_data_never_sent_to_crowd(self):
        router = self.router(lambda *x: self.fail('network called'))
        self.assertEqual(router.submit('msw-private-001', 'Dane klienta', classification='internal')['state'], 'no_provider_available')
        self.assertEqual(len(self.calls), 0)

    def test_local_first(self):
        router = self.router(lambda method, url, body, hdr: {'choices': [{'message': {'content': 'lokalnie'}}]},
                             models={'ollama': 'qwen-small'})
        got = router.submit('msw-local-001', 'Pytanie publiczne', classification='public')
        self.assertEqual(got['provider'], 'ollama')
        self.assertEqual(got['text'], 'lokalnie')
        self.assertTrue(self.calls[0][1].startswith('http://127.0.0.1'))

    def test_paid_transport_not_touched(self):
        router = self.router(lambda *x: {'id': '12345678-1234-1234-1234-123456789abc'})
        self.assertEqual(router.catalog()['external_paid_api_calls'], 0)
        self.assertEqual(set(router.catalog()['free_providers']), {'ollama','llama_cpp','ai_horde'})

    def test_unknown_submission_not_reissued(self):
        router = self.router(lambda *x: (_ for _ in ()).throw(TimeoutError()))
        result = router.submit('msw-unknown-001', 'Publiczne dane', classification='public')
        self.assertEqual(result['state'], 'outcome_unknown')
        router.submit('msw-unknown-001', 'Publiczne dane', classification='public')
        self.assertEqual(len(self.calls), 1)

    def test_duplicate_identity_conflict(self):
        router = self.router(lambda *x: {'id': '12345678-1234-1234-1234-123456789abc'})
        router.submit('msw-conflict-001', 'pierwsze', classification='public')
        with self.assertRaisesRegex(FailClosed, 'identity_conflict'):
            router.submit('msw-conflict-001', 'drugie', classification='public')

    def test_429_cooldown_honors_retry_after(self):
        def handler(method, url, body, hdr):
            if '127.0.0.1' in url:
                raise HTTPError(url,429,'Rate limited', {'Retry-After':'3600'}, BytesIO())
            return {'id': '12345678-1234-1234-1234-123456789abc'}
        # Test injection must translate network response into module's definitive rejection.
        from msw_engine.free_ai_fallback import DefinitiveUnavailable
        def rejection(method,url,body,hdr):
            if '127.0.0.1' in url:
                raise DefinitiveUnavailable(429,3600)
            return {'id': '12345678-1234-1234-1234-123456789abc'}
        router = self.router(rejection, models={'ollama':'a'})
        self.assertEqual(router.submit('msw-rate-00001', 'Publiczne dane', classification='public')['provider'], 'ai_horde')
        self.assertGreater(router.catalog()['free_providers']['ollama']['cooldown']['until'], self.now[0]+3590)

    def test_no_resend_after_restart(self):
        router = self.router(lambda *x: {'id': '12345678-1234-1234-1234-123456789abc'})
        router.submit('msw-restart-01', 'Publiczne dane', classification='public')
        newer = self.router(lambda *x: self.fail('second submission'))
        self.assertEqual(newer.submit('msw-restart-01', 'Publiczne dane', classification='public')['state'], 'provider_pending')

    def test_20minute_cycle_does_not_reset_provider_limits(self):
        from msw_engine.free_ai_fallback import DefinitiveUnavailable
        router = self.router(lambda method,url,body,headers: (_ for _ in ()).throw(DefinitiveUnavailable(429, 3600)))
        router.submit('msw-ratelimit-01', 'public', classification='public')
        self.now[0] += 1200
        self.assertEqual(router.catalog()['free_providers']['ai_horde']['cooldown']['status'],429)
        self.assertEqual(router.submit('msw-ratelimit-02', 'public', classification='public')['state'],'no_provider_available')

    def test_query_confidential_requires_internal_and_stays_local(self):
        router = self.router(lambda *x: {'choices':[{'message': {'content': 'OK'}}]}, models={'llama_cpp':'small'})
        self.assertEqual(router.submit('msw-conf-0012','Dane klienta',classification='internal')['provider'], 'llama_cpp')
        self.assertNotIn('aihorde.net', self.calls[0][1])

    def test_parallel_request_claims_only_one_generation(self):
        import time
        barrier = threading.Barrier(2)
        def slow(*args):
            time.sleep(0.03)
            return {'id':'12345678-1234-1234-1234-123456789abc'}
        router = self.router(slow)
        results = []
        def runner():
            barrier.wait()
            results.append(router.submit('msw-thread-0001', 'Publiczny prompt', classification='public'))
        pair = [threading.Thread(target=runner) for _ in range(2)]
        for th in pair: th.start()
        for th in pair: th.join()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(len(results), 2)
        self.assertTrue(all(r['state'] in {'sending','routing','provider_pending'} for r in results))

    def test_invalid_request_rejected(self):
        router = self.router(lambda *x: self.fail('should not call'))
        with self.assertRaises(ValueError):
            router.submit('x', 'public', classification='public')
        with self.assertRaises(ValueError):
            router.submit('msw-valid-001','public',classification='unknown')

if __name__ == '__main__':
    unittest.main()
