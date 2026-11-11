import base64
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from msw_engine import own_model


class OwnModelIsolationTests(unittest.TestCase):
    def test_no_pack_keeps_existing_engines_configuration(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {'MSW_RUNTIME_PACK_ROOT': 'existing-private-engines'}, clear=True):
            self.assertEqual(own_model.install(Path(d), {})['state'], 'not_configured')
            self.assertEqual(os.environ['MSW_RUNTIME_PACK_ROOT'], 'existing-private-engines')
            self.assertEqual(own_model.available_handlers(), {})

    def test_invalid_pack_is_disabled_without_breaking_worker(self):
        with tempfile.TemporaryDirectory() as d:
            for env in ({'MSW_OWN_MODEL_PACK_CHUNKS': 'bad'}, {'MSW_OWN_MODEL_PACK_CHUNKS': '1', 'MSW_OWN_MODEL_PACK_00': 'not base64'}):
                self.assertEqual(own_model.install(Path(d), env)['state'], 'blocked')


@unittest.skipUnless(os.environ.get('MSW_TEST_OWN_MODEL_PACK') and os.environ.get('MSW_TEST_OWN_CHECKPOINT'), 'reviewed private owner model and checkpoint required')
class OwnModelPrivateIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.patch = patch.dict(os.environ, {}, clear=False);self.patch.start();self.addCleanup(self.patch.stop)
        raw = Path(os.environ['MSW_TEST_OWN_MODEL_PACK']).read_bytes();encoded = base64.b64encode(raw).decode()
        chunks = [encoded[i:i+60000] for i in range(0, len(encoded), 60000)]
        env = {'MSW_OWN_MODEL_PACK_CHUNKS': str(len(chunks)), 'MSW_OWN_MODEL_PACK_SHA256': hashlib.sha256(raw).hexdigest()}
        env.update({f'MSW_OWN_MODEL_PACK_{i:02d}': p for i,p in enumerate(chunks)})
        self.assertEqual(own_model.install(self.directory, env)['state'], 'installed_verified')
        self.weights = Path(os.environ['MSW_TEST_OWN_CHECKPOINT']).read_bytes()
        parts = [self.weights[i:i+150000] for i in range(0, len(self.weights), 150000)]
        for i, part in enumerate(parts):
            ack = own_model.handle(self.payload('checkpoint_part', f'test-checkpoint-part-{i}', part=i, data_b64=base64.b64encode(part).decode()), self.directory)
        self.assertTrue(ack['checkpoint_ready'])

    def payload(self, action, request, **more):
        return dict(schema='msw.own-model.job.v1', owner_authorized=True, action=action, request_id=request, **more)

    def test_own_inference_and_immutable_weight_identity(self):
        status = own_model.handle(self.payload('status', 'test-own-status-01'), self.directory)
        self.assertEqual(status['checkpoint_sha256'], hashlib.sha256(self.weights).hexdigest())
        self.assertEqual((status['parameters'], status['training_steps']), (109763, 3000))
        answer = own_model.handle(self.payload('generate', 'test-own-generate-01', prompt='MSW cel techniczny', max_new_tokens=20), self.directory)
        self.assertIsInstance(answer['text'], str)
        self.assertTrue(answer['research_only']);self.assertFalse(answer['business_decision_authorized'])
        self.assertFalse(answer['external_model_weights_used'])

    def test_training_is_persistent_idempotent_and_parent_bound(self):
        status = own_model.handle(self.payload('status', 'test-own-status-02'), self.directory)
        p = self.payload('train', 'test-own-training-02', parent_sha256=status['checkpoint_sha256'], steps=2)
        first = own_model.handle(p, self.directory);second = own_model.handle(p, self.directory)
        self.assertEqual(first, second)
        self.assertEqual(first['training_steps'], 3002)
        self.assertTrue(first['weights_modified']);self.assertTrue(first['checkpoint_readback_verified'])
        fresh = own_model.handle(self.payload('generate', 'test-new-process-generation', parent_sha256=first['checkpoint_sha256'], prompt='MSW', max_new_tokens=10), self.directory)
        self.assertEqual(fresh['training_steps'], 3002)
        with self.assertRaisesRegex(ValueError, 'conflict'):
            own_model.handle(dict(p, steps=3), self.directory)

    def test_explicit_approved_rag_training_changes_research_checkpoint(self):
        from msw_engine.tasks import knowledge_ingest
        source = 'Własna zatwierdzona procedura MSW. Diagnozujemy instalację według dokumentacji. ' * 10
        holdout = 'Dokumentujemy pomiary, weryfikujemy wyniki i dopiero potem zalecamy naprawę. ' * 6
        records = [
            {'id': 'msw:training', 'title': 'MSW trening techniczny', 'text': source,
             'approved': True, 'provenance': 'msw-training-approved-public:v1:operator-reviewed'},
            {'id': 'msw:holdout', 'title': 'MSW walidacja techniczna', 'text': holdout,
             'approved': True, 'provenance': 'msw-training-approved-public:v1:operator-reviewed'},
        ]
        self.assertEqual(knowledge_ingest({'records': records}, self.directory)['readback_verified_records'], 2)
        ids = {'train_ids': ['msw:training'], 'validation_ids': ['msw:holdout']}
        data = own_model.handle(self.payload('training_data_status', 'test-rag-training-status-01', **ids), self.directory)
        self.assertEqual(data['state'], 'approved_snapshot_ready')
        state = own_model.handle(self.payload('status', 'test-rag-training-parent-01'), self.directory)
        task = self.payload('train_approved', 'test-rag-training-run-01', parent_sha256=state['checkpoint_sha256'],
                            dataset_sha256=data['dataset_sha256'], steps=2, **ids)
        grant = {'MSW_OWN_MODEL_TRAIN_APPROVED': '1',
                 'MSW_OWN_MODEL_TRAIN_DATASET_SHA256': data['dataset_sha256'],
                 'MSW_OWN_MODEL_TRAIN_PARENT_SHA256': state['checkpoint_sha256']}
        with patch.dict(os.environ, grant):
            first = own_model.handle(task, self.directory)
            self.assertEqual(first, own_model.handle(task, self.directory))
        self.assertTrue(first['training_performed'])
        self.assertTrue(first['weights_modified'])
        self.assertTrue(first['checkpoint_readback_verified'])
        self.assertEqual(first['training_corpus'], 'approved_rag')
        self.assertFalse(first['business_quality_improvement_proven'])
        with patch.dict(os.environ, grant):
            with self.assertRaisesRegex(ValueError, 'training_dataset_digest_mismatch'):
                own_model.handle(dict(task, request_id='test-rag-bad-snapshot-01',
                                      dataset_sha256='0' * 64), self.directory)

    def test_checkpoint_injection_and_arbitrary_code_are_rejected(self):
        with self.assertRaises(ValueError):own_model.handle(self.payload('checkpoint_part', 'test-invalid-checkpoint', part=0, data_b64=base64.b64encode(b'wrong').decode()), self.directory)
        with self.assertRaises(ValueError):own_model.handle(self.payload('status', 'test-invalid-code', code='print(1)'), self.directory)
        with self.assertRaises(ValueError):own_model.handle(dict(self.payload('status', 'test-no-permission'), owner_authorized=False), self.directory)
