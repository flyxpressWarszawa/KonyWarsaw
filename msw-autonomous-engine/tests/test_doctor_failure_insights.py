import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from msw_engine.doctor_failure_insights import FailureInsights, summarize
from msw_engine.doctor_sidecar import publish_evidence, safe_observation
from msw_engine.tasks import knowledge_search


class FailureInsightsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = self.root / 'receipts.sqlite3'
        with sqlite3.connect(self.db) as db:
            db.execute('CREATE TABLE receipts(state TEXT,updated_at INTEGER,result_json TEXT)')

    def tearDown(self):
        self.tmp.cleanup()

    def add(self, state, at, result):
        with sqlite3.connect(self.db) as db:
            db.execute('INSERT INTO receipts VALUES(?,?,?)', (state, at, json.dumps(result)))

    def test_categorizes_recent_acknowledgements_without_export_or_retry(self):
        self.add('acknowledged', 9999, {'ok': False, 'error': 'fed_autonomy_execute_http_409:msw_autonomy_source_changed',
                                       'secret': 'DO_NOT_EXPORT'})
        self.add('acknowledged', 9998, {'ok': False, 'error': 'fed_autonomy_execute_http_423:mswpc_fleet33_quarantine'})
        self.add('acknowledged', 9997, {'ok': False, 'error': 'fed_autonomy_execute_http_409:msw_autonomy_execution_not_registered'})
        self.add('acknowledged', 9996, {'ok': True, 'data': {'text': 'PRIVATE'}})
        self.add('pending', 9995, {'ok': False, 'error': 'fed_generate_http_502'})
        self.add('acknowledged', 1000, {'ok': False, 'error': 'fed_generate_http_502'})
        before = self.db.read_bytes()
        out = summarize(self.root, now=10000)
        self.assertEqual(out['receipts_sampled'], 4)
        self.assertEqual(out['failed_in_sample'], 3)
        self.assertEqual(out['categories'], {'execution_unregistered': 1, 'satellite_quarantined': 1, 'source_changed': 1})
        self.assertEqual(out['time_basis'], 'receipt_updated_at_not_execution')
        self.assertFalse(out['automatic_retry_performed'])
        self.assertFalse(out['repair_performed'])
        self.assertNotIn('DO_NOT_EXPORT', json.dumps(out))
        self.assertNotIn('PRIVATE', json.dumps(out))
        self.assertEqual(before, self.db.read_bytes())

    def test_no_missing_or_symlinked_database_is_created_or_followed(self):
        self.db.unlink()
        self.assertEqual(summarize(self.root, now=10000)['state'], 'not_found')
        self.assertFalse(self.db.exists())
        target = self.root / 'target'
        target.write_text('not a database')
        self.db.symlink_to(target)
        self.assertEqual(summarize(self.root, now=10000)['reason'], 'unsafe_path')

    def test_sampling_is_rate_limited_and_shared_as_observation(self):
        times = iter([1, 2, 302, 303])
        insight = FailureInsights(self.root, clock=lambda: 10000, monotonic=lambda: next(times))
        self.add('acknowledged', 9999, {'ok': False, 'error': 'native_autonomy_uncertain_no_repeat'})
        first = insight.snapshot()
        self.add('acknowledged', 9998, {'ok': False, 'error': 'fed_generate_http_502'})
        self.assertEqual(insight.snapshot(), first)
        self.assertEqual(insight.snapshot()['failed_in_sample'], 2)
        with patch('msw_engine.hermes_observer.inspect_hermes', return_value={'state': 'not_available'}):
            observation = safe_observation(self.root, failure_insights=insight)
        self.assertEqual(observation['acknowledged_failure_receipts']['failed_in_sample'], 2)
        self.assertTrue(publish_evidence(self.root, observation))
        self.assertEqual(knowledge_search({'query': 'outcome_uncertain'}, self.root)['items'][0]['id'],
                         'doctor:render:sidecar:latest')


if __name__ == '__main__':
    unittest.main()
