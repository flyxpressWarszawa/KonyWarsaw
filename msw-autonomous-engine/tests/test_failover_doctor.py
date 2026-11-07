import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import URLError

from msw_engine.failover_doctor import BridgeGrant, FailoverDoctor, MCP, TOKEN, status
from msw_engine.runner import Worker
from msw_engine.tasks import TaskError


class Clock:
    def __init__(self): self.value = 1000
    def __call__(self): return self.value


class FailoverTests(unittest.TestCase):
    def test_origin_outage_independent_probe_recovery_and_bounded_bridge(self):
        clock = Clock()
        rows = iter([{'state': 'http', 'status': 503}, {'state': 'timeout'}, {'state': 'http', 'status': 500},
                     {'state': 'timeout'}, {'state': 'timeout'}, {'state': 'http', 'status': 200}, {'state': 'http', 'status': 200}])
        calls = []
        class Bridge:
            def doctor_tick(self): calls.append('tick'); return {'state': 'bridge_doctor_tick_acknowledged', 'repair_verified': False}
        with tempfile.TemporaryDirectory() as root:
            doctor = FailoverDoctor(root, probe=lambda *a, **k: next(rows), bridge=Bridge(), clock=clock)
            self.assertFalse(doctor.step()['outage_confirmed']); clock.value += 60
            self.assertFalse(doctor.step()['outage_confirmed']); clock.value += 60
            self.assertTrue(doctor.step()['outage_confirmed']); self.assertEqual(calls, ['tick']); clock.value += 60
            doctor.step(); self.assertEqual(calls, ['tick']); clock.value += 120
            doctor.step(); self.assertEqual(calls, ['tick', 'tick']); clock.value += 60
            self.assertTrue(doctor.step()['outage_confirmed']); clock.value += 60
            result = doctor.step(); self.assertFalse(result['outage_confirmed'])
            self.assertFalse(result['website_replica_available']); self.assertFalse(result['unconditional_uptime_guaranteed'])
            saved = status(root); self.assertEqual(saved['remembered_cases'], 4)
            self.assertIsNone(doctor.load('open_incident'))

    def test_main_worker_pauses_network_claim_during_verified_outage(self):
        class Client:
            def send(self, *args): raise AssertionError('no request during outage')
        class Failover:
            def in_outage(self): return True
        with tempfile.TemporaryDirectory() as root:
            worker = Worker(Client(), Path(root)); worker.failover_doctor = Failover()
            self.assertEqual(worker.run_once()['state'], 'origin_unavailable')

    def test_stale_probe_does_not_pause_forever(self):
        clock = Clock()
        class Bridge:
            def doctor_tick(self): return {'state': 'unavailable', 'repair_verified': False}
        with tempfile.TemporaryDirectory() as root:
            doctor = FailoverDoctor(root, probe=lambda *a, **k: {'state': 'connection'}, bridge=Bridge(), clock=clock)
            doctor.step(); doctor.step(); doctor.step(); self.assertTrue(doctor.in_outage())
            clock.value += 181; self.assertFalse(doctor.in_outage())

    def test_grant_rotation_and_scoped_doctor_invocation(self):
        clock = Clock(); calls = []
        env = {'MSW_DOCTOR_BRIDGE_ACCESS_TOKEN': 'A'*43, 'MSW_DOCTOR_BRIDGE_REFRESH_TOKEN': 'R'*43,
               'MSW_DOCTOR_BRIDGE_CLIENT_ID': 'client_1', 'MSW_DOCTOR_BRIDGE_EXPIRES_AT': '1010'}
        def send(url, **kwargs):
            calls.append((url, kwargs))
            if url == TOKEN:
                body = {'access_token': 'B'*43, 'refresh_token': 'S'*43, 'expires_in': 3600,
                        'resource': MCP, 'scope': 'hosting.read hosting.write offline_access'}
            else:
                body = {'result': {'isError': False, 'structuredContent': {'cycle': {'state': 'awaiting_ai'}, 'site_recovery_verified': False}}}
            return {'state': 'http', 'status': 200, 'body': json.dumps(body).encode()}
        with tempfile.TemporaryDirectory() as root:
            grant = BridgeGrant(root, env=env, send=send, clock=clock)
            result = grant.doctor_tick()
            self.assertEqual(result['state'], 'bridge_doctor_tick_acknowledged')
            self.assertFalse(result['repair_verified']); self.assertEqual([x[0] for x in calls], [TOKEN, MCP])
            self.assertEqual(grant.load()['refresh_token'], 'S'*43)
            self.assertEqual(os.stat(Path(root)/'doctor-bridge-grant.json').st_mode & 0o777, 0o600)
            payload = json.loads(calls[1][1]['data']); self.assertEqual(payload['params']['name'], 'hosting_doctor_tick')
            self.assertNotIn('A'*43, json.dumps(result)); self.assertNotIn('R'*43, json.dumps(result))

    def test_uncertain_oauth_rotation_is_never_replayed(self):
        clock = Clock(); calls = []
        env = {'MSW_DOCTOR_BRIDGE_ACCESS_TOKEN': 'A'*43, 'MSW_DOCTOR_BRIDGE_REFRESH_TOKEN': 'R'*43,
               'MSW_DOCTOR_BRIDGE_CLIENT_ID': 'client_1', 'MSW_DOCTOR_BRIDGE_EXPIRES_AT': '1010'}
        def fail(*args, **kwargs): calls.append('sent'); return {'state': 'timeout'}
        with tempfile.TemporaryDirectory() as root:
            grant = BridgeGrant(root, env=env, send=fail, clock=clock)
            self.assertIsNone(grant.token()); self.assertIsNone(grant.token())
            self.assertEqual(calls, ['sent']); self.assertEqual(grant.load()['refresh_state'], 'outcome_uncertain')

    def test_paid_advisor_uses_only_sanitized_facts_and_does_not_execute(self):
        clock = Clock(); prompts = []
        class Bridge:
            def doctor_tick(self): return {'state': 'bridge_not_authorized', 'repair_verified': False}
        class SyncThread:
            def __init__(self, target, **kwargs): self.target = target
            def start(self): self.target()
        def generate(values, directory):
            prompts.append(values)
            return {'answer_sha256': 'a'*64, 'model': 'test-model'}
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {'OPENAI_API_KEY': 'K'*40, 'MSW_OPENAI_MODEL': 'test-model'}), \
             patch('msw_engine.openai_direct.generate', generate), patch('msw_engine.failover_doctor.threading.Thread', SyncThread):
            doctor = FailoverDoctor(root, probe=lambda *a, **k: {'state': 'http', 'status': 503}, bridge=Bridge(), clock=clock)
            doctor.step(); doctor.step(); result = doctor.step(); doctor.step()
            self.assertTrue(result['direct_ai_configured']); self.assertEqual(len(prompts), 1)
            self.assertNotIn('K'*40, prompts[0]['text']); self.assertIn('doctor-failover:', prompts[0]['request_id'])
            with doctor.connect() as db:
                row = db.execute("SELECT facts FROM cases WHERE kind='direct_ai_advice'").fetchone()
            self.assertTrue(json.loads(row[0])['advisory_only']); self.assertFalse(json.loads(row[0])['automatic_repairs_executed'])


if __name__ == '__main__': unittest.main()
