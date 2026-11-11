from contextlib import closing
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from msw_engine.client import Client, TransportError, TransportNotSent
from msw_engine.recovery import RecoveryCircuit
from msw_engine.runner import Worker, WorkerDraining


class UnsentCompletionTests(unittest.TestCase):
    def test_real_local_circuit_blocks_network_and_preserves_first_send(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            client = Client('https://example.com', 'https://example.com/transport.php', 'test', 'x' * 40)
            client.opener = Mock()
            circuit = RecoveryCircuit(root)
            circuit.enter('https://example.com', 45)
            with closing(circuit.connect()) as db, db:
                db.execute('update origins set open_until=?', (time.time() + 60,))
            worker = Worker(client, root)
            receipt = worker.journal.save({'id': 'job-test', 'payload_sha256': 'a'*64}, 'lease', {'ok': True})
            with patch.dict(os.environ, {'MSW_DATA_DIR': raw}):
                with self.assertRaises(TransportNotSent):
                    worker.acknowledge(receipt)
            client.opener.open.assert_not_called()
            self.assertEqual(worker.journal.lookup('job-test', 'a'*64)['state'], 'ready_to_complete')

    def test_unknown_transport_failure_never_reopens_completion(self):
        for error in (TransportError('transport_unavailable'), TransportError('server_rejected_request', 503)):
            with self.subTest(code=error.code), tempfile.TemporaryDirectory() as raw:
                client = Mock()
                client.send.side_effect = error
                worker = Worker(client, Path(raw))
                receipt = worker.journal.save({'id': 'job-test', 'payload_sha256': 'a'*64}, 'lease', {'ok': True})
                with self.assertRaises(TransportError):worker.acknowledge(receipt)
                self.assertEqual(worker.journal.lookup('job-test', 'a'*64)['state'], 'completion_attempted')

    def test_stop_after_reservation_preserves_unsent_result(self):
        with tempfile.TemporaryDirectory() as raw:
            client = Mock()
            worker = Worker(client, Path(raw))
            receipt = worker.journal.save({'id': 'job-test', 'payload_sha256': 'a'*64}, 'lease', {'ok': True})
            check = iter([False, True])
            worker.cancel_check = lambda: next(check)
            with self.assertRaises(WorkerDraining):worker.acknowledge(receipt)
            client.send.assert_not_called()
            self.assertEqual(worker.journal.lookup('job-test', 'a'*64)['state'], 'ready_to_complete')

    def test_unsent_release_never_downgrades_a_verified_ack(self):
        with tempfile.TemporaryDirectory() as raw:
            worker = Worker(Mock(), Path(raw))
            receipt = worker.journal.save({'id': 'job-test', 'payload_sha256': 'a'*64}, 'lease', {'ok': True})
            worker.journal.reserve_completion(receipt)
            worker.journal.mark(receipt, 'acknowledged')
            worker.journal.release_unsent(receipt)
            self.assertEqual(worker.journal.lookup('job-test', 'a'*64)['state'], 'acknowledged')
