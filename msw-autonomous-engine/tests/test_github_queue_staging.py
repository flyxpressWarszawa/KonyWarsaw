import copy
import unittest
from msw_engine.github_queue_staging import safe_snapshot, verified_snapshot, optional_one_shot
from msw_engine.github_chat_receipt_audit import ReceiptAuditError
from msw_engine.tasks import TaskError


class FakeQueue:
    def __init__(self, result):
        self.result = result
        self.payloads = []
        self.reads = 0
        self.doc = {
            "schema": "msw.hermes.chat.queue.v1", "protocol_version": 1,
            "revision": 0, "channel": {"conversation_id": "msw-test-conversation"},
            "tasks": [], "last_ack": None,
        }

    def handle(self, payload):
        self.payloads.append(payload)
        return self.result

    def _read_queue(self):
        self.reads += 1
        return copy.deepcopy(self.doc), "a" * 40

    def _archived_receipt(self, task_id):
        raise AssertionError("empty queue must not fetch an archive")


class ReadOnlyStagingProbeTests(unittest.TestCase):
    def test_probe_only_calls_status_and_returns_bounded_counts(self):
        queue = FakeQueue({
            "github_queue_connected": True,
            "live_external_chat_connected": False,
            "counts": {"pending": 1, "claimed": 0, "secret": "must-not-escape", "unknown": 99},
            "result": "private payload must-not-escape",
        })
        snapshot = safe_snapshot(queue)
        self.assertEqual(queue.payloads, [{"schema": "msw.hermes.chat-loop.v1", "operation": "status"}])
        self.assertEqual(snapshot["state"], "read_only_connected")
        self.assertEqual(snapshot["counts"], {"pending": 1, "claimed": 0})
        self.assertNotIn("secret", str(snapshot))
        self.assertNotIn("private payload", str(snapshot))

    def test_probe_never_claims_or_writes(self):
        queue = FakeQueue({"github_queue_connected": True, "counts": {}})
        safe_snapshot(queue)
        self.assertEqual(queue.payloads[0]["operation"], "status")
        self.assertNotIn(queue.payloads[0]["operation"], {"submit", "claim", "report", "ack"})

    def test_verified_probe_uses_checked_counts_without_fake_ack(self):
        queue = FakeQueue({"github_queue_connected": True, "counts": {"acknowledged": 1}})
        snapshot = verified_snapshot(queue)
        self.assertEqual(snapshot["counts"], {})
        self.assertFalse(snapshot["receipt_audit"]["queue_ack_committed"])
        self.assertFalse(snapshot["receipt_audit"]["hermes_identity_verified"])
        self.assertEqual(queue.reads, 2)
        self.assertEqual(len(queue.payloads), 1)
        self.assertEqual(queue.payloads[0]["operation"], "status")

    def test_disconnected_summary_is_rejected(self):
        queue = FakeQueue({"github_queue_connected": False, "counts": {}})
        with self.assertRaisesRegex(TaskError, "staging_not_connected"):
            verified_snapshot(queue)
        self.assertEqual(queue.reads, 0)

    def test_receipt_audit_failure_is_not_hidden(self):
        queue = FakeQueue({"github_queue_connected": True, "counts": {}})
        queue.doc["revision"] = True
        with self.assertRaisesRegex(ReceiptAuditError, "audit_queue_invalid"):
            verified_snapshot(queue)


    def test_one_shot_opt_in_disabled_never_claims(self):
        queue = FakeQueue({"github_queue_connected": True, "counts": {}})
        self.assertEqual(optional_one_shot(queue, environment={})["state"], "disabled")
        self.assertEqual(queue.payloads, [])

    def test_one_shot_policy_prohibits_production_mutation(self):
        queue = FakeQueue({"github_queue_connected": True, "counts": {}})
        policy = {"MSW_HERMES_STAGING_PYTHON_ONE_SHOT": "1",
                  "MSW_HERMES_CHAT_BACKEND": "github",
                  "MSW_HERMES_CHAT_LOOP_ENABLED": "1",
                  "MSW_HERMES_PRODUCTION_MUTATION_ALLOWED": "1"}
        self.assertEqual(optional_one_shot(queue, environment=policy)["state"], "policy_blocked")
        self.assertEqual(queue.payloads, [])

    def test_one_shot_bad_task_binding_never_claims(self):
        queue = FakeQueue({"github_queue_connected": True, "counts": {}})
        policy = {"MSW_HERMES_STAGING_PYTHON_ONE_SHOT": "1",
                  "MSW_HERMES_CHAT_BACKEND": "github",
                  "MSW_HERMES_CHAT_LOOP_ENABLED": "1"}
        self.assertEqual(optional_one_shot(queue, environment=policy)["state"], "invalid_staging_binding")
        self.assertEqual(queue.payloads, [])


if __name__ == "__main__":
    unittest.main()
