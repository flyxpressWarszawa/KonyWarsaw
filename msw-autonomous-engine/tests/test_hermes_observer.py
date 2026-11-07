import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from msw_engine.doctor_sidecar import publish_evidence, safe_observation
from msw_engine.hermes_observer import inspect_hermes
from msw_engine.tasks import knowledge_search


class HermesObserverTests(unittest.TestCase):
    def test_realistic_gateway_and_cron_are_observed_without_private_payloads(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / 'home'
            cron = home / 'cron'
            cron.mkdir(parents=True)
            release = root / 'releases' / 'v0.23.0-test'
            release.mkdir(parents=True)
            proc = root / 'proc' / '123'
            proc.mkdir(parents=True)
            (proc / 'comm').write_text('hermes\n')
            (proc / 'cmdline').write_bytes(b'python\0-m\0hermes_cli.main\0gateway\0run\0')
            (proc / 'cwd').symlink_to(release, target_is_directory=True)
            heartbeat = cron / 'ticker_heartbeat'
            heartbeat.write_text('heartbeat')
            os.utime(heartbeat, (1000, 1000))
            (cron / 'jobs.json').write_text(json.dumps({'jobs': [
                {'id': 'job1', 'name': 'MSW business job contact@example.com', 'enabled': True,
                 'state': 'scheduled', 'next_run_at': '2026-11-07T18:00:00Z',
                 'prompt': 'DO_NOT_EXPORT_PRIVATE_PROMPT'},
                {'id': 'job2', 'name': 'finished', 'enabled': False, 'state': 'completed'}]}))
            with sqlite3.connect(cron / 'executions.db') as db:
                db.execute('CREATE TABLE executions(job_id TEXT,status TEXT,started_at TEXT,finished_at TEXT,error TEXT)')
                db.execute('CREATE TABLE cron_incidents(failure_type TEXT,state TEXT,error TEXT)')
                db.execute('INSERT INTO executions VALUES(?,?,?,?,?)',
                           ('job1', 'completed', '2026-11-07T17:00:00Z', '2026-11-07T17:01:00Z', 'DO_NOT_EXPORT_ERROR'))
                db.execute('INSERT INTO cron_incidents VALUES(?,?,?)', ('timeout', 'open', 'DO_NOT_EXPORT_ERROR'))
            out = inspect_hermes(home, proc_root=root / 'proc', now=1050)
            self.assertEqual(out['gateway'], {'state': 'running', 'pid': 123})
            self.assertTrue(out['scheduler_heartbeat']['fresh'])
            self.assertEqual(out['jobs']['enabled'], 1)
            self.assertEqual(out['executions']['recent'][0]['status'], 'completed')
            self.assertEqual(out['executions']['incidents'][0]['type'], 'timeout')
            self.assertFalse(out['business_result_verified'])
            text = json.dumps(out)
            self.assertNotIn('DO_NOT_EXPORT', text)
            self.assertNotIn('contact@example.com', text)
            self.assertIn('[redacted]', text)

    def test_missing_gateway_stale_heartbeat_and_no_database_are_not_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / 'home'
            (home / 'cron').mkdir(parents=True)
            (root / 'proc').mkdir()
            heartbeat = home / 'cron' / 'ticker_heartbeat'
            heartbeat.touch()
            os.utime(heartbeat, (100, 100))
            out = inspect_hermes(home, proc_root=root / 'proc', now=1000)
            self.assertEqual(out['gateway']['state'], 'not_observed')
            self.assertFalse(out['scheduler_heartbeat']['fresh'])
            self.assertEqual(out['jobs']['state'], 'not_found')
            self.assertEqual(out['executions']['state'], 'not_found')
            self.assertFalse((home / 'cron' / 'executions.db').exists())

    def test_symlinked_cron_is_not_followed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / 'home'
            home.mkdir()
            other = root / 'other'
            other.mkdir()
            (other / 'jobs.json').write_text('{"jobs":[{"name":"PRIVATE"}]}')
            (home / 'cron').symlink_to(other, target_is_directory=True)
            out = inspect_hermes(home, proc_root=root / 'missing-proc', now=1000)
            self.assertEqual(out['jobs']['state'], 'unavailable')
            self.assertEqual(out['executions']['state'], 'unavailable')
            self.assertNotIn('PRIVATE', str(out))

    def test_sidecar_publishes_hermes_metadata_to_shared_memory(self):
        hermes = {'schema': 'msw.hermes-observation.v1', 'state': 'observed',
                  'gateway': {'state': 'running'}, 'business_result_verified': False}
        with tempfile.TemporaryDirectory() as tmp, \
             patch('msw_engine.hermes_observer.inspect_hermes', return_value=hermes):
            directory = Path(tmp)
            observation = safe_observation(directory)
            self.assertEqual(observation['hermes']['gateway']['state'], 'running')
            self.assertTrue(publish_evidence(directory, observation))
            hits = knowledge_search({'query': 'hermes'}, directory)['items']
            self.assertEqual(hits[0]['id'], 'doctor:render:sidecar:latest')


if __name__ == '__main__':
    unittest.main()
