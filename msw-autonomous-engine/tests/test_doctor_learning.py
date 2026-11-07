from pathlib import Path
import hashlib
import json
import sqlite3
import tempfile
import unittest

from msw_engine.doctor_learning import diagnose, learn_recent, signals


class DoctorLearningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        with sqlite3.connect(self.root / 'receipts.sqlite3') as db:
            db.execute('CREATE TABLE receipts (job_id TEXT PRIMARY KEY, result_json TEXT, result_sha256 TEXT, state TEXT, updated_at INTEGER)')

    def receipt(self, ident, code, label, *, state='acknowledged', ok=True,
                verified=True, schema='msw.doctor.repair_feedback.v1', corrupt=False):
        before = {'hosting': {'sample_fresh': True, 'http_status': code, 'secret': 'never-store-me'}}
        body = json.dumps({'ok': ok, 'data': {'schema': schema, 'effect_verified': verified,
                          'cause': label, 'evidence_sha256': 'a' * 64,
                          'observation_before': before}}, separators=(',', ':'))
        digest = hashlib.sha256(body.encode()).hexdigest() if not corrupt else 'b' * 64
        with sqlite3.connect(self.root / 'receipts.sqlite3') as db:
            db.execute('INSERT INTO receipts VALUES(?,?,?,?,?)', (ident, body, digest, state, 999))

    def observation(self, code, *, fresh=True):
        return {'hosting': {'sample_fresh': fresh, 'http_status': code}}

    def test_every_client_and_server_status_is_classified_as_a_signal(self):
        for code in range(400, 600):
            with self.subTest(code=code):
                self.assertEqual(len(signals(self.observation(code))), 1)

    def test_stale_or_invalid_metrics_never_produce_flags(self):
        self.assertEqual(signals(self.observation(503, fresh=False)), ())
        self.assertEqual(signals({'render': {'fresh': True, 'cpu_percent': True, 'memory_percent': float('nan')}}), ())
        self.assertEqual(signals({'render': {'fresh': False, 'cpu_percent': 100}}), ())

    def test_cpu_thresholds_and_ambiguous_status_are_evidence_not_attacks(self):
        self.assertEqual(signals({'render': {'fresh': True, 'cpu_percent': 70, 'memory_percent': 90}}),
                         ('render_cpu_70', 'render_memory_70', 'render_memory_90'))
        self.assertEqual(signals(self.observation(429)), ('http_429',))
        self.assertEqual(diagnose(self.root, self.observation(429))['prediction'], None)

    def test_only_verified_acknowledged_repair_feedback_becomes_training_data(self):
        self.receipt('good', 404, 'missing_route')
        self.receipt('failed', 404, 'missing_route', ok=False)
        self.receipt('pending', 404, 'missing_route', state='pending')
        self.receipt('unverified', 404, 'missing_route', verified=False)
        self.receipt('wrong_schema', 404, 'missing_route', schema='ordinary.task.v1')
        self.receipt('tampered', 404, 'missing_route', corrupt=True)
        result = learn_recent(self.root, now=1000)
        self.assertEqual(result['learned'], 1)
        self.assertEqual(result['receipts_sampled'], 5)
        self.assertFalse(result['customer_content_stored'])
        self.assertEqual(learn_recent(self.root, now=1000)['learned'], 0)
        with sqlite3.connect(self.root / 'doctor-learning' / 'incidents.sqlite3') as db:
            rows = db.execute('SELECT * FROM examples').fetchall()
        self.assertEqual(len(rows), 1)
        self.assertNotIn('never-store-me', str(rows))
        self.assertEqual(diagnose(self.root, self.observation(404))['state'], 'insufficient_evidence')

    def test_model_learns_two_labels_and_returns_hypotheses_not_repairs(self):
        for i in range(3):
            self.receipt(f'route-{i}', 404, 'missing_route')
            self.receipt(f'server-{i}', 503, 'server_error')
        self.assertEqual(learn_recent(self.root, now=1000)['learned'], 6)
        result = diagnose(self.root, self.observation(404))
        self.assertEqual(result['state'], 'hypothesis')
        self.assertEqual(result['prediction'], 'missing_route')
        self.assertFalse(result['language_model'])
        self.assertFalse(result['repair_performed'])
        self.assertFalse(result['score_calibrated'])
        self.assertFalse(result['causality_proven'])

    def test_zero_signal_or_one_label_never_fabricates_a_prediction(self):
        for i in range(6):
            self.receipt(f'route-{i}', 404, 'missing_route')
        learn_recent(self.root, now=1000)
        self.assertIsNone(diagnose(self.root, self.observation(404))['prediction'])
        self.assertIsNone(diagnose(self.root, {})['prediction'])

    def test_ordinary_job_success_is_not_training(self):
        self.receipt('regular-job', 503, 'server_error', schema='ordinary.task.v1')
        self.assertEqual(learn_recent(self.root, now=1000)['learned'], 0)
        self.assertEqual(diagnose(self.root, self.observation(503))['state'], 'awaiting_repair_feedback')

    def test_symlink_model_path_refused(self):
        self.receipt('good', 404, 'missing_route')
        (self.root / 'doctor-learning').symlink_to(self.root, target_is_directory=True)
        self.assertEqual(learn_recent(self.root, now=1000)['learned'], 0)
        self.assertFalse((self.root / 'incidents.sqlite3').exists())

    def test_learning_scan_is_bounded_and_absent_journal_is_safe(self):
        with self.assertRaises(ValueError):
            learn_recent(self.root, now=1000, max_receipts=101)
        (self.root / 'receipts.sqlite3').unlink()
        self.assertEqual(learn_recent(self.root, now=1000)['state'], 'journal_unavailable')


if __name__ == '__main__':
    unittest.main()
