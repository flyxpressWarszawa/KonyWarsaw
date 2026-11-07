import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from msw_engine.client import TransportError
from msw_engine.doctor_host_guard import HostPressureGuard
from msw_engine.runner import Worker


class Clock:
    def __init__(self): self.now = 0
    def __call__(self): return self.now


class Client:
    wordpress_url = 'https://mobilny-serwis-warszawa.pl'
    def __init__(self, response=None):
        self.calls = []
        self.response = response if response is not None else {'state': 'idle'}
    def send(self, action, fields):
        self.calls.append((action, fields))
        return self.response


class HostGuardTests(unittest.TestCase):
    def test_pressure_filters_callbacks_and_requires_two_cool_samples(self):
        c = Clock()
        readings = iter([{'state': 'fresh', 'at': 10000, 'cpu_percent': 95, 'physical_memory_percent': 20},
                         {'state': 'fresh', 'at': 10060, 'cpu_percent': 30, 'physical_memory_percent': 20},
                         {'state': 'fresh', 'at': 10120, 'cpu_percent': 30, 'physical_memory_percent': 20}])
        guard = HostPressureGuard(None, reader=lambda _: next(readings), clock=c, wall_clock=lambda: 10000 + c.now)
        kinds = ['module.execute', 'runtime.catalog', 'engine.v3.ingest', 'seo.analyze', 'knowledge.search']
        self.assertEqual(guard.capabilities(kinds)[0], ['seo.analyze', 'knowledge.search'])
        guard.mark_claim()
        self.assertFalse(guard.claim_allowed())
        c.now = 60
        self.assertEqual(guard.capabilities(kinds)[0], ['seo.analyze', 'knowledge.search'])
        self.assertTrue(guard.claim_allowed())
        c.now = 120
        self.assertEqual(guard.capabilities(kinds)[0], kinds)

    def test_unavailable_telemetry_is_not_healthy_and_does_not_clear_prior_pressure(self):
        c = Clock()
        readings = iter([{'state': 'fresh', 'at': 10000, 'cpu_percent': 70, 'physical_memory_percent': 20},
                         {'state': 'unavailable'}, {'state': 'fresh', 'at': 10120, 'cpu_percent': True, 'physical_memory_percent': 20}])
        guard = HostPressureGuard(None, reader=lambda _: next(readings), clock=c, wall_clock=lambda: 10000 + c.now)
        self.assertTrue(guard.capabilities(['module.execute'])[1]['pressure_active'])
        for t in (60, 120):
            c.now = t
            kinds, status = guard.capabilities(['module.execute', 'seo.analyze'])
            self.assertEqual(kinds, ['seo.analyze'])
            self.assertEqual(status['state'], 'unavailable')
            self.assertTrue(status['pressure_active'])

    def test_unknown_at_start_preserves_availability_but_is_not_reported_healthy(self):
        guard = HostPressureGuard(None, reader=lambda _: {'state': 'unavailable'}, clock=lambda: 0)
        kinds, status = guard.capabilities(['module.execute'])
        self.assertEqual(kinds, ['module.execute'])
        self.assertEqual(status, {'state': 'unavailable', 'pressure_active': False})

    def test_worker_does_not_send_empty_capabilities_or_execute_unexpected_claim(self):
        clock = Clock()
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'MSW_HOST_PRESSURE_GUARD': '1'}):
            client = Client()
            worker = Worker(client, Path(folder))
            worker.host_guard = HostPressureGuard(client, reader=lambda _: {
                'state': 'fresh', 'at': 10000, 'cpu_percent': 95, 'physical_memory_percent': 20}, clock=clock, wall_clock=lambda: 10000)
            with patch('msw_engine.runner.handlers', return_value={'module.execute': object()}):
                self.assertEqual(worker.run_once()['state'], 'doctor_host_deferred')
                self.assertEqual(client.calls, [])
            client.response = {'state': 'claimed', 'job': {'kind': 'module.execute'}, 'lease_token': 'lease'}
            with patch('msw_engine.runner.handlers', return_value={'seo.analyze': object(), 'module.execute': object()}):
                with self.assertRaisesRegex(TransportError, 'host_guard_claim_outside_capabilities'):
                    worker.run_once()
                self.assertEqual(client.calls[0], ('claim', {'capabilities': ['seo.analyze']}))
                self.assertEqual(worker.run_once()['state'], 'doctor_host_deferred')

    def test_worker_guard_is_opt_in(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {}, clear=True):
            worker = Worker(Client(), Path(folder))
            self.assertIsNone(worker.host_guard)


if __name__ == '__main__':
    unittest.main()
