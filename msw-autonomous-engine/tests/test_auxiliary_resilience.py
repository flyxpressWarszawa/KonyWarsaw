import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from msw_engine import render_auxiliary as auxiliary
from msw_engine.client import TransportError
from msw_engine.runner import Worker


class StopAfterCycle:
    def __init__(self, stop_at_start=False):
        self.waits = []
        self.stopped = False
        self.stop_at_start = stop_at_start

    def is_set(self):
        return self.stopped

    def wait(self, seconds=None):
        self.waits.append(seconds)
        if self.stop_at_start or seconds is None or len(self.waits) >= 2:
            self.stopped = True
        return self.stopped


class ContinueCycle:
    def is_set(self):
        return False

    def wait(self, seconds):
        return False


class ReadyGate:
    def check(self, client):
        return {"state": "ready"}


def job(index):
    raw = json.dumps({"title": "Bounded replay", "content": "public test data"})
    return {"id": f"00000000-0000-0000-0000-{index:012d}", "kind": "seo.analyze",
            "payload": raw, "payload_sha256": hashlib.sha256(raw.encode()).hexdigest()}


class QueueClient:
    wordpress_url = "https://example.test"

    def __init__(self, old_error=0):
        self.calls = []
        self.results = {}
        self.claims = 0
        self.old_error = old_error

    def send(self, action, payload):
        ident = payload.get("job_id")
        self.calls.append((action, ident))
        old = ident is not None and int(ident.rsplit("-", 1)[1]) < 100
        if action == "claim":
            self.claims += 1
            if "batch_limit" in payload:
                return {"state": "claimed_batch", "batch_protocol": "msw.seo.batch.v1", "jobs": [
                    {"job": job(100 + self.claims * 10 + index), "lease_token": "test-lease", "lease_seconds": 900}
                    for index in range(2)]}
            return {"state": "claimed", "job": job(100 + self.claims), "lease_token": "test-lease"}
        if action == "complete":
            if old and self.old_error:
                raise TransportError("test_rejection", self.old_error)
            self.results[ident] = payload["result_sha256"]
            return {"stored": True, "result_sha256": payload["result_sha256"]}
        if action == "status":
            if old and self.old_error == 409:
                raise TransportError("test_missing", 404)
            return {"state": "completed", "result_sha256": self.results[ident]}
        raise AssertionError(action)


class RecordingWorker(Worker):
    # This fixture exercises real journaling/replay/ACKs. CPU execution is
    # covered by the existing real local SEO job test, not repeated here.
    def execute(self, item, lease):
        return self.journal.save(item, lease, {"ok": True, "data": {"fixture": True}})


class AuxiliaryResilienceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.clock = [10000.0]
        environment = patch.dict(os.environ, {"MSW_DATA_DIR": self.tmp.name,
                                              "MSW_AUTONOMOUS_AGENT": "0", "MSW_RUNTIME_MODE": "render-free"})
        environment.start()
        self.addCleanup(environment.stop)

    def backoff(self):
        return auxiliary.AuxiliaryBackoff(self.directory, clock=lambda: self.clock[0])

    def run_main(self, event, *, result="idle", error=None):
        with patch.object(auxiliary.threading, "Event", return_value=event), \
             patch.object(auxiliary.signal, "signal"), \
             patch.object(auxiliary.time, "time", side_effect=lambda: self.clock[0]), \
             patch.object(auxiliary, "RetryAwareClient"), patch.object(auxiliary, "Worker"), \
             patch.object(auxiliary, "emit") as emitted, \
             patch.object(auxiliary, "run_cycle", return_value=result, side_effect=error) as cycle:
            self.assertEqual(auxiliary.main(), 0)
            return cycle.call_count, emitted.call_args_list

    def seed_receipts(self, worker, count=8):
        for index in range(1, count + 1):
            worker.journal.save(job(index), "old-lease", {"ok": True, "data": {"index": index}})

    def test_one_old_receipt_total_even_when_two_fresh_jobs_complete(self):
        client = QueueClient()
        worker = RecordingWorker(client, self.directory)
        self.seed_receipts(worker)
        with patch.object(auxiliary, "emit"):
            self.assertEqual(auxiliary.run_cycle(client, worker, ReadyGate(), ContinueCycle()), "completed")
        old_completes = [ident for action, ident in client.calls
                         if action == "complete" and int(ident.rsplit("-", 1)[1]) < 100]
        self.assertEqual(len(old_completes), 1)
        self.assertEqual(client.claims, 1)
        self.assertEqual(len(worker.journal.pending()), 7)

    def test_deferred_or_conflicted_old_receipt_consumes_the_single_replay_budget(self):
        for status in (404, 409):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as directory:
                client = QueueClient(old_error=status)
                worker = RecordingWorker(client, Path(directory))
                self.seed_receipts(worker)
                with patch.object(auxiliary, "emit"):
                    auxiliary.run_cycle(client, worker, ReadyGate(), ContinueCycle())
                old_completes = [ident for action, ident in client.calls
                                 if action == "complete" and int(ident.rsplit("-", 1)[1]) < 100]
                self.assertEqual(len(old_completes), 1)
                self.assertEqual(client.claims, 1)
                with worker.journal.connect() as db:
                    self.assertEqual(db.execute("SELECT COUNT(*) FROM receipts WHERE state='pending_deferred'").fetchone()[0], 1)
                    self.assertEqual(db.execute("SELECT COUNT(*) FROM receipts WHERE state='pending'").fetchone()[0], 7)

    def test_failed_replay_preserves_all_receipts_and_prevents_new_claims(self):
        client = QueueClient(old_error=503)
        worker = RecordingWorker(client, self.directory)
        self.seed_receipts(worker)
        with self.assertRaises(TransportError):
            auxiliary.run_cycle(client, worker, ReadyGate(), ContinueCycle())
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(client.claims, 0)
        self.assertEqual(len(worker.journal.pending()), 8)

    def test_default_main_worker_replay_budget_stays_eight(self):
        client = QueueClient()
        worker = RecordingWorker(client, self.directory)
        self.seed_receipts(worker, 10)
        self.assertEqual(worker.replay_receipts(), 8)
        self.assertEqual(len(worker.journal.pending()), 2)

    def test_invalid_replay_limits_never_send_requests(self):
        client = QueueClient()
        worker = RecordingWorker(client, self.directory)
        for value in (True, 0, -1, 9, 1.0):
            with self.subTest(value=value), self.assertRaises(ValueError):
                worker.replay_receipts(limit=value)
        self.assertEqual(client.calls, [])

    def test_long_retry_after_survives_restart_without_network_calls(self):
        self.backoff().save(2, 7200)
        self.clock[0] += 120
        event = StopAfterCycle(stop_at_start=True)
        calls, _ = self.run_main(event)
        self.assertEqual(event.waits, [7080])
        self.assertEqual(calls, 0)
        self.assertEqual(self.backoff().read(), (2, 7080))

    def test_failure_streak_survives_restart_and_resets_after_success(self):
        self.backoff().save(2, 300)
        self.clock[0] += 600
        event = StopAfterCycle()
        calls, _ = self.run_main(event, error=TransportError("outage", 503))
        self.assertEqual(calls, 1)
        self.assertEqual(event.waits, [300, 1200])
        self.assertEqual(self.backoff().read(), (3, 1200))
        self.clock[0] += 1800
        self.run_main(StopAfterCycle(), result="idle")
        self.assertEqual(self.backoff().read(), (0, 300))

    def test_success_never_shortens_an_unexpired_server_deadline(self):
        self.backoff().save(3, 7200)
        before = (self.directory / "auxiliary-backoff.json").read_bytes()
        deadline = json.loads(before)["not_before"]
        self.clock[0] += 12.25
        self.backoff().save(0, 300)
        self.assertEqual(json.loads((self.directory / "auxiliary-backoff.json").read_bytes())["not_before"], deadline)

    def test_corrupt_state_blocks_before_any_cycle(self):
        path = self.directory / "auxiliary-backoff.json"
        invalid = [b"not json", b"{}", b"x" * 1025,
                   b'{"version":1,"failures":0,"failures":1,"not_before":0}',
                   b'{"version":1,"failures":true,"not_before":0}',
                   b'{"version":1,"failures":0,"not_before":1e999}',
                   b'{"version":1,"failures":0,"not_before":-1}',
                   json.dumps({"version": 1, "failures": 0, "not_before": 10000 + 8 * 86400}).encode()]
        for raw in invalid:
            with self.subTest(raw=raw[:80]):
                path.write_bytes(raw)
                event = StopAfterCycle()
                calls, emitted = self.run_main(event)
                self.assertEqual(calls, 0)
                self.assertEqual(event.waits, [None])
                self.assertEqual(emitted[0].args[0], "configuration_required")

    def test_failed_atomic_write_keeps_previous_deadline_and_cleans_temporary(self):
        self.backoff().save(1, 7200)
        path = self.directory / "auxiliary-backoff.json"
        previous = path.read_bytes()
        with patch.object(auxiliary.os, "replace", side_effect=OSError("test unavailable")):
            with self.assertRaises(OSError):
                self.backoff().save(2, 9000)
        self.assertEqual(path.read_bytes(), previous)
        self.assertEqual(list(self.directory.glob(".aux-backoff-*")), [])

    def test_unwritable_checkpoint_stops_without_second_cycle(self):
        event = StopAfterCycle()
        with patch.object(auxiliary.AuxiliaryBackoff, "save", side_effect=OSError("test full disk")):
            calls, emitted = self.run_main(event)
        self.assertEqual(calls, 1)
        self.assertEqual(event.waits, [300, None])
        self.assertTrue(any(call.args[0] == "backoff_storage_blocked" for call in emitted))

    def test_checkpoint_contains_only_timing_metadata_and_is_private(self):
        self.backoff().save(100, 300)
        path = self.directory / "auxiliary-backoff.json"
        self.assertEqual(set(json.loads(path.read_bytes())), {"version", "failures", "not_before"})
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.backoff().read(), (5, 300))


if __name__ == "__main__":
    unittest.main()
