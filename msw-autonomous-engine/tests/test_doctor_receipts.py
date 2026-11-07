import hashlib
from pathlib import Path
import tempfile
import unittest

from msw_engine.client import TransportError
from msw_engine.doctor_receipts import ReceiptDoctor


class Client:
    def __init__(self, digest):
        self.digest, self.global_failure, self.recovered = digest, False, False
    def send(self, action, payload):
        if action == 'status':
            if self.global_failure or (payload['job_id'] == 'missing' and not self.recovered):
                raise TransportError('server_rejected_request', 404)
            return {'state': 'completed', 'result_sha256': self.digest}
        if action == 'complete': raise TransportError('server_rejected_request', 409)
        raise AssertionError(action)


class ReceiptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.raw = '{"ok":true}'
        self.digest = hashlib.sha256(self.raw.encode()).hexdigest()
        self.client = Client(self.digest)
        self.doctor = ReceiptDoctor(Path(self.tmp.name), client=self.client, clock=lambda: 10000)
        with self.doctor.worker.journal.connect() as db:
            rows = [('missing','a'*64,'private-lease',self.raw,self.digest,'pending',1),
                    ('control','b'*64,'other-lease',self.raw,self.digest,'acknowledged',2)]
            db.executemany('INSERT INTO receipts VALUES(?,?,?,?,?,?,?)', rows)

    def row(self):
        with self.doctor.worker.journal.connect() as db:
            return dict(db.execute('SELECT * FROM receipts WHERE job_id=?', ('missing',)).fetchone())

    def test_missing_receipt_is_deferred_without_losing_result_or_claiming_ack(self):
        before = self.row()
        result = self.doctor.defer_missing_oldest()
        self.assertEqual(result['state'], 'deferred_verified')
        self.assertTrue(result['receipt_preserved'])
        self.assertFalse(result['server_ack_verified'])
        after = self.row()
        self.assertEqual(after['state'], 'pending_deferred')
        for field in ('job_id','payload_sha256','lease_token','result_json','result_sha256'):
            self.assertEqual(before[field], after[field])

    def test_global_404_does_not_change_receipt(self):
        self.client.global_failure = True
        self.assertEqual(self.doctor.defer_missing_oldest()['state'], 'status_control_unverified')
        self.assertEqual(self.row()['state'], 'pending')

    def test_corrupted_local_result_is_not_modified(self):
        with self.doctor.worker.journal.connect() as db:
            db.execute('UPDATE receipts SET result_json=? WHERE job_id=?', ('corrupt', 'missing'))
        self.assertEqual(self.doctor.defer_missing_oldest()['state'], 'local_digest_mismatch')
        self.assertEqual(self.row()['state'], 'pending')

    def test_deferred_entry_is_retried_after_hour_and_matching_ack_is_verified(self):
        self.doctor.defer_missing_oldest()
        self.assertEqual(self.doctor.retry_deferred(), [])
        with self.doctor.worker.journal.connect() as db:
            db.execute('UPDATE receipts SET updated_at=1 WHERE job_id=?', ('missing',))
        self.client.recovered = True
        result = self.doctor.retry_deferred()
        self.assertEqual(result, [{'state': 'acknowledged', 'server_ack_verified': True}])
        self.assertEqual(self.row()['state'], 'acknowledged')

    def test_recovered_mismatching_digest_is_never_marked_acknowledged(self):
        self.doctor.defer_missing_oldest()
        with self.doctor.worker.journal.connect() as db:
            db.execute('UPDATE receipts SET updated_at=1 WHERE job_id=?', ('missing',))
        self.client.recovered = True
        self.client.digest = 'c'*64
        self.assertEqual(self.doctor.retry_deferred(), [{'state': 'retry_later', 'server_ack_verified': False}])
        self.assertEqual(self.row()['state'], 'pending_deferred')


if __name__ == '__main__': unittest.main()
