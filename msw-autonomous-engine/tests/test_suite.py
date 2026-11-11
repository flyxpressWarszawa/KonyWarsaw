from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import hashlib
import multiprocessing
import os
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from msw_engine.client import TransportError
from msw_engine.runner import Journal, Worker
from msw_engine.suite import analyze
from msw_engine.tasks import TaskError, runtime_catalog


TEST_JOB = {"id": "job-one", "payload_sha256": "a" * 64}


def completed_readback(receipt):
    return {"id": receipt["job_id"], "state": "completed", "payload_sha256": receipt["payload_sha256"],
            "result_sha256": receipt["result_sha256"]}


class DurableOrigin:
    def __init__(self, directory, crash_after_commit=False):
        self.path = directory / "origin.sqlite3"
        self.crash_after_commit = crash_after_commit

    def send(self, action, payload):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("INSERT INTO calls VALUES (?)", (action,))
            if action == "complete":
                db.execute("INSERT INTO results VALUES (?,?,?)", (payload["job_id"], payload["payload_sha256"], payload["result_sha256"]))
            elif action == "status":
                row = db.execute("SELECT id,payload_sha256,result_sha256 FROM results WHERE id=?", (payload["job_id"],)).fetchone()
                if row:
                    return completed_readback(dict(zip(("job_id", "payload_sha256", "result_sha256"), row)))
                return {"state": "running"}
            else:
                raise AssertionError(action)
        if self.crash_after_commit:
            os._exit(72)
        return {"stored": True, "result_sha256": payload["result_sha256"]}


def submit_then_crash(directory):
    worker = Worker(DurableOrigin(directory, crash_after_commit=True), directory)
    worker.acknowledge(worker.journal.save(TEST_JOB, "lease", {"ok": True}))


def recover_in_new_process(directory):
    worker = Worker(DurableOrigin(directory), directory)
    assert worker.replay_receipts() == 1
    assert worker.journal.lookup(TEST_JOB["id"], TEST_JOB["payload_sha256"])["state"] == "acknowledged"


class SuiteTests(unittest.TestCase):
    def snapshot(self, **changes):
        value = {"schema": "msw.suite.snapshot.v1", "provenance": "authenticated_wordpress_plugin_metadata",
                 "origin": "https://mobilny-serwis-warszawa.pl", "observed_at": int(time.time()),
                 "modules": [{"plugin": "example/example.php", "installed": True, "active": True,
                              "contract_version": "1.0.0", "missing_dependencies": [], "cron": [], "state": {}}]}
        value.update(changes)
        return value

    def test_integrated_is_not_proof_of_execution(self):
        result = analyze(self.snapshot())
        self.assertEqual(result["counts"]["diagnostics_integrated"], 1)
        self.assertFalse(result["execution_verified"])
        self.assertFalse(result["publication_verified"])
        self.assertEqual(result["actions_performed"], [])

    def test_dependency_and_overdue_cron_are_reported(self):
        snapshot = self.snapshot()
        snapshot["modules"][0].update(missing_dependencies=["woocommerce"], cron=[{"hook": "test_tick", "next_at": snapshot["observed_at"] - 2000}])
        self.assertEqual({x["code"] for x in analyze(snapshot)["issues"]}, {"dependency_inactive", "cron_overdue"})

    def test_inactive_module_is_not_labeled_broken(self):
        snapshot = self.snapshot()
        snapshot["modules"][0].update(active=False, missing_dependencies=["woocommerce"])
        self.assertEqual(analyze(snapshot)["issues"], [])

    def test_accepts_empty_php_array_as_empty_state_but_not_nonempty_lists(self):
        snapshot = self.snapshot()
        snapshot["modules"][0]["state"] = []
        self.assertEqual(analyze(snapshot)["issues"], [])
        snapshot["modules"][0]["state"] = ["not_an_object"]
        with self.assertRaises(TaskError):
            analyze(snapshot)

    def test_rejects_untrusted_origin_future_and_duplicate_modules(self):
        for changes in [{"origin": "https://attacker.invalid"}, {"observed_at": int(time.time()) + 3600}, {"modules": self.snapshot()["modules"] * 2}]:
            with self.subTest(changes=changes), self.assertRaises(TaskError):
                analyze(self.snapshot(**changes))

    def test_catalog_without_pack_still_exposes_current_worker(self):
        with tempfile.TemporaryDirectory() as d, patch("msw_engine.tasks.handlers", return_value={"runtime.catalog": None}):
            catalog = runtime_catalog({}, Path(d))
            self.assertEqual(catalog["worker_version"], "1.7.0")
        self.assertEqual(catalog["capabilities"], ["runtime.catalog"])


