import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from email.message import Message
from email.utils import parsedate_to_datetime
from unittest.mock import patch
from urllib.error import HTTPError

from msw_engine import render_auxiliary
from msw_engine.client import TransportError
from msw_engine.render_auxiliary import (CAPABILITY, ProtocolError, RetryAwareClient,
                                         parse_retry_after, retry_delay, step)
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
        self.env = patch.dict(os.environ, {"MSW_RUNTIME_MODE": "render-free", "MSW_AUTONOMOUS_AGENT": "0",
                                         "MSW_DATA_DIR": self.tmp.name})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.gate = type("TestReadyGate", (), {"check": lambda self, client: {"state": "ready"}})()

    def test_real_local_seo_job_and_signed_queue_receipt_contract(self):
        client = FakeClient()
        worker = Worker(client, Path(self.tmp.name), max_seconds=30, memory_mb=256)
        out = step(client, worker, gate=self.gate)
        self.assertEqual(out["state"], "completed")
        self.assertTrue(out["server_ack_verified"])
        self.assertEqual([x[0] for x in client.calls], ["claim", "complete", "status"])
        self.assertEqual(client.calls[0][1], {"capabilities": ["seo.analyze", "research.rank"]})
        self.assertEqual(out["result_sha256"], client.result_sha256)
        self.assertEqual(worker.journal.pending(), [])

    def test_foreign_kind_is_not_executed_or_acknowledged(self):
        client = FakeClient("module.execute")
        worker = Worker(client, Path(self.tmp.name), max_seconds=30, memory_mb=256)
        with self.assertRaises(ProtocolError):
            step(client, worker, gate=self.gate)
        self.assertEqual([x[0] for x in client.calls], ["claim"])

    def test_real_local_research_rank_returns_verified_receipt(self):
        client = FakeClient("research.rank")
        raw = json.dumps({"ideas": [{"id": "offer-1", "demand": 80, "competition": 20,
                                      "margin": 70, "delivery_effort": 30,
                                      "sources": ["https://example.test/evidence"]}]})
        client.job["payload"] = raw
        client.job["payload_sha256"] = hashlib.sha256(raw.encode()).hexdigest()
        worker = Worker(client, Path(self.tmp.name), max_seconds=30, memory_mb=256)
        result = step(client, worker, gate=self.gate)
        self.assertEqual(result["state"], "completed")
        self.assertTrue(result["server_ack_verified"])
        self.assertEqual(worker.journal.pending(), [])

    def test_503_after_result_keeps_receipt_and_retries_same_result(self):
        class FailingOnceClient(FakeClient):
            complete_attempts = 0
            claims = 0

            def send(self, action, payload):
                if action == "claim":
                    self.claims += 1
                    if self.claims > 1:
                        self.calls.append((action, payload))
                        return {"state": "idle"}
                if action == "complete":
                    self.complete_attempts += 1
                    if self.complete_attempts == 1:
                        self.calls.append((action, payload))
                        raise TransportError("server_rejected_request", 503)
                return super().send(action, payload)

        client = FailingOnceClient()
        worker = Worker(client, Path(self.tmp.name), max_seconds=30, memory_mb=256)
        with self.assertRaises(TransportError):
            step(client, worker, gate=self.gate)
        self.assertEqual(len(worker.journal.pending()), 1)
        self.assertEqual(step(client, worker, gate=self.gate)["state"], "idle")
        completed = [payload for action, payload in client.calls if action == "complete"]
        self.assertEqual(len(completed), 2)
        self.assertEqual(completed[0], completed[1])
        self.assertEqual(worker.journal.pending(), [])

    def test_draining_does_not_claim_work(self):
        client = FakeClient()
        worker = Worker(client, Path(self.tmp.name))
        with self.assertRaises(WorkerDraining):
            step(client, worker, lambda: True, gate=self.gate)
        self.assertEqual(client.calls, [])

    def test_backoff_is_at_least_five_minutes_and_increases(self):
        self.assertEqual([retry_delay(i) for i in (1, 2, 3, 4, 8)], [300, 600, 1200, 2400, 3600])
        self.assertEqual(retry_delay(1, 900), 900)
        self.assertEqual(retry_delay(1, 7200), 7200)

    def test_retry_after_supports_seconds_and_http_dates(self):
        date = "Wed, 21 Oct 2015 07:28:00 GMT"
        when = parsedate_to_datetime(date).timestamp()
        self.assertEqual(parse_retry_after("7200"), 7200)
        self.assertEqual(parse_retry_after(date, now=when - 7200), 7200)
        self.assertEqual(parse_retry_after(date, now=when + 1), 0)
        self.assertIsNone(parse_retry_after("invalid"))
        with self.assertRaises(ProtocolError):
            parse_retry_after("99999999999999999999999999")
        with self.assertRaises(ProtocolError):
            parse_retry_after("9" * 129)

    def test_real_transport_does_not_truncate_long_503_or_429_header(self):
        class FailingOpener:
            def __init__(self, status):
                self.status = status

            def open(self, request, timeout):
                headers = Message()
                headers["Retry-After"] = "7200"
                raise HTTPError(request.full_url, self.status, "busy", headers, None)

        with patch.dict(os.environ, {"MSW_DATA_DIR": self.tmp.name}):
            for status in (429, 503):
                with self.subTest(status=status):
                    client = RetryAwareClient("https://example.com", "https://example.com/transport",
                                              "test_node", "x" * 32)
                    original = client.opener = FailingOpener(status)
                    with self.assertRaises(TransportError) as caught:
                        client.send("claim", {"capabilities": [CAPABILITY]})
                    self.assertEqual(caught.exception.status, status)
                    self.assertEqual(caught.exception.retry_after, 7200)
                    self.assertIs(client.opener, original)

    def test_long_retry_after_blocks_instead_of_retrying_early(self):
        class FailingOpener:
            def open(self, request, timeout):
                headers = Message()
                headers["Retry-After"] = "999999999999"
                raise HTTPError(request.full_url, 503, "busy", headers, None)

        with patch.dict(os.environ, {"MSW_DATA_DIR": self.tmp.name}):
            client = RetryAwareClient("https://example.com", "https://example.com/transport",
                                      "test_node", "x" * 32)
            original = client.opener = FailingOpener()
            with self.assertRaises(ProtocolError):
                client.send("claim", {"capabilities": [CAPABILITY]})
            self.assertIs(client.opener, original)

    def test_http_503_delays_next_call_at_least_five_minutes(self):
        class StopAfterWait:
            def __init__(self):
                self.stopped = False
                self.waits = []

            def is_set(self):
                return self.stopped

            def wait(self, seconds=None):
                self.waits.append(seconds)
                if len(self.waits) == 1:
                    return False
                self.stopped = True
                return True

        event = StopAfterWait()
        with patch.object(render_auxiliary.threading, "Event", return_value=event), \
             patch.object(render_auxiliary.signal, "signal"), \
             patch.object(render_auxiliary, "RetryAwareClient"), patch.object(render_auxiliary, "Worker"), \
             patch.object(render_auxiliary, "emit"), \
             patch.object(render_auxiliary, "run_cycle", side_effect=TransportError("server_rejected_request", 503)) as run:
            self.assertEqual(render_auxiliary.main(), 0)
        self.assertEqual(event.waits, [300, 300])
        self.assertEqual(run.call_count, 1)

    def test_stop_during_quiet_start_does_not_contact_queue(self):
        class AlreadyStopped:
            def is_set(self):
                return True

            def wait(self, seconds=None):
                return True

        with patch.object(render_auxiliary.threading, "Event", return_value=AlreadyStopped()), \
             patch.object(render_auxiliary.signal, "signal"), \
             patch.object(render_auxiliary, "RetryAwareClient"), patch.object(render_auxiliary, "Worker"), \
             patch.object(render_auxiliary, "emit"), patch.object(render_auxiliary, "run_cycle") as run:
            self.assertEqual(render_auxiliary.main(), 0)
        run.assert_not_called()

    def test_auth_failure_does_not_repeat_requests(self):
        class Event:
            stopped = False

            def __init__(self):
                self.waits = []

            def is_set(self):
                return self.stopped

            def wait(self, seconds=None):
                self.waits.append(seconds)
                if seconds is None:
                    self.stopped = True
                    return True
                return False

        event = Event()
        with patch.object(render_auxiliary.threading, "Event", return_value=event), \
             patch.object(render_auxiliary.signal, "signal"), \
             patch.object(render_auxiliary, "RetryAwareClient"), patch.object(render_auxiliary, "Worker"), \
             patch.object(render_auxiliary, "emit"), \
             patch.object(render_auxiliary, "run_cycle", side_effect=TransportError("rejected", 401)) as run:
            self.assertEqual(render_auxiliary.main(), 0)
        self.assertEqual(event.waits, [300, None])
        self.assertEqual(run.call_count, 1)


if __name__ == "__main__":
    unittest.main()

