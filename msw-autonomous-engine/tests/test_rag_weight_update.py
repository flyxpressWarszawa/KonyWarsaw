"""No real provider, secrets or private checkpoints: exercise the existing weight-update handler."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from msw_engine import own_model
from msw_engine.tasks import knowledge_ingest


class FakeModel:
    def __init__(self, weight=0.0, step=3):
        self.p = {'w': np.array([weight], dtype=np.float64)}
        self.step = step
        self.parameter_count = 1

    @classmethod
    def load(cls, path):
        with np.load(path, allow_pickle=False) as data:
            return cls(float(data['w'][0]), int(data['step'][0])), {}

    def save(self, path, metadata=None):
        with open(path, 'wb') as handle:
            np.savez(handle, w=self.p['w'], step=np.array([self.step]))

    def loss(self, x, y):
        return (float(self.p['w'][0]) - float(y.mean()) / 256) ** 2

    def train_batch(self, x, y):
        target = float(y.mean()) / 256
        self.p['w'][0] += 0.05 * (target - float(self.p['w'][0]))
        self.step += 1


class FakeCode:
    MSWDualMemory = FakeModel

    @staticmethod
    def encode(text):
        return np.frombuffer(text.encode('utf-8'), dtype=np.uint8).astype(np.float64)


class DualMemoryApprovedWeightTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name)
        self.root = self.data / 'private-model'
        (self.root / 'checkpoint').mkdir(parents=True)
        base = self.data / 'initial.npz'
        FakeModel().save(base)
        self.digest = hashlib.sha256(base.read_bytes()).hexdigest()
        base_dir = self.data / 'own-model' / 'checkpoints'
        base_dir.mkdir(parents=True)
        (base_dir / (self.digest + '.npz')).write_bytes(base.read_bytes())
        (self.root / 'checkpoint' / 'checkpoint-manifest.json').write_text(json.dumps({'sha256': self.digest}))
        self.mock = patch.object(own_model, 'module', return_value=(self.root, FakeCode))
        self.mock.start()
        self.addCleanup(self.mock.stop)
        self.ids = {'train_ids': ['msw:training'], 'validation_ids': ['msw:holdout']}
        rows = [
            {'id': 'msw:training', 'title': 'MSW materiały techniczne',
             'text': 'Kontrola instalacji zgodnie z dokumentacją techniczną i bezpieczną procedurą. ' * 10,
             'approved': True, 'provenance': 'msw-training-approved-public:v1:reviewed'},
            {'id': 'msw:holdout', 'title': 'MSW walidacja techniczna',
             'text': 'Diagnoza źródłowa, pomiary i ostrożne zalecenia techniczne. ' * 8,
             'approved': True, 'provenance': 'msw-training-approved-public:v1:reviewed'},
        ]
        self.assertEqual(knowledge_ingest({'records': rows}, self.data)['readback_verified_records'], 2)

    def task(self, action, request_id, **more):
        return {'schema': 'msw.own-model.job.v1', 'owner_authorized': True, 'action': action,
                'request_id': request_id, **more}

    def test_actual_numeric_weight_update_with_tensor_readback_and_no_provider(self):
        view = own_model.handle(self.task('training_data_status', 'msw-view-100', **self.ids), self.data)
        self.assertEqual(view['state'], 'approved_snapshot_ready')
        self.assertFalse(view['weights_modified'])
        job = self.task('train_approved', 'msw-train-100',
                        parent_sha256=self.digest, dataset_sha256=view['dataset_sha256'],
                        steps=2, **self.ids)
        grant = {'MSW_OWN_MODEL_TRAIN_APPROVED': '1',
                 'MSW_OWN_MODEL_TRAIN_DATASET_SHA256': view['dataset_sha256'],
                 'MSW_OWN_MODEL_TRAIN_PARENT_SHA256': self.digest}
        with patch.dict('os.environ', grant):
            first = own_model.handle(job, self.data)
            self.assertEqual(first, own_model.handle(job, self.data))
        self.assertEqual(first['training_steps'], 5)
        self.assertTrue(first['training_performed'])
        self.assertTrue(first['weights_modified'])
        self.assertTrue(first['checkpoint_readback_verified'])
        self.assertEqual(first['training_corpus'], 'approved_rag')
        self.assertFalse(first['business_quality_improvement_proven'])
        self.assertTrue((self.data / 'own-model' / 'checkpoints' /
                         (first['checkpoint_sha256'] + '.npz')).exists())
        self.assertLessEqual(first['validation_byte_nll_after'], first['validation_byte_nll_before'])
        self.assertEqual(first['training_records'], 1)
        self.assertEqual(first['validation_records'], 1)

    def test_wrong_snapshot_fails_before_any_gradient_update(self):
        job = self.task('train_approved', 'msw-bad-snapshot-100', parent_sha256=self.digest,
                        dataset_sha256='0'*64, steps=2, **self.ids)
        with patch.dict('os.environ', {'MSW_OWN_MODEL_TRAIN_APPROVED': '1',
                                        'MSW_OWN_MODEL_TRAIN_DATASET_SHA256': '0'*64,
                                        'MSW_OWN_MODEL_TRAIN_PARENT_SHA256': self.digest}):
            with self.assertRaisesRegex(ValueError, 'dataset_digest_mismatch'):
                own_model.handle(job, self.data)
        self.assertFalse(list((self.data / 'own-model' / 'receipts').glob('*.json')))

    def test_training_requires_out_of_band_owner_grant(self):
        view = own_model.handle(self.task('training_data_status', 'msw-read-only-101', **self.ids), self.data)
        job = self.task('train_approved', 'msw-no-training-grant-101',
                        parent_sha256=self.digest, dataset_sha256=view['dataset_sha256'],
                        steps=2, **self.ids)
        with patch.dict('os.environ', {
            'MSW_OWN_MODEL_TRAIN_APPROVED': '0',
            'MSW_OWN_MODEL_TRAIN_DATASET_SHA256': view['dataset_sha256'],
            'MSW_OWN_MODEL_TRAIN_PARENT_SHA256': self.digest
        }):
            with self.assertRaisesRegex(ValueError, 'out_of_band_grant_missing'):
                own_model.handle(job, self.data)
        with patch.dict('os.environ', {
            'MSW_OWN_MODEL_TRAIN_APPROVED': '1',
            'MSW_OWN_MODEL_TRAIN_DATASET_SHA256': view['dataset_sha256'],
            'MSW_OWN_MODEL_TRAIN_PARENT_SHA256': 'f'*64
        }):
            with self.assertRaisesRegex(ValueError, 'out_of_band_grant_missing'):
                own_model.handle(job, self.data)
        self.assertFalse(list((self.data / 'own-model' / 'receipts').glob('*.json')))

    def test_stale_parent_does_not_start_redundant_gradient_job(self):
        view = own_model.handle(self.task('training_data_status', 'msw-view-stale-100', **self.ids), self.data)
        job = self.task('train_approved', 'msw-training-promote-100',
                        parent_sha256=self.digest, dataset_sha256=view['dataset_sha256'],
                        steps=2, **self.ids)
        grant = {'MSW_OWN_MODEL_TRAIN_APPROVED': '1',
                 'MSW_OWN_MODEL_TRAIN_DATASET_SHA256': view['dataset_sha256'],
                 'MSW_OWN_MODEL_TRAIN_PARENT_SHA256': self.digest}
        with patch.dict('os.environ', grant):
            first = own_model.handle(job, self.data)
            self.assertTrue(first['promoted_to_research_current'])
            self.assertNotEqual(first['checkpoint_sha256'], self.digest)
            with self.assertRaisesRegex(ValueError, 'own_model_training_stale_parent'):
                own_model.handle(dict(job, request_id='msw-duplicate-from-baseline-101'), self.data)
            # The historic synthetic hourly action must fail the same way.
            with self.assertRaisesRegex(ValueError, 'own_model_training_stale_parent'):
                own_model.handle(self.task('train', 'msw-old-hourly-parent-102',
                                           parent_sha256=self.digest, steps=2), self.data)
            # An identical previously completed request must still be idempotent.
            self.assertEqual(own_model.handle(job, self.data), first)

    def test_unauthorized_snapshot_request_is_blocked(self):
        job = self.task('training_data_status', 'msw-no-authorize', **self.ids)
        job['owner_authorized'] = False
        with self.assertRaisesRegex(ValueError, 'authorization_required'):
            own_model.handle(job, self.data)


if __name__ == '__main__':
    unittest.main()
