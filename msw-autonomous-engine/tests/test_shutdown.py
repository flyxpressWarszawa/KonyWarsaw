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
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from msw_engine.runner import Worker


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
