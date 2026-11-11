from pathlib import Path
import hashlib
import json
import multiprocessing
import os
import signal
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from msw_engine.runner import Journal, Worker, WorkerDraining


def slow_task(*args):
    time.sleep(120)


class FakeClient:
    def __init__(self):
        self.calls = []

    def send(self, action, payload):
        self.calls.append(action)
        if action != 'claim':
            raise AssertionError('Interrupted work must not be acknowledged')
        body = json.dumps({'content': 'test'})
        return {'state': 'claimed', 'lease_token': 'unit-lease', 'job': {
            'id': 'unit-shutdown', 'kind': 'seo.analyze', 'payload': body,
            'payload_sha256': hashlib.sha256(body.encode()).hexdigest()}}


class ShutdownTest(unittest.TestCase):
    def test_stop_prevents_replay_of_a_saved_result(self):
        client = FakeClient()
        with tempfile.TemporaryDirectory() as directory:
            worker = Worker(client, Path(directory))
            receipt = worker.journal.save({"id": "saved-job", "payload_sha256": "a" * 64}, "lease", {"ok": True})
            worker.stopping = True
            with self.assertRaises(WorkerDraining):
                worker.run_once()
            self.assertEqual(client.calls, [])
            self.assertEqual(worker.journal.pending()[0], receipt)

    def test_cancel_prevents_claim(self):
        client = FakeClient()
        with tempfile.TemporaryDirectory() as directory:
            worker = Worker(client, Path(directory), cancel_check=lambda: True)
            with self.assertRaises(WorkerDraining):
                worker.run_once()
            self.assertEqual(client.calls, [])

    def test_stop_during_claim_prevents_job_start(self):
        client = FakeClient()
        with tempfile.TemporaryDirectory() as directory:
            worker = Worker(client, Path(directory))
            send_claim = client.send

            def claim_then_stop(action, payload):
                response = send_claim(action, payload)
                worker.stopping = True
                return response

            with patch.object(worker.client, 'send', side_effect=claim_then_stop) as send, \
                    patch('msw_engine.runner.multiprocessing.get_context', side_effect=AssertionError('STOP before child setup')) as context:
                with self.assertRaises(WorkerDraining):
                    worker.run_once()
            self.assertEqual(send.call_count, 1)
            context.assert_not_called()
            self.assertEqual(worker.journal.pending(), [])

    def test_cancel_during_process_setup_closes_pipe_without_start(self):
        client = FakeClient()
        response = client.send('claim', {})
        cancelled = threading.Event()
        parent, child, process = Mock(), Mock(), Mock()
        process.start.side_effect = AssertionError('Cancel before starting child')
        with tempfile.TemporaryDirectory() as directory:
            worker = Worker(client, Path(directory), cancel_check=cancelled.is_set)
            with patch('msw_engine.runner.multiprocessing.get_context') as get_context:
                context = get_context.return_value
                context.Pipe.return_value = parent, child

                def prepare_process(**kwargs):
                    cancelled.set()
                    return process

                context.Process.side_effect = prepare_process
                with self.assertRaises(WorkerDraining):
                    worker.execute(response['job'], response['lease_token'])
            process.start.assert_not_called()
            parent.close.assert_called_once()
            child.close.assert_called_once()
            self.assertEqual(worker.journal.pending(), [])

    def test_cancel_before_completion_keeps_result_in_journal(self):
        client = FakeClient()
        with tempfile.TemporaryDirectory() as directory:
            worker = Worker(client, Path(directory), cancel_check=lambda: True)
            receipt = worker.journal.save({"id": "saved-job", "payload_sha256": "a" * 64}, "lease", {"ok": True})
            with self.assertRaises(WorkerDraining):
                worker.acknowledge(receipt)
            self.assertEqual(client.calls, [])
            self.assertEqual(Journal(Path(directory)).pending()[0], receipt)

    def test_sigterm_stops_child_without_acknowledging_unfinished_work(self):
        previous = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT)}
        client = FakeClient()
        try:
            with tempfile.TemporaryDirectory() as directory:
                worker = Worker(client, Path(directory), max_seconds=300, memory_mb=0)
                timer = threading.Timer(0.5, os.kill, args=(os.getpid(), signal.SIGTERM))
                started = time.monotonic()
                with patch('msw_engine.runner.child_task', slow_task):
                    timer.start()
                    try:
                        result = worker.loop(poke_cron_seconds=0)
                    finally:
                        timer.cancel()
                self.assertEqual(result, 0)
                self.assertLess(time.monotonic() - started, 15)
                self.assertEqual(client.calls, ['claim'])
                self.assertEqual(worker.journal.pending(), [])
                self.assertEqual(json.loads((Path(directory) / 'health.json').read_text())['state'], 'draining')
                self.assertFalse(multiprocessing.active_children())
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)


if __name__ == '__main__':
    unittest.main()
