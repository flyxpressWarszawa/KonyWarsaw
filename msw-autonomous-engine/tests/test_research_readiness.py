import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from msw_engine.own_model import research_status


class ResearchEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.addCleanup(self.t.cleanup)
        self.data = Path(self.t.name)
        self.private = self.data / 'private'
        (self.private / 'checkpoint').mkdir(parents=True)
        self.patch = patch.dict('os.environ', {'MSW_OWN_MODEL_ROOT': str(self.private)})
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.baseline = b'fake-pinned-checkpoint-without-model-execution'
        self.digest = hashlib.sha256(self.baseline).hexdigest()
        (self.private / 'checkpoint' / 'checkpoint-manifest.json').write_text(
            json.dumps({'sha256': self.digest, 'training_steps': 3000}))
        self.cp = self.data / 'own-model' / 'checkpoints'
        self.cp.mkdir(parents=True)

    def test_manifest_without_checkpoint_reports_waiting(self):
        status = research_status(self.data)
        self.assertEqual(status['state'], 'waiting_for_pinned_checkpoint')
        self.assertFalse(status['model_weight_training_verified'])

    def test_verified_baseline_does_not_claim_training(self):
        (self.cp / (self.digest + '.npz')).write_bytes(self.baseline)
        status = research_status(self.data)
        self.assertEqual(status['state'], 'verified_research_checkpoint')
        self.assertEqual(status['checkpoint_sha256'], self.digest)
        self.assertTrue(status['checkpoint_readback_verified'])
        self.assertEqual(status['training_receipts_verified'], 0)
        self.assertFalse(status['model_weight_training_verified'])

    def test_corrupted_checkpoint_fails_closed(self):
        (self.cp / (self.digest + '.npz')).write_bytes(b'not-the-same-bytes')
        self.assertEqual(research_status(self.data)['state'], 'checkpoint_integrity_unverified')

    def test_valid_active_checkpoint_still_not_assumed_trained(self):
        (self.cp / (self.digest + '.npz')).write_bytes(self.baseline)
        active = b'fake-adaptive-weights'
        active_digest = hashlib.sha256(active).hexdigest()
        (self.cp / (active_digest + '.npz')).write_bytes(active)
        (self.data / 'own-model' / 'active.json').write_text(
            json.dumps({'sha256': active_digest, 'training_steps': 3002}))
        status = research_status(self.data)
        self.assertEqual(status['checkpoint_sha256'], active_digest)
        self.assertEqual(status['training_steps'], 3002)
        self.assertFalse(status['model_weight_training_verified'])

    def test_receipt_must_be_pinned_and_valid_shape(self):
        (self.cp / (self.digest + '.npz')).write_bytes(self.baseline)
        trained = b'new-trained-tensor-synthetic'
        trained_digest = hashlib.sha256(trained).hexdigest()
        (self.cp / (trained_digest + '.npz')).write_bytes(trained)
        receipts = self.data / 'own-model' / 'receipts'
        receipts.mkdir()
        result = dict(checkpoint_sha256=trained_digest, provider_ref='MSW-DualMemory-byte-v1',
                      action='train_approved', training_performed=True,
                      weights_modified=True, checkpoint_readback_verified=True)
        (receipts / 'valid.json').write_text(json.dumps({'payload_sha256': 'a'*64, 'result': result}))
        (receipts / 'invalid.json').write_text(json.dumps({'payload_sha256': 'wrong', 'result': result}))
        status = research_status(self.data)
        self.assertEqual(status['training_receipts_verified'], 1)
        self.assertTrue(status['model_weight_training_verified'])

    def test_symlink_forbidden(self):
        (self.cp / (self.digest + '.npz')).symlink_to(self.private / 'checkpoint' / 'checkpoint-manifest.json')
        self.assertEqual(research_status(self.data)['state'], 'waiting_for_pinned_checkpoint'
                         if not (self.cp / (self.digest + '.npz')).is_file()
                         else 'checkpoint_integrity_unverified')


if __name__ == '__main__':
    unittest.main()
