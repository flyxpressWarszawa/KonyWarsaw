import base64
import hashlib
import json
import unittest

from msw_engine.github_chat_queue import (
    GitHubChatQueue,
    QUEUE_SCHEMA,
    SCHEMA,
)


CONVERSATION = "6af7e699-24ec-83ed-b25a-3d6969794a84"


class FakeGitHub:
    def __init__(self):
        self.files = {
            "knowledge/hermes-chat-loop/github-queue.json": {
                "schema": QUEUE_SCHEMA,
                "protocol_version": 1,
                "channel": {
                    "provider": "GitHub private repository",
                    "repository": "flyxpressWarszawa/signal-harbor-companion",
                    "branch": "main",
                    "conversation_surface": "ordinary_personal_chat",
                    "conversation_id": CONVERSATION,
                },
                "concurrency": {},
                "state_transitions": ["pending", "claimed", "reported", "acknowledged", "uncertain"],
                "task_fields": {},
                "revision": 0,
                "updated_at_utc": "2026-11-14T00:00:00Z",
                "tasks": [],
            }
        }
        self.calls = []

    @staticmethod
    def _sha(raw):
        header = b"blob " + str(len(raw)).encode("ascii") + b"\0"
        return hashlib.sha1(header + raw).hexdigest()

    def request(self, method, path, body):
        self.calls.append((method, path))
        existing = self.files.get(path)
        if method == "GET":
            if existing is None:
                return 404, {}
            raw = (json.dumps(existing, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
            return 200, {
                "type": "file",
                "sha": self._sha(raw),
                "content": base64.b64encode(raw).decode("ascii"),
            }
        if method == "PUT":
            if existing is None:
                if "sha" in body:
                    raise AssertionError("create unexpectedly used a prior SHA")
            else:
                old_raw = (json.dumps(existing, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
                if body.get("sha") != self._sha(old_raw):
                    from msw_engine.github_chat_queue import _Conflict
                    raise _Conflict()
            raw = base64.b64decode(body["content"])
            updated = json.loads(raw.decode("utf-8"))
            self.files[path] = updated
            return (200 if existing is not None else 201), {
                "content": {"sha": self._sha(raw)},
                "commit": {"sha": "test-commit"},
            }
        raise AssertionError("unsupported method")


class GitHubQueueTests(unittest.TestCase):
    def setUp(self):
        self.remote = FakeGitHub()
        self.queue = GitHubChatQueue(self.remote.request)
        self.task_id = "msw-owner-task-0001"
        self.work_key = "msw-owner-work-0001"

    def submit(self, **changes):
        payload = {
            "schema": SCHEMA,
            "operation": "submit",
            "task_id": self.task_id,
            "conversation_key": CONVERSATION,
            "kind": "plan",
            "goal": "Przygotuj krótki plan kolejnych prac.",
            "executor": "chatgpt_scheduled",
            "work_key": self.work_key,
        }
        payload.update(changes)
        return self.queue.handle(payload)

    def claim(self, executor="chatgpt_scheduled"):
        return self.queue.handle({
            "schema": SCHEMA,
            "operation": "claim",
            "executor": executor,
            "conversation_key": CONVERSATION,
        })

    def report(self, lease, result="Plan gotowy; następny krok to weryfikacja.", outcome="completed"):
        return self.queue.handle({
            "schema": SCHEMA,
            "operation": "report",
            "task_id": lease["task_id"],
            "lease_token": lease["lease_token"],
            "result": result,
            "result_sha256": hashlib.sha256(result.encode()).hexdigest(),
            "outcome": outcome,
        })

    def acknowledge(self, task_id=None, result="Plan gotowy; następny krok to weryfikacja."):
        return self.queue.handle({
            "schema": SCHEMA,
            "operation": "ack",
            "task_id": task_id or self.task_id,
            "result_sha256": hashlib.sha256(result.encode()).hexdigest(),
        })

    def test_claim_report_ack_is_single_assignment_and_archive_readback(self):
        self.submit()
        lease = self.claim()
        self.assertEqual(lease["state"], "claimed")
        self.assertEqual(self.claim()["state"], "empty")
        report = self.report(lease)
        self.assertEqual(report["state"], "reported")
        ack = self.acknowledge()
        self.assertTrue(ack["hermes_ack_verified"])
        self.assertEqual(ack["state"], "acknowledged")
        self.assertEqual(self.remote.files["knowledge/hermes-chat-loop/github-queue.json"]["tasks"], [])
        receipt_path = f"knowledge/hermes-chat-loop/receipts/{self.task_id}.json"
        self.assertEqual(self.remote.files[receipt_path]["result_sha256"], ack["result_sha256"])
        status = self.queue.handle({"schema": SCHEMA, "operation": "status", "task_id": self.task_id})
        self.assertEqual(status["state"], "acknowledged")
        self.assertTrue(status["github_queue_connected"])

    def test_follow_up_requires_parent_ack_and_uses_stable_work_key(self):
        self.submit()
        lease = self.claim()
        self.report(lease)
        with self.assertRaisesRegex(Exception, "parent_not_acknowledged"):
            self.submit(
                task_id="msw-owner-task-0002",
                work_key="msw-owner-work-0002",
                parent_id=self.task_id,
                start_next=True,
                goal="Sprawdź wynik i zaproponuj następną pracę.",
            )
        self.acknowledge()
        child = self.submit(
            task_id="msw-owner-task-0002",
            work_key="msw-owner-work-0002",
            parent_id=self.task_id,
            start_next=True,
            goal="Sprawdź wynik i zaproponuj następną pracę.",
        )
        self.assertEqual(child["task_id"], "msw-owner-task-0002")
        self.assertEqual(child["state"], "pending")

    def test_same_work_key_retry_after_ack_returns_original_task(self):
        self.submit()
        lease = self.claim()
        self.report(lease)
        self.acknowledge()
        replay = self.submit(task_id="msw-owner-task-0002")
        self.assertEqual(replay["task_id"], self.task_id)
        self.assertEqual(replay["state"], "acknowledged")

    def test_wrong_conversation_and_bad_digest_fail_closed(self):
        with self.assertRaisesRegex(Exception, "conversation_binding"):
            self.submit(conversation_key="another-chat-thread")
        self.submit()
        lease = self.claim()
        with self.assertRaisesRegex(Exception, "report_content"):
            self.queue.handle({
                "schema": SCHEMA, "operation": "report", "task_id": self.task_id,
                "lease_token": lease["lease_token"], "result": "changed",
                "result_sha256": "0" * 64, "outcome": "completed",
            })

    def test_private_writes_are_limited_to_queue_and_generated_subpaths(self):
        from msw_engine.github_chat_queue import _allowed_path
        self.assertTrue(_allowed_path("knowledge/hermes-chat-loop/github-queue.json"))
        self.assertTrue(_allowed_path("knowledge/hermes-chat-loop/receipts/msw-owner-task-0001.json"))
        self.assertFalse(_allowed_path("README.md"))
        self.assertFalse(_allowed_path("knowledge/hermes-chat-loop/../../README.md"))


if __name__ == "__main__":
    unittest.main()
