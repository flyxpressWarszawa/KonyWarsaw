"""Read-only/offline reproductions of Qwen's worker heartbeat and cooldown audit.

No real process, network, provider request, Render configuration, or paid model.
The intended contracts are marked expectedFailure until the production design
has been approved and a patch has passed independent review.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from msw_engine.client import Client, TransportError
from msw_engine.runner import Worker
from msw_engine.recovery import OriginPaused


class FakeProcess:
    def __init__(self):
        self.terminated = False
        self.started = False
        self.killed = False

    def start(self):
        self.started = True

    def is_alive(self):
        return self.started and not self.terminated

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True
        self.terminated = True

    def join(self, timeout=None):
        pass


class FakePipeEnd:
    def __init__(self):
        self.polls = 0
        self.closed = False

    def poll(self, timeout=None):
        self.polls += 1
        # A still-running child has no result ready at the failed heartbeat.
        # The separate salvage test supplies a completed result during error.
        return self.polls > 2

    def recv_bytes(self, limit=None):
        return b'{"ok":true,"data":{"offline":true}}'

    def close(self):
        self.closed = True


class FakeContext:
    def __init__(self):
        self.proc = FakeProcess()
        self.parent = FakePipeEnd()
        self.child = FakePipeEnd()

    def Pipe(self, duplex=False):
        assert duplex is False
        return self.parent, self.child

    def Process(self, target, args):
        # Never invoke target or spawn the child: offline simulation only.
        return self.proc


class HeartbeatClient:
    def __init__(self):
        self.calls = []

    def send(self, action, payload):
        self.calls.append(action)
        if action == "heartbeat":
            raise TransportError("transport_unavailable", 503, 30)
        raise AssertionError("No other transport actions are allowed in this test")


def sample_job():
    raw = json.dumps({"text": "offline synthetic only"}, separators=(",", ":"))
    return {
        "id": "offline-heartbeat-0001",
        "kind": "seo.analyze",
        "payload": raw,
        "payload_sha256": hashlib.sha256(raw.encode()).hexdigest(),
    }


class QwenOfflineHeartbeatAudit(unittest.TestCase):
    def run_simulation(self, directory, *, expect_failure):
        fake = FakeContext()
        client = HeartbeatClient()
        worker = Worker(client, Path(directory), max_seconds=300, memory_mb=0)
        job = sample_job()
        with patch("msw_engine.runner.multiprocessing.get_context", return_value=fake):
            with patch("msw_engine.runner.time.monotonic", side_effect=[1000, 1061, 1062]):
                if expect_failure:
                    with self.assertRaises(TransportError):
                        worker.execute(job, "unit-lease")
                    receipt = None
                else:
                    receipt = worker.execute(job, "unit-lease")
        return fake, client, worker, job, receipt

    def test_current_behavior_heartbeat_failure_kills_inflight_child_without_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            fake, client, worker, job, receipt = self.run_simulation(
                directory, expect_failure=True
            )
            self.assertEqual(client.calls, ["heartbeat"])
            self.assertTrue(fake.proc.terminated)
            self.assertFalse(fake.proc.killed)
            self.assertIsNone(worker.journal.lookup(job["id"], job["payload_sha256"]))
            self.assertIsNone(receipt)

    @unittest.expectedFailure
    def test_desired_behavior_preserves_inflight_work_during_transient_heartbeat_error(self):
        # Design gate: only implement after proving lease/idempotency semantics.
        # A transient transport error must not silently claim paid work succeeded
        # or cause an unreviewed new paid attempt for the same request.
        with tempfile.TemporaryDirectory() as directory:
            fake, client, worker, job, receipt = self.run_simulation(
                directory, expect_failure=False
            )
            self.assertEqual(client.calls, ["heartbeat"])
            self.assertIsNotNone(receipt)
            self.assertEqual(receipt["job_id"], job["id"])


class QwenOfflineRecoveryAudit(unittest.TestCase):
    def test_client_preserves_origin_cooldown_retry_after(self):
        client = Client(
            "https://example.org",
            "https://example.org/transport.php",
            "unit-node",
            "X" * 40,
        )
        with patch("msw_engine.client.protected_open", side_effect=OriginPaused(120)):
            with self.assertRaises(TransportError) as caught:
                client.send("status", {"job_id": "offline-only"})
        self.assertGreaterEqual(caught.exception.retry_after, 120)


if __name__ == "__main__":
    unittest.main()
