"""Offline paid-call deduplication, receipt salvage and thread-local HTTP tests.

No network, subprocess execution, or paid providers. Regression checks are
strictly local to the candidate branch; full production merge remains HOLD.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from msw_engine.client import Client, TransportError
from msw_engine.paid_attempt_guard import PaidAttemptGuard
from msw_engine.runner import Worker


def job_for(job_id="paid-job-00001", request_id="idempotency-test-00001"):
    payload = {"request_id": request_id, "prompt": "synthetic only"}
    raw = json.dumps(payload, separators=(",", ":"))
    return {
        "id": job_id, "kind": "llm.generate", "payload": raw,
        "payload_sha256": hashlib.sha256(raw.encode()).hexdigest(),
    }, payload


class FakeProcess:
    def __init__(self):
        self.started = False
        self.terminated = False

    def start(self):
        self.started = True

    def is_alive(self):
        return self.started and not self.terminated

    def terminate(self):
        self.terminated = True

    def join(self, timeout=None):
        pass

    def kill(self):
        self.terminated = True


class FakePipe:
    def __init__(self, ready_after):
        self.polls = 0
        self.ready_after = ready_after

    def poll(self, timeout=None):
        self.polls += 1
        return self.polls > self.ready_after

    def recv_bytes(self, *args):
        return b'{"ok":true,"data":{"receipt":"fake-offline"}}'

    def close(self):
        pass


class FakeContext:
    def __init__(self, ready_after=999):
        self.process = FakeProcess()
        self.parent = FakePipe(ready_after)
        self.child = FakePipe(999)

    def Pipe(self, duplex=False):
        assert not duplex
        return self.parent, self.child

    def Process(self, **kwargs):
        # Deliberately never spawn any process.
        return self.process


class FakeClient:
    def __init__(self, *, fail_heartbeat=True):
        self.calls = []
        self.fail_heartbeat = fail_heartbeat

    def send(self, action, payload):
        self.calls.append(action)
        if action == "heartbeat" and self.fail_heartbeat:
            raise TransportError("origin_recovery_backoff", retry_after=120)
        if action == "heartbeat":
            return {"ok": True}
        raise AssertionError("This test forbids network transport actions")


class PaidAttemptSafetyTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name)

    def test_paid_attempt_is_reserved_before_child_and_cannot_rerun_after_bad_heartbeat(self):
        job, _ = job_for()
        context = FakeContext(ready_after=999)
        client = FakeClient()
        worker = Worker(client, self.path, max_seconds=200, memory_mb=0)
        with patch("msw_engine.runner.handlers", return_value={"llm.generate": object()}), \
             patch("msw_engine.runner.multiprocessing.get_context", return_value=context) as factory, \
             patch("msw_engine.runner.time.monotonic", side_effect=[1000, 1061]):
            with self.assertRaisesRegex(TransportError, "origin_recovery_backoff"):
                worker.execute(job, "lease-A")
            self.assertTrue(context.process.terminated)
            self.assertIsNone(worker.journal.lookup(job["id"], job["payload_sha256"]))
            with self.assertRaisesRegex(TransportError, "paid_attempt_outcome_unverified"):
                worker.execute(job, "lease-B")
        self.assertEqual(factory.call_count, 1)
        self.assertEqual(client.calls, ["heartbeat"])

    def test_second_server_job_with_same_idempotency_key_is_blocked(self):
        first, payload = job_for()
        second, _ = job_for("paid-job-00002")
        gate = PaidAttemptGuard(self.path)
        gate.reserve(first, payload)
        with self.assertRaisesRegex(TransportError, "paid_attempt_outcome_unverified"):
            gate.reserve(second, payload)

    def test_conflicting_payload_for_same_id_is_blocked(self):
        first, payload = job_for()
        gate = PaidAttemptGuard(self.path)
        gate.reserve(first, payload)
        changed, changed_payload = job_for(request_id="idempotency-test-00001")
        changed["payload_sha256"] = "d" * 64
        with self.assertRaisesRegex(TransportError, "paid_attempt_payload_conflict"):
            gate.reserve(changed, changed_payload)

    def test_original_result_saved_then_released_lease_reuses_receipt_without_provider_call(self):
        job, _ = job_for()
        context = FakeContext(ready_after=0)
        client = FakeClient(fail_heartbeat=False)
        worker = Worker(client, self.path, max_seconds=200, memory_mb=0)
        with patch("msw_engine.runner.handlers", return_value={"llm.generate": object()}), \
             patch("msw_engine.runner.multiprocessing.get_context", return_value=context) as factory, \
             patch("msw_engine.runner.time.monotonic", return_value=1000):
            receipt1 = worker.execute(job, "lease-A")
            receipt2 = worker.execute(job, "lease-B")
        self.assertEqual(receipt1["result_sha256"], receipt2["result_sha256"])
        self.assertEqual(receipt2["lease_token"], "lease-B")
        self.assertEqual(factory.call_count, 1)

    def test_heartbeat_transport_failure_salvages_already_received_result(self):
        job, _ = job_for()
        context = FakeContext(ready_after=1)
        client = FakeClient()
        worker = Worker(client, self.path, max_seconds=200, memory_mb=0)
        with patch("msw_engine.runner.handlers", return_value={"llm.generate": object()}), \
             patch("msw_engine.runner.multiprocessing.get_context", return_value=context), \
             patch("msw_engine.runner.time.monotonic", side_effect=[1000, 1061]):
            receipt = worker.execute(job, "lease-A")
        self.assertEqual(receipt["lease_token"], "lease-A")
        self.assertIn("fake-offline", receipt["result_json"])
        self.assertEqual(receipt["state"], "pending")
        self.assertEqual(client.calls, ["heartbeat"])

    def test_guards_are_atomic_across_threads_and_runners(self):
        job, payload = job_for()
        ready = threading.Barrier(6)
        def attempt(_):
            ready.wait(timeout=10)
            try:
                PaidAttemptGuard(self.path).reserve(job, payload)
                return "reserved"
            except TransportError as error:
                return error.code
        with ThreadPoolExecutor(max_workers=6) as executor:
            results = list(executor.map(attempt, range(6)))
        self.assertEqual(results.count("reserved"), 1)
        self.assertEqual(results.count("paid_attempt_outcome_unverified"), 5)

    def test_concurrent_new_database_schema_and_reservations_are_bounded(self):
        # Reproduce the intermittent SQLite locked failure from CI. Every
        # round starts with a new DB; no live processes or paid inference.
        for round_number in range(16):
            with tempfile.TemporaryDirectory() as folder:
                directory = Path(folder)
                job, payload = job_for(
                    job_id="paid-job-stress-%04d" % round_number,
                    request_id="idempotency-stress-%04d" % round_number
                )
                barrier = threading.Barrier(8)

                def claim(_):
                    barrier.wait(timeout=10)
                    try:
                        PaidAttemptGuard(directory).reserve(job, payload)
                        return "reserved"
                    except TransportError as error:
                        return error.code

                with ThreadPoolExecutor(max_workers=8) as pool:
                    states = list(pool.map(claim, range(8)))
                self.assertEqual(states.count("reserved"), 1)
                self.assertEqual(states.count("paid_attempt_outcome_unverified"), 7)

    def test_http_opener_is_shared_within_thread_not_between_threads(self):
        client = Client("https://example.org", "https://example.org/transport.php",
                        "unit-node", "X" * 40)
        main_opener = client.opener
        barrier = threading.Barrier(4)
        def get_thread_opener(_):
            barrier.wait(timeout=10)
            a, b = client.opener, client.opener
            self.assertIs(a, b)
            return a
        with ThreadPoolExecutor(max_workers=4) as pool:
            openers = list(pool.map(get_thread_opener, range(4)))
        self.assertEqual(len({id(x) for x in openers}), 4)
        self.assertNotIn(main_opener, openers)

    def test_denied_or_corrupted_db_does_not_start_child(self):
        job, payload = job_for()
        PaidAttemptGuard(self.path).reserve(job, payload)
        context = FakeContext()
        worker = Worker(FakeClient(), self.path, max_seconds=200, memory_mb=0)
        with patch("msw_engine.runner.handlers", return_value={"llm.generate": object()}), \
             patch("msw_engine.runner.multiprocessing.get_context", return_value=context) as factory:
            with self.assertRaisesRegex(TransportError, "paid_attempt_outcome_unverified"):
                worker.execute(job, "another-lease")
        factory.assert_not_called()


if __name__ == "__main__":
    unittest.main()