class ReceiptTests(unittest.TestCase):
    def test_reusing_job_id_for_different_payload_cannot_corrupt_receipt(self):
        with tempfile.TemporaryDirectory() as d:
            journal = Journal(Path(d))
            job = {"id": "job-one", "payload_sha256": "a" * 64}
            journal.save(job, "lease", {"ok": True})
            with self.assertRaises(TransportError):
                journal.save({**job, "payload_sha256": "b" * 64}, "other", {"ok": False})
            self.assertEqual(journal.lookup("job-one", "a" * 64)["lease_token"], "lease")

    def test_completion_lost_in_transport_is_recovered_from_server_digest(self):
        class Client:
            def send(self, action, payload):
                if action == "complete":
                    raise TransportError("expired_lease", 409)
                return completed_readback(receipt)
        with tempfile.TemporaryDirectory() as d:
            worker = Worker(Client(), Path(d))
            receipt = worker.journal.save({"id": "job-one", "payload_sha256": "a" * 64}, "lease", {"ok": True})
            self.assertEqual(worker.replay_receipts(), 1)
            self.assertEqual(worker.journal.pending(), [])
            self.assertEqual(worker.journal.lookup("job-one", "a" * 64)["state"], "acknowledged")

    def test_crash_after_origin_commit_recovers_without_second_complete(self):
        with tempfile.TemporaryDirectory() as d:
            directory = Path(d)
            origin = DurableOrigin(directory)
            with closing(sqlite3.connect(origin.path)) as db, db:
                db.executescript("CREATE TABLE calls(action TEXT); CREATE TABLE results(id TEXT PRIMARY KEY, payload_sha256 TEXT, result_sha256 TEXT);")
            context = multiprocessing.get_context("spawn")
            for target, expected_exit in ((submit_then_crash, 72), (recover_in_new_process, 0)):
                process = context.Process(target=target, args=(directory,))
                process.start()
                try:
                    process.join(timeout=5)
                    self.assertEqual(process.exitcode, expected_exit)
                finally:
                    if process.is_alive():
                        process.kill()
                        process.join(timeout=5)
                    process.close()
            with closing(sqlite3.connect(origin.path)) as db:
                self.assertEqual(db.execute("SELECT action FROM calls ORDER BY rowid").fetchall(), [("complete",), ("status",)])
                self.assertEqual(db.execute("SELECT COUNT(*) FROM results").fetchone(), (1,))

    def test_legacy_pending_receipts_require_full_identity_on_status_only(self):
        with tempfile.TemporaryDirectory() as d:
            for index, field in enumerate(("id", "payload_sha256", "result_sha256")):
                with self.subTest(field=field):
                    client = Mock()
                    worker = Worker(client, Path(d) / str(index))
                    receipt = worker.journal.save(TEST_JOB, "lease", {"ok": True})
                    with closing(worker.journal.connect()) as db, db:
                        db.execute("UPDATE receipts SET state='pending'")
                    client.send.return_value = {**completed_readback(receipt), field: "different"}
                    self.assertEqual(worker.replay_receipts(), 0)
                    client.send.assert_called_once_with("status", {"job_id": TEST_JOB["id"]})
                    self.assertEqual(worker.journal.lookup(TEST_JOB["id"], TEST_JOB["payload_sha256"])["state"], "readback_deferred")

    def test_unattempted_receipt_can_be_reserved_with_the_new_lease(self):
        with tempfile.TemporaryDirectory() as d:
            journal = Journal(Path(d))
            old = journal.save(TEST_JOB, "old-lease", {"ok": True})
            current = journal.save(TEST_JOB, "new-lease", {"ok": True})
            self.assertFalse(journal.reserve_completion(old))
            self.assertTrue(journal.reserve_completion(current))

    def test_interruption_after_reservation_keeps_unknown_outcome_pending(self):
        with tempfile.TemporaryDirectory() as d:
            directory = Path(d)
            journal = Journal(directory)
            receipt = journal.save(TEST_JOB, "lease", {"ok": True})
            self.assertTrue(journal.reserve_completion(receipt))
            client = Mock()
            client.send.return_value = {"state": "running"}
            worker = Worker(client, directory)
            self.assertEqual(worker.replay_receipts(), 0)
            client.send.assert_called_once_with("status", {"job_id": TEST_JOB["id"]})
            self.assertEqual(journal.lookup(TEST_JOB["id"], TEST_JOB["payload_sha256"])["state"], "readback_deferred")

    def test_competing_writers_reserve_only_one_complete(self):
        entered, proceed = threading.Event(), threading.Event()
        complete_requests = []
        result = None

        class Client:
            def send(self, action, payload):
                nonlocal result
                if action == "complete":
                    complete_requests.append(action)
                    entered.set()
                    if not proceed.wait(timeout=3):
                        raise AssertionError("Second writer did not inspect the existing attempt")
                    result = completed_readback(payload)
                    return {"stored": True, "result_sha256": result["result_sha256"]}
                return result if result is not None else {"state": "running"}

        with tempfile.TemporaryDirectory() as d, ThreadPoolExecutor(max_workers=2) as pool:
            first, second = Worker(Client(), Path(d)), Worker(Client(), Path(d))
            receipt = first.journal.save(TEST_JOB, "lease", {"ok": True})
            pending = pool.submit(first.acknowledge, receipt)
            try:
                self.assertTrue(entered.wait(timeout=2))
                self.assertFalse(pool.submit(second.acknowledge, receipt).result(timeout=2)["server_ack_verified"])
            finally:
                proceed.set()
            self.assertTrue(pending.result(timeout=3)["server_ack_verified"])
            self.assertEqual(complete_requests, ["complete"])
            self.assertEqual(second.journal.pending(), [])

    def test_missing_old_receipt_is_preserved_and_other_work_can_continue(self):
        class Client:
            def send(self, action, payload):
                if action == "complete": raise TransportError("expired_lease", 409)
                if action == "status": raise TransportError("server_rejected_request", 404)
                if action == "claim": return {"state": "idle"}
                raise AssertionError(action)
        with tempfile.TemporaryDirectory() as d:
            worker = Worker(Client(), Path(d))
            original = worker.journal.save({"id": "job-one", "payload_sha256": "a" * 64}, "lease", {"ok": True})
            self.assertEqual(worker.run_once(), {"state": "idle"})
            saved = worker.journal.lookup("job-one", "a" * 64)
            self.assertEqual(saved["state"], "readback_deferred")
            self.assertEqual(saved["result_sha256"], original["result_sha256"])
            self.assertEqual(saved["result_json"], original["result_json"])
            self.assertEqual(worker.journal.pending(), [])
            with worker.journal.connect() as db:
                db.execute("UPDATE receipts SET updated_at=? WHERE job_id=?", (int(time.time()) - 3601, "job-one"))
            self.assertEqual(worker.journal.pending()[0]["job_id"], "job-one")

    def test_retryable_server_error_does_not_defer_or_discard_receipt(self):
        class Client:
            calls = []
            def send(self, action, payload):
                self.calls.append(action)
                raise TransportError("transport_unavailable", 503)
        with tempfile.TemporaryDirectory() as d:
            client = Client()
            worker = Worker(client, Path(d))
            worker.journal.save(TEST_JOB, "lease", {"ok": True})
            with self.assertRaises(TransportError):
                worker.replay_receipts()
            saved = worker.journal.save(TEST_JOB, "renewed-lease", {"ok": True})
            self.assertEqual(saved["state"], "completion_attempted")
            self.assertEqual(saved["lease_token"], "lease")
            with self.assertRaises(TransportError):
                worker.replay_receipts()
            self.assertEqual(client.calls, ["complete", "status"])


if __name__ == "__main__":
    unittest.main()
