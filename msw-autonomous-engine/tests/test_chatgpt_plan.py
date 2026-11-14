"""No-network checks for opt-in ChatGPT plan route."""
import io
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch

from msw_engine import chatgpt_plan
from msw_engine.tasks import TaskError


class PlanRouteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.profile = self.root / "oauth.json"
        self.profile.write_text(json.dumps({
            "access_token": "test-oauth-token-for-offline-only",
            "scope": "openid resource.invoke chatgpt.tokens.use.direct",
            "expires_at": time.time() + 1800,
        }))
        os.chmod(self.profile, 0o600)
        self.env = patch.dict(os.environ, {
            "MSW_CHATGPT_PLAN_ENABLED": "1",
            "MSW_CHATGPT_PLAN_MODEL": "account-selected-model",
            "MSW_CHATGPT_PLAN_OAUTH_FILE": str(self.profile),
        })
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_disabled_never_advertises_generate(self):
        with patch.dict(os.environ, {"MSW_CHATGPT_PLAN_ENABLED": "0"}):
            self.assertNotIn("chatgpt.plan.generate", chatgpt_plan.available_handlers())
            with self.assertRaises(TaskError):
                chatgpt_plan.generate({"text": "hi", "request_id": "my-test-123"}, self.root)

    def test_missing_scopes_fails_closed(self):
        self.profile.write_text(json.dumps({"access_token": "test-oauth-token-for-offline-only",
                                           "scope": "openid profile",
                                           "expires_at": time.time() + 1800}))
        with self.assertRaisesRegex(TaskError, "oauth_scopes_missing"):
            chatgpt_plan.generate({"text": "hi", "request_id": "my-test-123"}, self.root)

    def test_stream_and_receipt_no_repeat(self):
        events = (
            'data: {"type":"response.output_text.delta","delta":"Dzień "}\n\n'
            'data: {"type":"response.output_text.delta","delta":"dobry"}\n\n'
            'data: {"type":"response.completed"}\n\n'
            'data: [DONE]\n\n'
        ).encode("utf-8")
        requests = []

        class FakeOpener:
            def open(self, request, timeout):
                requests.append(request)
                return io.BytesIO(events)

        data = {"text": "odpowiedz krótko", "request_id": "msw-check-20261114-01"}
        with patch.object(chatgpt_plan, "build_opener", return_value=FakeOpener()):
            answer = chatgpt_plan.generate(data, self.root)
            second = chatgpt_plan.generate(data, self.root)
        self.assertEqual(answer["text"], "Dzień dobry")
        self.assertEqual(second, answer)
        self.assertEqual(len(requests), 1)
        payload = json.loads(requests[0].data)
        self.assertEqual(set(payload), {"model", "input", "store", "stream"})
        self.assertEqual(payload["input"], [{"role": "user", "content": "odpowiedz krótko"}])
        self.assertFalse(payload["store"])
        self.assertTrue(payload["stream"])
        self.assertEqual(requests[0].full_url, chatgpt_plan.ENDPOINT)

    def test_incomplete_stream_never_receipted_as_success(self):
        class FakeOpener:
            def open(self, request, timeout):
                return io.BytesIO(b'data: {"type":"response.output_text.delta","delta":"hi"}\n\n')
        data = {"text": "hi", "request_id": "msw-check-20261114-02"}
        with patch.object(chatgpt_plan, "build_opener", return_value=FakeOpener()):
            with self.assertRaises(TaskError):
                chatgpt_plan.generate(data, self.root)
            with self.assertRaisesRegex(TaskError, "prior_request_unknown"):
                chatgpt_plan.generate(data, self.root)

    def test_world_readable_token_rejected(self):
        os.chmod(self.profile, 0o644)
        with self.assertRaisesRegex(TaskError, "oauth_file_not_private"):
            chatgpt_plan.generate({"text": "hi", "request_id": "my-test-123"}, self.root)


if __name__ == "__main__":
    unittest.main()
