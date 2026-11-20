"""Offline tests for SQLite native Codex apply adapter."""
import hashlib
import importlib.util
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from msw_engine.hermes_chat_bridge import SCHEMA, handle

ADAPTER = Path(__file__).resolve().parents[1] / "integrations" / "hermes_codex_native_v1" / "sqlite_apply_adapter.py"
spec = importlib.util.spec_from_file_location("sqlite_apply_adapter", ADAPTER)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def receipt(text):
    obj = {"status": "VERIFIED", "independent_readback": True, "tools": 0,
           "text": text, "response_sha256": hashlib.sha256(text.encode()).hexdigest()}
    obj["receipt_sha256"] = hashlib.sha256(module.canonical(obj).encode()).hexdigest()
    return obj


class SQLiteApplyAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.job = {"root_id": "root-00000001", "task_id": "task-00000001",
                    "request_id": "request-00000001", "work_key": "work-00000001",
                    "parent_id": "parent-00000001"}
        handle({"schema": SCHEMA, "operation": "submit",
                "task_id": self.job["task_id"], "conversation_key": "conv-00000001",
                "kind": "plan", "goal": "Offline test", "executor": "hermes",
                "work_key": self.job["work_key"]}, self.directory)
        self.lease = handle({"schema": SCHEMA, "operation": "claim",
                             "executor": "hermes", "conversation_key": "conv-00000001"},
                            self.directory)["lease_token"]

    def test_full_native_ack_readback(self):
        result = module.apply_once(self.directory, self.job, self.lease, receipt("ok"))
        self.assertEqual(result["status"], "APPLIED_VERIFIED")
        self.assertTrue(result["readback_verified"])
        self.assertEqual(result["native_ack"]["state"], "acknowledged")

    def test_wrong_work_key_does_not_write(self):
        with self.assertRaisesRegex(module.ApplyHold, "work_identity_mismatch"):
            module.apply_once(self.directory, dict(self.job, work_key="wrong-00000001"),
                              self.lease, receipt("ok"))
        status = handle({"schema": SCHEMA, "operation": "status",
                         "task_id": self.job["task_id"]}, self.directory)
        self.assertEqual(status["state"], "leased")

    def test_invalid_digest_does_not_write(self):
        bad = receipt("ok")
        bad["response_sha256"] = "0" * 64
        with self.assertRaises(module.ApplyHold):
            module.apply_once(self.directory, self.job, self.lease, bad)
        status = handle({"schema": SCHEMA, "operation": "status",
                         "task_id": self.job["task_id"]}, self.directory)
        self.assertEqual(status["state"], "leased")


if __name__ == "__main__":
    unittest.main()
