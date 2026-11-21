"""Synthetic, offline storage checks; not a live ChatGPT or Hermes test."""
import copy
import hashlib
import unittest

from msw_engine.github_chat_receipt_audit import audit_snapshot, ReceiptAuditError


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def fixture():
    body = "Syntetyczny wynik — bez rzeczywistego wywołania modelu."
    receipt = {
        "schema": "msw.hermes.chat.receipt.v1",
        "task_id": "msw-test-task-0001", "work_key": "msw-test-work-0001",
        "executor": "chatgpt_scheduled", "parent_id": None, "depth": 1,
        "conversation_id": "msw-test-conversation", "request_sha256": "a" * 64,
        "result": body, "result_sha256": sha(body), "outcome": "completed",
        "hermes_ack": {"verified": True, "at_utc": "2026-11-14T19:00:00Z"},
    }
    ack = {key: receipt[key] for key in ("task_id", "work_key", "executor", "parent_id",
                                         "depth", "request_sha256", "result_sha256", "outcome")}
    ack.update(receipt_path="knowledge/hermes-chat-loop/receipts/msw-test-task-0001.json",
               acknowledged_at_utc=receipt["hermes_ack"]["at_utc"])
    doc = {"schema": "msw.hermes.chat.queue.v1", "protocol_version": 1,
           "revision": 4, "channel": {"conversation_id": receipt["conversation_id"]},
           "tasks": [], "last_ack": ack}
    return doc, receipt


class FakeReader:
    def __init__(self, doc, receipt):
        self.doc, self.receipt = copy.deepcopy(doc), copy.deepcopy(receipt)
        self.reads = 0
        self.archive_reads = []
        self.change_on_second_read = False

    def _read_queue(self):
        self.reads += 1
        doc = copy.deepcopy(self.doc)
        if self.change_on_second_read and self.reads == 2:
            doc["revision"] += 1
        return doc, "b" * 40

    def _archived_receipt(self, task_id):
        self.archive_reads.append(task_id)
        return copy.deepcopy(self.receipt)

    def handle(self, payload):
        raise AssertionError("audit must not invoke a dispatch/write operation")


class ReceiptAuditTests(unittest.TestCase):
    def setUp(self):
        self.doc, self.receipt = fixture()

    def reader(self):
        return FakeReader(self.doc, self.receipt)

    def test_consistent_records_are_storage_evidence_only(self):
        reader = self.reader()
        result = audit_snapshot(reader)
        self.assertTrue(result["queue_ack_committed"])
        self.assertTrue(result["receipt_integrity_verified"])
        self.assertFalse(result["hermes_identity_verified"])
        self.assertFalse(result["live_external_chat_connected"])
        self.assertFalse(result["new_chat_verified"])
        self.assertFalse(result["existing_chat_verified"])
        self.assertEqual(reader.reads, 2)

    def test_orphan_archive_cannot_prove_ack(self):
        self.doc["last_ack"] = None
        reader = self.reader()
        self.assertFalse(audit_snapshot(reader)["queue_ack_committed"])
        self.assertEqual(reader.archive_reads, [])

    def test_reported_task_with_prepared_archive_is_not_ack(self):
        self.doc["last_ack"] = None
        self.doc["tasks"] = [{**self.receipt, "state": "reported"}]
        result = audit_snapshot(self.reader())
        self.assertEqual(result["counts"], {"reported": 1})
        self.assertFalse(result["queue_ack_committed"])

    def test_missing_archive_is_rejected(self):
        self.receipt = None
        with self.assertRaisesRegex(ReceiptAuditError, "ack_receipt_missing"):
            audit_snapshot(self.reader())

    def test_tampered_answer_is_rejected(self):
        self.receipt["result"] = "changed body"
        with self.assertRaisesRegex(ReceiptAuditError, "result_integrity"):
            audit_snapshot(self.reader())

    def test_wrong_conversation_is_rejected(self):
        self.receipt["conversation_id"] = "another-conversation"
        with self.assertRaisesRegex(ReceiptAuditError, "conversation_mismatch"):
            audit_snapshot(self.reader())

    def test_wrong_request_is_rejected(self):
        self.receipt["request_sha256"] = "f" * 64
        with self.assertRaisesRegex(ReceiptAuditError, "receipt_mismatch"):
            audit_snapshot(self.reader())

    def test_wrong_result_digest_is_rejected(self):
        self.doc["last_ack"]["result_sha256"] = "f" * 64
        with self.assertRaisesRegex(ReceiptAuditError, "receipt_mismatch"):
            audit_snapshot(self.reader())

    def test_wrong_path_is_rejected(self):
        self.doc["last_ack"]["receipt_path"] = "elsewhere.json"
        with self.assertRaisesRegex(ReceiptAuditError, "path_mismatch"):
            audit_snapshot(self.reader())

    def test_changed_snapshot_is_rejected_even_with_same_blob_label(self):
        reader = self.reader()
        reader.change_on_second_read = True
        with self.assertRaisesRegex(ReceiptAuditError, "snapshot_changed"):
            audit_snapshot(reader)

    def test_boolean_revision_is_rejected(self):
        self.doc["revision"] = True
        with self.assertRaisesRegex(ReceiptAuditError, "queue_invalid"):
            audit_snapshot(self.reader())

    def test_ack_for_still_active_task_is_rejected(self):
        self.doc["tasks"] = [{**self.receipt, "state": "reported"}]
        with self.assertRaisesRegex(ReceiptAuditError, "ack_still_active"):
            audit_snapshot(self.reader())

    def test_unmarked_receipt_is_rejected(self):
        self.receipt["hermes_ack"]["verified"] = False
        with self.assertRaisesRegex(ReceiptAuditError, "receipt_not_marked"):
            audit_snapshot(self.reader())

    def test_ack_timestamp_must_match(self):
        self.receipt["hermes_ack"]["at_utc"] = "different"
        with self.assertRaisesRegex(ReceiptAuditError, "ack_time_mismatch"):
            audit_snapshot(self.reader())

    def test_private_result_and_ids_not_in_summary(self):
        result = str(audit_snapshot(self.reader()))
        for private in (self.receipt["result"], self.receipt["task_id"],
                        self.receipt["work_key"], self.receipt["conversation_id"]):
            self.assertNotIn(private, result)

    def test_invalid_reported_result_is_rejected(self):
        self.doc["last_ack"] = None
        self.doc["tasks"] = [{**self.receipt, "state": "reported", "result": "tampered"}]
        with self.assertRaisesRegex(ReceiptAuditError, "result_integrity"):
            audit_snapshot(self.reader())

    def test_malformed_field_types_fail_with_bounded_codes(self):
        cases = [
            ("executor", []), ("outcome", {}), ("depth", True),
        ]
        for key, value in cases:
            with self.subTest(key=key):
                doc, receipt = fixture()
                doc["last_ack"][key] = value
                receipt[key] = value
                with self.assertRaises(ReceiptAuditError):
                    audit_snapshot(FakeReader(doc, receipt))


if __name__ == "__main__":
    unittest.main()
