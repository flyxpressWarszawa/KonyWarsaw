import hashlib
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from msw_engine.client import TransportError
from msw_engine.runner import Journal, Worker
from msw_engine.suite import analyze
from msw_engine.tasks import TaskError, runtime_catalog


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
        self.assertEqual(catalog["worker_version"], "1.2.0")
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
                return {"state": "completed", "result_sha256": receipt["result_sha256"]}
        with tempfile.TemporaryDirectory() as d:
            worker = Worker(Client(), Path(d))
            receipt = worker.journal.save({"id": "job-one", "payload_sha256": "a" * 64}, "lease", {"ok": True})
            self.assertEqual(worker.replay_receipts(), 1)
            self.assertEqual(worker.journal.pending(), [])
            self.assertEqual(worker.journal.lookup("job-one", "a" * 64)["state"], "acknowledged")


if __name__ == "__main__":
    unittest.main()
