from pathlib import Path
import hashlib
import json
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from msw_engine import continuity, wordpress_agent
from msw_engine.legacy_memory import insert_row


class ContinuityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.sha = 'a' * 64
        self.source = self.root / ('legacy-malta-' + self.sha + '.sqlite3')
        with sqlite3.connect(self.source) as db:
            db.execute('CREATE TABLE documents (id INTEGER PRIMARY KEY,title TEXT,body TEXT,source TEXT,kind TEXT,digest TEXT)')
            db.execute("CREATE VIRTUAL TABLE document_fts USING fts5(id UNINDEXED,title,body)")
            for i in range(1, 4):
                insert_row(db, json.dumps({'id': i, 'title': 'Archiwum MSW ' + str(i), 'content': 'Historyczny projekt techniczny', 'source': 'owner archive', 'kind': 'historical'}).encode())
        (self.root / 'legacy-source-status.json').write_text(json.dumps({'state': 'ready', 'source_sha256': self.sha, 'records': 3}))

    def test_resume_source_cataloguing_and_no_full_history_claim(self):
        first = continuity.learn(self.root, limit=2)
        self.assertEqual(first['catalogued_records'], 2)
        second = continuity.learn(self.root, limit=2)
        self.assertEqual(second['catalogued_records'], 3)
        self.assertEqual(second['processed_this_cycle'], 1)
        self.assertEqual(continuity.learn(self.root)['processed_this_cycle'], 0)
        self.assertFalse(second['full_chatgpt_account_history_access'])
        self.assertFalse(second['semantic_review_of_all_records_performed'])

    def test_tampered_source_does_not_advance_cursor(self):
        with sqlite3.connect(self.source) as db:
            db.execute("UPDATE documents SET body='changed' WHERE id=1")
        with self.assertRaises(ValueError):
            continuity.learn(self.root)
        with continuity.database(self.root) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM cursors').fetchone()[0], 0)

    def test_current_owner_mission_has_durable_readback(self):
        result = continuity.bootstrap(self.root)
        self.assertTrue(result['owner_mission_readback_verified'])
        from msw_engine.tasks import knowledge_search
        self.assertTrue(knowledge_search({'query': 'autonomicznie', 'limit': 2}, self.root)['items'])


class WordPressToolsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.sha = 'b' * 64
        self.rows = [
            {'id': 'active/a.php', 'name': 'active', 'active': True, 'native_probe': 'read.a'},
            {'id': 'inactive/b.php', 'name': 'inactive', 'active': False, 'native_probe': 'read.b'},
            {'id': 'active2/c.php', 'name': 'active2', 'active': True, 'native_probe': None}]
        with sqlite3.connect(self.root / 'module-bus.sqlite3') as db:
            db.execute('CREATE TABLE modules (id TEXT,catalog_sha256 TEXT,observation_json TEXT)')
            db.executemany('INSERT INTO modules VALUES(?,?,?)', [(r['id'], self.sha, json.dumps(r)) for r in self.rows])

    def test_dynamic_catalogue_never_inspects_inactive_plugin(self):
        with patch('msw_engine.federation.gateway', return_value={'catalog_sha256': self.sha, 'total': 3}), \
             patch('msw_engine.fleet_scheduler.fetch_roster', return_value=[]), \
             patch('msw_engine.module_bus.probe', return_value={'modules': [{'id': 'active/a.php', 'native_read_executed': True}]}) as probe:
            result = wordpress_agent.discover(self.root)
        self.assertEqual(result['active_modules'], 2)
        self.assertEqual(probe.call_args.args[0]['ids'], ['active/a.php'])
        self.assertEqual(result['inactive_modules_changed'], 0)
        self.assertEqual(len(wordpress_agent.tool_context(self.root, 'active')), 2)

    def test_native_work_requires_execution_receipt_and_deduplicates(self):
        data = {'schema': 'msw.native-autonomy.receipt.v1', 'native_callbacks_executed': True,
            'storage_readback_verified': True, 'modules': ['active/a.php'], 'hook': 'existing_business_hook'}
        receipt = {'job_id': 'one-job', 'result_sha256': 'c' * 64, 'result_json': json.dumps({'ok': True, 'data': data})}
        wordpress_agent.record_native_receipt(self.root, receipt)
        wordpress_agent.record_native_receipt(self.root, receipt)
        result = wordpress_agent.summary(self.root)
        self.assertEqual(result['native_jobs_completed_with_ack'], 1)
        self.assertFalse(result['all_business_effects_verified'])
        receipt['job_id'] = 'unverified-job'
        receipt['result_json'] = json.dumps({'ok': True, 'data': {**data, 'native_callbacks_executed': False}})
        wordpress_agent.record_native_receipt(self.root, receipt)
        self.assertEqual(wordpress_agent.summary(self.root)['native_jobs_completed_with_ack'], 1)

    def test_all_satellites_come_from_live_combo_and_need_signed_execution_proof(self):
        site = '733bcd1b-a160-419b-8b2c-531811c2b6a0'
        with patch('msw_engine.federation.gateway', return_value={'catalog_sha256': self.sha, 'total': 3}), \
             patch('msw_engine.module_bus.probe', return_value={'modules': []}), \
             patch('msw_engine.fleet_scheduler.fetch_roster', return_value=[{'site_id': site, 'url': 'https://e-hydraulikwarszawa.pl'}]):
            result = wordpress_agent.discover(self.root)
        self.assertEqual(result['satellites_from_live_combo'], 1)
        self.assertEqual(result['satellites_with_native_execution_receipt'], 0)
        data = {'schema': 'msw.native-autonomy.receipt.v1', 'native_callbacks_executed': True,
            'storage_readback_verified': True, 'modules': ['active/a.php'], 'hook': 'native_work', 'site_id': site}
        receipt = {'job_id': 'sat-job', 'result_sha256': 'd' * 64, 'result_json': json.dumps({'ok': True, 'data': data})}
        wordpress_agent.record_native_receipt(self.root, receipt)
        self.assertEqual(wordpress_agent.summary(self.root)['satellites_with_native_execution_receipt'], 0)
        data['satellite_hmac_readback_verified'] = True
        receipt['result_json'] = json.dumps({'ok': True, 'data': data})
        wordpress_agent.record_native_receipt(self.root, receipt)
        self.assertEqual(wordpress_agent.summary(self.root)['satellites_with_native_execution_receipt'], 1)


if __name__ == '__main__':
    unittest.main()
