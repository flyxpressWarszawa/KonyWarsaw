from pathlib import Path
import hashlib
import json
import tempfile
import unittest
from unittest.mock import patch

from msw_engine.client import TransportError
from msw_engine.doctor_compute_lane import COMPUTE_KINDS, ComputeLane
from msw_engine.runner import Worker as RealWorker


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
        self.lane = ComputeLane(self.root, client=self.client, worker_factory=Worker,
                                supported_kinds=COMPUTE_KINDS, clock=lambda: 1000)
        self.site = {'state': 'reachable', 'sample_fresh': True}
        self.render = {'measurement': {'fresh': True, 'cpu_percent': 3, 'memory_percent': 40}}

    def step(self):
        with patch('msw_engine.doctor_compute_lane.hosting_status', return_value=self.site), \
             patch('msw_engine.doctor_compute_lane.render_status', return_value=self.render):
            return self.lane.step()

    def test_executes_only_lease_filtered_analysis_and_records_verified_ack(self):
        result = self.step()
        self.assertEqual(self.client.calls, [('claim', {'capabilities': list(COMPUTE_KINDS)})])
        self.assertEqual(result['state'], 'completed')
        self.assertTrue(result['server_ack_verified'])
        self.assertFalse(result['hosting_cpu_measured'])
        self.assertFalse(result['publication_performed'])
        self.assertFalse(self.lane.pending.exists())

    def test_other_local_compute_kinds_are_executed_and_identified(self):
        for kind in COMPUTE_KINDS[1:]:
            with self.subTest(kind=kind):
                self.job['kind'] = kind
                result = self.step()
                self.assertEqual(result['kind'], kind)
                self.assertEqual(result['state'], 'completed')
                self.assertTrue(result['server_ack_verified'])

    def test_advertises_only_handlers_available_in_runtime(self):
        self.lane = ComputeLane(self.root, client=self.client, worker_factory=Worker,
                                supported_kinds=('seo.analyze', 'research.rank'), clock=lambda: 1000)
        self.step()
        self.assertEqual(self.client.calls[0][1]['capabilities'], ['seo.analyze', 'research.rank'])

    def test_no_available_handlers_never_claims(self):
        self.lane = ComputeLane(self.root, client=self.client, worker_factory=Worker,
                                supported_kinds=(), clock=lambda: 1000)
        self.assertEqual(self.step()['state'], 'no_supported_compute_kinds')
        self.assertFalse(self.client.calls)

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

    def test_real_worker_ranks_and_reads_back_knowledge_without_php(self):
        class Queue:
            def __init__(self): self.jobs, self.result, self.sequence = [], None, 0
            def enqueue(self, kind, payload):
                self.sequence += 1
                raw = json.dumps(payload, separators=(',', ':'), ensure_ascii=False)
                self.jobs.append({'id': 'real-job-' + str(self.sequence),
                                  'kind': kind, 'payload': raw,
                                  'payload_sha256': hashlib.sha256(raw.encode()).hexdigest()})
            def send(self, action, fields):
                if action == 'claim':
                    return {'state': 'claimed', 'job': self.jobs.pop(0), 'lease_token': 'lease'}
                if action == 'complete':
                    self.result = json.loads(fields['result_json'])
                    self.digest = fields['result_sha256']
                    return {'stored': True, 'result_sha256': self.digest}
                if action == 'status':
                    return {'state': 'completed', 'result_sha256': self.digest}
                raise AssertionError(action)
        queue = Queue()
        self.lane = ComputeLane(self.root, client=queue, worker_factory=RealWorker,
                                supported_kinds=COMPUTE_KINDS, clock=lambda: 1000)
        queue.enqueue('research.rank', {'ideas': [{'id': 'idea-1', 'title': 'test', 'demand': 80,
            'competition': 20, 'margin': 70, 'delivery_effort': 30, 'sources': ['https://example.com/a']}]})
        self.assertTrue(self.step()['server_ack_verified'])
        self.assertEqual(queue.result['data']['ideas'][0]['state'], 'scored_from_supplied_evidence')
        queue.enqueue('knowledge.ingest', {'records': [{'id': 'test:source', 'title': 'Pompa',
            'text': 'Pompa wymaga diagnostyki', 'approved': True}]})
        self.assertTrue(self.step()['server_ack_verified'])
        self.assertEqual(queue.result['data']['readback_verified_records'], 1)
        queue.enqueue('knowledge.search', {'query': 'pompa'})
        self.assertTrue(self.step()['server_ack_verified'])
        self.assertEqual(queue.result['data']['items'][0]['id'], 'test:source')
        self.assertFalse(queue.result['data']['training_performed'])


if __name__ == '__main__': unittest.main()
