"""Fail-closed handoff tests: no production dispatch, no duplicate ACK writes."""
import hashlib
import importlib
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from msw_engine.hermes_chat_bridge import SCHEMA, handle

entry = importlib.import_module("integrations.hermes_codex_native_v1.guarded_receipt_entry")
adapter = importlib.import_module("integrations.hermes_codex_native_v1.sqlite_apply_adapter")


def receipt(text, task_id, work_key):
    data = {"status": "VERIFIED", "independent_readback": True, "tools": 0,
            "text": text, "task_id": task_id, "work_key": work_key,
            "response_sha256": hashlib.sha256(text.encode()).hexdigest()}
    data["receipt_sha256"] = hashlib.sha256(adapter.canonical(data).encode()).hexdigest()
    return data


class GuardedHandoff(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.job = dict(root_id="root-00000001", task_id="task-00000001",
                        request_id="request-00000001", work_key="work-00000001",
                        parent_id="parent-00000001")
        handle(dict(schema=SCHEMA, operation="submit", task_id=self.job["task_id"],
                    conversation_key="conv-00000001", kind="plan", goal="Test",
                    executor="hermes", work_key=self.job["work_key"]), self.root)
        self.lease = handle(dict(schema=SCHEMA, operation="claim", executor="hermes",
                                 conversation_key="conv-00000001"), self.root)["lease_token"]

    def apply(self, proof, **kw):
        return entry.apply_codex_result(self.root, self.job, self.lease, proof, **kw)

    def test_default_is_hold_without_write(self):
        with self.assertRaisesRegex(adapter.ApplyHold, "dispatch_disabled"):
            self.apply(receipt("ok", self.job["task_id"], self.job["work_key"]))
        state = handle(dict(schema=SCHEMA, operation="status", task_id=self.job["task_id"]), self.root)
        self.assertEqual(state["state"], "leased")

    def test_wrong_task_identity_rejected(self):
        with self.assertRaisesRegex(adapter.ApplyHold, "receipt_task_mismatch"):
            self.apply(receipt("ok", "different-task", self.job["work_key"]), dispatch_enabled=True)

    def test_ack_and_repeat_are_idempotent(self):
        proof = receipt("ok", self.job["task_id"], self.job["work_key"])
        first = self.apply(proof, dispatch_enabled=True)
        second = self.apply(proof, dispatch_enabled=True)
        self.assertEqual(first["status"], "APPLIED_VERIFIED")
        self.assertEqual(first["result_sha256"], second["result_sha256"])
        self.assertEqual(second["native_ack"]["state"], "acknowledged")

    def test_conflicting_result_rejected(self):
        self.apply(receipt("ok", self.job["task_id"], self.job["work_key"]), dispatch_enabled=True)
        with self.assertRaises(adapter.ApplyHold):
            self.apply(receipt("different", self.job["task_id"], self.job["work_key"]),
                       dispatch_enabled=True)


if __name__ == "__main__":
    unittest.main()
