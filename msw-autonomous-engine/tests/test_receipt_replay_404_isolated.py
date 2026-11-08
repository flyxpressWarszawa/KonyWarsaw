import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from msw_engine.client import TransportError
from msw_engine.runner import Worker

class ScriptedClient:
    def __init__(self, action_errors=None, state="completed"):
        self.actions = []
        self.action_errors = action_errors or {}
        self.digest = None
        self.state = state
    def send(self, action, payload):
        self.actions.append(action)
        error = self.action_errors.get(action)
        if error:
            raise TransportError("fixture", error)
        if action == "complete":
            self.digest = payload["result_sha256"]
            return {"stored": True, "result_sha256": self.digest}
        if action == "status":
            return {"state": self.state, "result_sha256": self.digest}
        if action == "claim":
            return {"state": "idle"}
        raise AssertionError(action)

class ReceiptRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = patch.dict(os.environ, {"MSW_RUNTIME_MODE": "render-free", "MSW_AUTONOMOUS_AGENT": "0"})
        self.env.start()
        self.addCleanup(self.env.stop)
    def worker(self, client):
        worker = Worker(client, Path(self.tmp.name))
        before = worker.journal.save({"id": "fixture-job", "payload_sha256": "a"*64}, "lease", {"ok": True, "data": "safe-fixture"})
        return worker, before
    def test_missing_status_preserves_result_and_continues_claiming(self):
        client = ScriptedClient({"complete": 409, "status": 404})
        worker, before = self.worker(client)
        self.assertEqual(worker.run_once(), {"state": "idle"})
        after = worker.journal.lookup("fixture-job", "a"*64)
        self.assertEqual(after["state"], "pending_deferred")
        self.assertEqual(after["result_sha256"], before["result_sha256"])
        self.assertEqual(after["result_json"], before["result_json"])
        self.assertEqual(client.actions, ["complete", "status", "claim"])
        self.assertEqual(worker.journal.pending(), [])
        with worker.journal.connect() as db:
            db.execute("UPDATE receipts SET updated_at=? WHERE job_id=?", (int(time.time())-3601,"fixture-job"))
        self.assertEqual(worker.journal.pending()[0]["job_id"], "fixture-job")
    def test_404_completing_receipt_preserves_result(self):
        client = ScriptedClient({"complete": 404})
        worker, before = self.worker(client)
        self.assertEqual(worker.run_once(), {"state": "idle"})
        after = worker.journal.lookup("fixture-job", "a"*64)
        self.assertEqual(after["state"], "pending_deferred")
        self.assertEqual(after["result_sha256"], before["result_sha256"])
        self.assertEqual(client.actions, ["complete", "claim"])
    def test_unavailable_status_is_not_deferred(self):
        client = ScriptedClient({"complete": 409, "status": 503})
        worker, before = self.worker(client)
        with self.assertRaises(TransportError) as error:
            worker.run_once()
        self.assertEqual(error.exception.status, 503)
        after = worker.journal.lookup("fixture-job", "a"*64)
        self.assertEqual(after["state"], "pending")
        self.assertEqual(after["result_sha256"], before["result_sha256"])
        self.assertNotIn("claim", client.actions)
    def test_good_ack_still_verifies_both_responses(self):
        client = ScriptedClient()
        worker, _ = self.worker(client)
        self.assertEqual(worker.run_once(), {"state": "idle"})
        self.assertEqual(worker.journal.lookup("fixture-job", "a"*64)["state"], "acknowledged")
        self.assertEqual(client.actions, ["complete", "status", "claim"])
    def test_claim_404_is_not_misreported_as_success(self):
        client = ScriptedClient({"complete": 404, "claim": 404})
        worker, before = self.worker(client)
        with self.assertRaises(TransportError) as error:
            worker.run_once()
        self.assertEqual(error.exception.status, 404)
        after = worker.journal.lookup("fixture-job", "a"*64)
        self.assertEqual(after["state"], "pending_deferred")
        self.assertEqual(after["result_sha256"], before["result_sha256"])

if __name__ == "__main__":
    unittest.main()
