import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from msw_engine import render_auxiliary
from msw_engine.client import TransportError
from msw_engine.render_auxiliary import CAPABILITY, ProtocolError, retry_delay, step
from msw_engine.runner import Worker, WorkerDraining


class FakeClient:
    wordpress_url = "https://mobilny-serwis-warszawa.pl"

    def __init__(self, kind=CAPABILITY):
        raw = json.dumps({"title": "Próba", "content": "tekst techniczny"})
        self.job = {"id": "00000000-0000-0000-0000-000000000001", "kind": kind,
                    "payload": raw, "payload_sha256": hashlib.sha256(raw.encode()).hexdigest()}
        self.calls = []
        self.result_sha256 = None

    def send(self, action, payload):
        self.calls.append((action, payload))
        if action == "claim":
            return {"state": "claimed", "job": self.job, "lease_token": "lease-token"}
        if action == "complete":
            self.result_sha256 = payload["result_sha256"]
            return {"stored": True, "result_sha256": self.result_sha256}
        if action == "status":
            return {"state": "completed", "result_sha256": self.result_sha256}
        raise AssertionError(action)


class AuxiliaryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = patch.dict(os.environ, {"MSW_RUNTIME_MODE": "render-free", "MSW_AUTONOMOUS_AGENT": "0"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_real_local_seo_job_and_signed_queue_receipt_contract(self):
        client = FakeClient()
        worker = Worker(client, Path(self.tmp.name), max_seconds=30, memory_mb=256)
        out = step(client, worker)
        self.assertEqual(out["state"], "completed")
        self.assertTrue(out["server_ack_verified"])
        self.assertEqual([x[0] for x in client.calls], ["claim", "complete", "status"])
        self.assertEqual(client.calls[0][1], {"capabilities": ["seo.analyze"]})
        self.assertEqual(out["result_sha256"], client.result_sha256)
        self.assertEqual(worker.journal.pending(), [])

    def test_foreign_kind_is_not_executed_or_acknowledged(self):
        client = FakeClient("module.execute")
        worker = Worker(client, Path(self.tmp.name), max_seconds=30, memory_mb=256)
        with self.assertRaises(ProtocolError):
            step(client, worker)
        self.assertEqual([x[0] for x in client.calls], ["claim"])

    def test_draining_does_not_claim_work(self):
        client = FakeClient()
        worker = Worker(client, Path(self.tmp.name))
        with self.assertRaises(WorkerDraining):
            step(client, worker, lambda: True)
        self.assertEqual(client.calls, [])

    def test_backoff_is_at_least_five_minutes_and_increases(self):
        self.assertEqual([retry_delay(i) for i in (1, 2, 3, 4, 8)], [300, 600, 1200, 2400, 3600])
        self.assertEqual(retry_delay(1, 900), 900)

    def test_http_503_delays_next_call_at_least_five_minutes(self):
        class StopAfterWait:
            stopped = False
            waits = []

            def is_set(self):
                return self.stopped

            def wait(self, seconds=None):
                self.waits.append(seconds)
                self.stopped = True

        event = StopAfterWait()
        with patch.object(render_auxiliary.threading, "Event", return_value=event), \
             patch.object(render_auxiliary.signal, "signal"), \
             patch.object(render_auxiliary, "Client"), patch.object(render_auxiliary, "Worker"), \
             patch.object(render_auxiliary, "emit"), \
             patch.object(render_auxiliary, "step", side_effect=TransportError("server_rejected_request", 503)) as run:
            self.assertEqual(render_auxiliary.main(), 0)
        self.assertEqual(event.waits, [300])
        self.assertEqual(run.call_count, 1)


if __name__ == "__main__":
    unittest.main()
