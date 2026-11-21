"""Offline tests only: no GitHub, WordPress, ChatGPT or paid provider calls."""
import hashlib
import json
import unittest

from msw_engine.github_chat_queue import _request_sha
from msw_engine.github_python_hub_once import (
    EXACT_GOAL, HealthReadError, run_exact_readonly_task,
)

TASK = "msw-staging-python-hub-20261114"
CONV = "msw-staging-conversation-20261114"


class FakeQueue:
    def __init__(self, task=None):
        self.task = task
        self.calls = []
        self.record = None
        self.ack_called = False

    def _read_queue(self):
        return {"channel": {"conversation_id": CONV}, "tasks": [] if self.task is None else [self.task]}, "a" * 40

    def handle(self, payload):
        self.calls.append(payload["operation"])
        operation = payload["operation"]
        if operation == "claim":
            return {"state": "claimed", "task_id": TASK,
                    "request_sha256": self.task["request_sha256"], "lease_token": "test-secret-lease-token"}
        if operation == "report":
            assert payload["lease_token"] == "test-secret-lease-token"
            assert hashlib.sha256(payload["result"].encode()).hexdigest() == payload["result_sha256"]
            self.record = payload
            return {"state": "reported", "result_sha256": payload["result_sha256"]}
        if operation == "status":
            return {"state": "reported", "result_sha256": self.record["result_sha256"]}
        if operation == "ack":
            self.ack_called = True
        raise AssertionError("not an expected call")


def task(**updates):
    result = {"state": "pending", "task_id": TASK, "executor": "hermes",
              "conversation_id": CONV, "kind": "review", "goal": EXACT_GOAL, "parent_id": None,
              "request_sha256": _request_sha(TASK, CONV, "review", EXACT_GOAL, None, False)}
    result.update(updates)
    return result


class PythonHubOneShotTests(unittest.TestCase):
    def run_task(self, queue, reader=None):
        if reader is None:
            reader = lambda: {"verified_public_http_200": True, "body_sha256": "a" * 64}
        return run_exact_readonly_task(queue, task_id=TASK, conversation_id=CONV, reader=reader)

    def test_empty_queue_does_not_claim(self):
        q = FakeQueue()
        self.assertEqual(self.run_task(q)["state"], "empty")
        self.assertEqual(q.calls, [])

    def test_other_tasks_never_claimed(self):
        for changes in ({"task_id": "different-id"}, {"goal": "Do something else"},
                        {"executor": "chatgpt_scheduled"}, {"state": "claimed"},
                        {"request_sha256": "0" * 64}, {"parent_id": "some-parent"}):
            with self.subTest(changes=changes):
                q = FakeQueue(task(**changes))
                self.assertEqual(self.run_task(q)["state"], "task_not_allowlisted")
                self.assertEqual(q.calls, [])

    def test_success_reports_digest_but_does_not_ack(self):
        q = FakeQueue(task())
        result = self.run_task(q)
        self.assertEqual(q.calls, ["claim", "report", "status"])
        self.assertEqual(result["state"], "reported_waiting_for_hermes_ack")
        self.assertEqual(result["outcome"], "completed")
        self.assertFalse(result["hermes_ack_verified"])
        self.assertFalse(q.ack_called)
        receipt = json.loads(q.record["result"])
        self.assertFalse(receipt["production_mutation_performed"])
        self.assertFalse(receipt["external_chat_verified"])
        self.assertEqual(receipt["source_sha256"], "a" * 64)

    def test_failure_reports_blocked_without_fake_success(self):
        q = FakeQueue(task())
        def broken():
            raise HealthReadError("hub_transport_unavailable")
        result = self.run_task(q, reader=broken)
        self.assertEqual(result["outcome"], "blocked")
        self.assertEqual(json.loads(q.record["result"])["code"], "hub_transport_unavailable")
        self.assertFalse(q.ack_called)

    def test_no_claim_when_conversation_mismatches(self):
        q = FakeQueue(task())
        self.assertEqual(run_exact_readonly_task(q, task_id=TASK, conversation_id="other-conversation")["state"], "conversation_mismatch")
        self.assertEqual(q.calls, [])

    def test_reader_does_not_run_until_exact_claim(self):
        q = FakeQueue(task(goal="unsafe"))
        called = []
        self.run_task(q, reader=lambda: called.append(True))
        self.assertEqual(called, [])


if __name__ == "__main__":
    unittest.main()
