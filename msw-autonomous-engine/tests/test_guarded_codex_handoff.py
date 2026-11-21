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
            "root_id": "root-00000001", "request_id": "request-00000001",
            "parent_id": "root-00000001",
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
                        parent_id="root-00000001")
        handle(dict(schema=SCHEMA, operation="submit", task_id=self.job["root_id"],
                    conversation_key="conv-00000001", kind="plan", goal="Parent",
                    executor="hermes", work_key="work-root-00000001"), self.root)
        parent_lease = handle(dict(schema=SCHEMA, operation="claim", executor="hermes",
                                   conversation_key="conv-00000001"), self.root)["lease_token"]
        parent_result = "parent complete"
        parent_digest = hashlib.sha256(parent_result.encode()).hexdigest()
        handle(dict(schema=SCHEMA, operation="report", task_id=self.job["root_id"],
                    lease_token=parent_lease, result=parent_result,
                    result_sha256=parent_digest, outcome="completed"), self.root)
        handle(dict(schema=SCHEMA, operation="ack", task_id=self.job["root_id"],
                    result_sha256=parent_digest), self.root)
        handle(dict(schema=SCHEMA, operation="submit", task_id=self.job["task_id"],
                    conversation_key="conv-00000001", kind="plan", goal="Test",
                    executor="hermes", work_key=self.job["work_key"],
                    parent_id=self.job["parent_id"]), self.root)
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

    def test_missing_identity_fields_hold_without_ack(self):
        for missing, reason in (("task_id", "receipt_task_mismatch"),
                                ("work_key", "receipt_work_mismatch")):
            with self.subTest(missing=missing):
                proof = receipt("ok", self.job["task_id"], self.job["work_key"])
                proof.pop(missing)
                proof["receipt_sha256"] = hashlib.sha256(
                    adapter.canonical({k: v for k, v in proof.items()
                                       if k != "receipt_sha256"}).encode()
                ).hexdigest()
                with self.assertRaisesRegex(adapter.ApplyHold, reason):
                    self.apply(proof, dispatch_enabled=True)
                state = handle(dict(schema=SCHEMA, operation="status",
                                    task_id=self.job["task_id"]), self.root)
                self.assertEqual(state["state"], "leased")

    def test_receipt_root_request_parent_binding(self):
        # Even a correctly hashed but misassociated proof must not ACK a job.
        for field in ("root_id", "request_id", "parent_id"):
            for new_value in (None, "other-" + field):
                with self.subTest(field=field, new_value=new_value):
                    proof = receipt("ok", self.job["task_id"], self.job["work_key"])
                    if new_value is None:
                        proof.pop(field)
                    else:
                        proof[field] = new_value
                    proof["receipt_sha256"] = hashlib.sha256(
                        adapter.canonical({k: v for k, v in proof.items()
                                           if k != "receipt_sha256"}).encode()
                    ).hexdigest()
                    with self.assertRaisesRegex(adapter.ApplyHold, "receipt_" + field + "_mismatch"):
                        self.apply(proof, dispatch_enabled=True)
                    state = handle(dict(schema=SCHEMA, operation="status",
                                        task_id=self.job["task_id"]), self.root)
                    self.assertEqual(state["state"], "leased")

    def test_direct_adapter_cannot_bypass_receipt_identity(self):
        for field in ("root_id", "request_id", "parent_id", "task_id", "work_key"):
            with self.subTest(field=field):
                proof = receipt("ok", self.job["task_id"], self.job["work_key"])
                proof[field] = "other-" + field
                proof["receipt_sha256"] = hashlib.sha256(
                    adapter.canonical({k: v for k, v in proof.items()
                                       if k != "receipt_sha256"}).encode()
                ).hexdigest()
                with self.assertRaisesRegex(adapter.ApplyHold, "receipt_" + field + "_mismatch"):
                    adapter.apply_once(self.root, self.job, self.lease, proof)
                state = handle(dict(schema=SCHEMA, operation="status",
                                    task_id=self.job["task_id"]), self.root)
                self.assertEqual(state["state"], "leased")

    def test_direct_adapter_rejects_noninteger_tools_before_write(self):
        # No wrapper bypass: a recomputed hash cannot launder False or 0.0
        # into the integer-zero attestation required for native apply.
        for unsafe in (False, 0.0, -0.0, True, "0", None):
            with self.subTest(tools=repr(unsafe)):
                proof = receipt("ok", self.job["task_id"], self.job["work_key"])
                proof["tools"] = unsafe
                proof["receipt_sha256"] = hashlib.sha256(
                    adapter.canonical({k: v for k, v in proof.items()
                                       if k != "receipt_sha256"}).encode()
                ).hexdigest()
                with self.assertRaisesRegex(adapter.ApplyHold, "receipt_unsafe"):
                    adapter.apply_once(self.root, self.job, self.lease, proof)
                state = handle(dict(schema=SCHEMA, operation="status",
                                    task_id=self.job["task_id"]), self.root)
                self.assertEqual(state["state"], "leased")
                self.assertIsNone(state.get("result_sha256"))

    def test_corrupted_receipt_digest_prevents_ack(self):
        proof = receipt("ok", self.job["task_id"], self.job["work_key"])
        proof["receipt_sha256"] = "0" * 64
        with self.assertRaisesRegex(adapter.ApplyHold, "receipt_digest_mismatch"):
            self.apply(proof, dispatch_enabled=True)
        state = handle(dict(schema=SCHEMA, operation="status",
                            task_id=self.job["task_id"]), self.root)
        self.assertEqual(state["state"], "leased")

    def test_corrupted_response_digest_prevents_ack(self):
        proof = receipt("ok", self.job["task_id"], self.job["work_key"])
        proof["response_sha256"] = "0" * 64
        proof["receipt_sha256"] = hashlib.sha256(
            adapter.canonical({k: v for k, v in proof.items()
                               if k != "receipt_sha256"}).encode()
        ).hexdigest()
        with self.assertRaisesRegex(adapter.ApplyHold, "result_digest_mismatch"):
            self.apply(proof, dispatch_enabled=True)
        state = handle(dict(schema=SCHEMA, operation="status",
                            task_id=self.job["task_id"]), self.root)
        self.assertEqual(state["state"], "leased")

    def test_wrong_work_key_rejected_without_ack(self):
        proof = receipt("ok", self.job["task_id"], "different-work")
        with self.assertRaisesRegex(adapter.ApplyHold, "receipt_work_mismatch"):
            self.apply(proof, dispatch_enabled=True)
        state = handle(dict(schema=SCHEMA, operation="status",
                            task_id=self.job["task_id"]), self.root)
        self.assertEqual(state["state"], "leased")

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
