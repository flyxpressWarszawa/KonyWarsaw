from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from msw_engine.client import TransportError
from msw_engine.doctor_compute_lane import ComputeLane


class Client:
    def __init__(self, response): self.response, self.calls = response, []
    def send(self, action, payload):
        self.calls.append((action, payload))
        if action == 'claim': return self.response
        if action == 'status': return {'state': 'completed', 'result_sha256': 'b' * 64}
        raise AssertionError(action)


class Journal:
    def __init__(self): self.receipt = None
    def lookup(self, ident, digest): return self.receipt
    def mark(self, ident, state): self.receipt['state'] = state


class Worker:
    fail_ack = False
    def __init__(self, client, directory, **kwargs):
        self.client, self.journal = client, Journal()
    def execute(self, job, lease):
        self.journal.receipt = {'job_id': job['id'], 'payload_sha256': job['payload_sha256'],
            'result_sha256': 'b' * 64, 'state': 'pending'}
        return self.journal.receipt
    def acknowledge(self, receipt):
        if self.fail_ack: raise TransportError('transport_unavailable')
        receipt['state'] = 'acknowledged'
        return {'state': 'completed', 'server_ack_verified': True}


class ComputeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.job = {'id': 'example-123', 'kind': 'seo.analyze', 'payload_sha256': 'a' * 64}
        self.client = Client({'state': 'claimed', 'job': self.job, 'lease_token': 'lease'})
        self.lane = ComputeLane(self.root, client=self.client, worker_factory=Worker, clock=lambda: 1000)
        self.site = {'state': 'reachable', 'sample_fresh': True}
        self.render = {'measurement': {'fresh': True, 'cpu_percent': 3, 'memory_percent': 40}}

    def step(self):
        with patch('msw_engine.doctor_compute_lane.hosting_status', return_value=self.site), \
             patch('msw_engine.doctor_compute_lane.render_status', return_value=self.render):
            return self.lane.step()

    def test_executes_only_lease_filtered_analysis_and_records_verified_ack(self):
        result = self.step()
        self.assertEqual(self.client.calls, [('claim', {'capabilities': ['seo.analyze']})])
        self.assertEqual(result['state'], 'completed')
        self.assertTrue(result['server_ack_verified'])
        self.assertFalse(result['hosting_cpu_measured'])
        self.assertFalse(result['publication_performed'])
        self.assertFalse(self.lane.pending.exists())

    def test_pressure_or_missing_site_defers_without_claim(self):
        self.render['measurement']['cpu_percent'] = 70
        self.assertEqual(self.step()['state'], 'deferred_missing_health_or_render_pressure')
        self.assertFalse(self.client.calls)
        self.render['measurement']['cpu_percent'] = 3
        self.site['sample_fresh'] = False
        self.assertEqual(self.step()['state'], 'deferred_missing_health_or_render_pressure')
        self.assertFalse(self.client.calls)

    def test_rejects_unexpected_kind_without_executing(self):
        self.job['kind'] = 'module.execute'
        with self.assertRaisesRegex(ValueError, 'unexpected_claim'): self.step()
        self.assertIsNone(self.lane.worker.journal.receipt)

    def test_uncertain_ack_is_persisted_then_verified_without_repeating_job(self):
        self.lane.worker.fail_ack = True
        self.assertEqual(self.step()['state'], 'result_ack_unverified')
        self.assertTrue(self.lane.pending.exists())
        self.assertEqual(self.step()['state'], 'previous_result_acknowledged')
        self.assertFalse(self.lane.pending.exists())
        self.assertEqual([c[0] for c in self.client.calls], ['claim', 'status'])

    def test_unfinished_claim_is_not_replaced_with_another(self):
        self.lane.write(self.lane.pending, {'id': 'example-123', 'digest': 'a' * 64, 'created': 990})
        self.assertEqual(self.step()['state'], 'waiting_for_unfinished_lease')
        self.assertFalse(self.client.calls)


if __name__ == '__main__': unittest.main()
