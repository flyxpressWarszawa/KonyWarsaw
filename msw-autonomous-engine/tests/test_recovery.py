from email.message import Message
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError
from urllib.request import Request

from msw_engine.recovery import RecoveryCircuit, OriginPaused, protected_open


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.now = [1000.0]
        self.origin = 'https://mobilny-serwis-warszawa.pl'
        self.circuit = RecoveryCircuit(self.tmp.name, clock=lambda: self.now[0])

    def fail(self, circuit=None):
        circuit = circuit or self.circuit
        circuit.enter(self.origin, 45)
        circuit.failure(self.origin)

    def test_three_failures_pause_other_processes_and_only_one_probe_recovers(self):
        for _ in range(3):
            self.fail()
        other = RecoveryCircuit(self.tmp.name, clock=lambda: self.now[0])
        with self.assertRaises(OriginPaused):
            other.enter(self.origin, 45)
        self.now[0] += 61
        self.assertTrue(other.enter(self.origin, 45))
        with self.assertRaises(OriginPaused):
            self.circuit.enter(self.origin, 45)
        other.success(self.origin, True)
        self.assertFalse(self.circuit.enter(self.origin, 45))

    def test_dead_probe_is_recovered_without_permanent_lock(self):
        for _ in range(3):
            self.fail()
        self.now[0] += 61
        self.assertTrue(self.circuit.enter(self.origin, 45))
        self.now[0] += 61
        self.assertTrue(self.circuit.enter(self.origin, 45))

    def test_satellites_remain_independent_during_central_outage(self):
        for _ in range(3):
            self.fail()
        self.assertFalse(self.circuit.enter('https://another-owner-site.pl', 30))

    def test_spaced_failures_do_not_trip_circuit(self):
        for _ in range(5):
            self.fail()
            self.now[0] += 61
        self.assertFalse(self.circuit.enter(self.origin, 45))

    def test_http_503_retry_after_is_respected_and_no_write_is_replayed(self):
        opener = MagicMock()
        headers = Message()
        headers['Retry-After'] = '300'
        opener.open.side_effect = HTTPError(self.origin, 503, 'busy', headers, None)
        request = Request(self.origin + '/wp-json/test', data=b'one write')
        with patch.dict('os.environ', {'MSW_DATA_DIR': self.tmp.name}):
            for _ in range(3):
                with self.assertRaises(HTTPError):
                    with protected_open(opener, request, timeout=45):
                        pass
            with self.assertRaises(OriginPaused) as context:
                with protected_open(opener, request, timeout=45):
                    pass
        self.assertGreaterEqual(context.exception.retry_after, 298)
        self.assertEqual(opener.open.call_count, 3)

    def test_http_504_and_timeout_also_open_circuit(self):
        for error in (HTTPError(self.origin, 504, 'busy', Message(), None), TimeoutError()):
            with tempfile.TemporaryDirectory() as directory:
                opener = MagicMock()
                opener.open.side_effect = error
                with patch.dict('os.environ', {'MSW_DATA_DIR': directory}):
                    for _ in range(3):
                        with self.assertRaises(type(error)):
                            with protected_open(opener, Request(self.origin), timeout=45):
                                pass
                    with self.assertRaises(OriginPaused):
                        with protected_open(opener, Request(self.origin), timeout=45):
                            pass

    def test_authorization_error_never_becomes_outage_retry(self):
        opener = MagicMock()
        opener.open.side_effect = HTTPError(self.origin, 403, 'scope', Message(), None)
        with patch.dict('os.environ', {'MSW_DATA_DIR': self.tmp.name}):
            for _ in range(5):
                with self.assertRaises(HTTPError):
                    with protected_open(opener, Request(self.origin), timeout=45):
                        pass
        self.assertEqual(opener.open.call_count, 5)


if __name__ == '__main__':
    unittest.main()
