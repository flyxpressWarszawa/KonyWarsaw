"""Synthetic Qwen streaming/Responses API contract and paid-call safety tests."""
from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from msw_engine.aliyun_protocols import BASE, generate
from msw_engine.tasks import TaskError

ENV = {"MSW_ALIYUN_API_KEY": "mock-key-not-real",
       "MSW_ALIYUN_MODEL": "qwen3.8-max",
       "MSW_ALIYUN_THINKING_BUDGET": "256"}
DATA = {"text": "Who are you?", "max_tokens": 128, "request_id": "msw-test-qwen38-001"}


class FakeOpen:
    def __init__(self, response=None, error=None):
        self.response, self.error, self.calls = response, error, []

    def open(self, request, timeout):
        body = json.loads(request.data)
        self.calls.append((request.full_url, body))
        assert request.get_header("Authorization") == "Bearer mock-key-not-real"
        assert timeout == 90
        if self.error is not None:
            raise self.error
        return io.BytesIO(self.response)


def sse(*events):
    return b"".join(b"data: " + json.dumps(e).encode("utf-8") + b"\n\n" for e in events) + b"data: [DONE]\n\n"


class TestQwenProtocols(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)

    def test_stream_thinking_is_never_saved_as_final_text(self):
        raw = sse(
            {"choices": [{"delta": {"reasoning_content": "SECRET_REASONS"}, "finish_reason": None}]},
            {"choices": [{"delta": {"content": "Test "}, "finish_reason": None}]},
            {"choices": [{"delta": {"content": "OK"}, "finish_reason": "stop"}]},
            {"choices": []},
        )
        endpoint = FakeOpen(raw)
        with patch.dict(os.environ, ENV), patch(
                "msw_engine.aliyun_protocols.build_opener", return_value=endpoint):
            a = generate(DATA, self.root, protocol="chat_stream")
            b = generate(DATA, self.root, protocol="chat_stream")
        self.assertEqual(a, b)
        self.assertEqual(a["text"], "Test OK")
        self.assertEqual(a["provider"], "aliyun_model_studio")
        self.assertEqual(len(endpoint.calls), 1)
        self.assertNotIn("SECRET_REASONS", str(a))
        self.assertNotIn("SECRET_REASONS", (self.root / "aliyun-direct" / "requests.sqlite3").read_bytes().decode("latin-1"))
        path, body = endpoint.calls[0]
        self.assertEqual(path, BASE + "/chat/completions")
        self.assertTrue(body["stream"])
        self.assertTrue(body["enable_thinking"])
        self.assertEqual(body["thinking_budget"], 256)
        self.assertEqual(body["max_completion_tokens"], 384)
        self.assertTrue(body["stream_options"]["include_usage"])

    def test_responses_api_extracts_only_final_message(self):
        upstream = {"status": "completed", "model": "qwen3.8-max", "output": [
            {"type": "reasoning", "summary": [{"type": "summary_text", "text": "SECRET_REASONING"}]},
            {"type": "message", "status": "completed", "content": [
                {"type": "output_text", "text": "9.9 is larger than 9.11."}]}]}
        endpoint = FakeOpen(json.dumps(upstream).encode())
        payload = {**DATA, "request_id": "msw-test-responses-001"}
        with patch.dict(os.environ, ENV), patch(
                "msw_engine.aliyun_protocols.build_opener", return_value=endpoint):
            reply = generate(payload, self.root, protocol="responses")
        self.assertEqual(reply["text"], "9.9 is larger than 9.11.")
        self.assertNotIn("SECRET_REASONING", str(reply))
        uri, body = endpoint.calls[0]
        self.assertEqual(uri, BASE + "/responses")
        self.assertTrue(body["enable_thinking"])
        self.assertFalse(body["store"])
        self.assertEqual(body["input"], DATA["text"])
        self.assertEqual(body["max_output_tokens"], 384)

    def test_fails_closed_without_api_key(self):
        with patch.dict(os.environ, {**ENV, "MSW_ALIYUN_API_KEY": ""}):
            with self.assertRaisesRegex(TaskError, "aliyun_key_missing"):
                generate(DATA, self.root, protocol="chat_stream")
        self.assertFalse((self.root / "aliyun-direct").exists())

    def test_missing_done_is_uncertain_and_no_paid_replay(self):
        incomplete = b'data: {"choices":[{"delta":{"content":"partial"},"finish_reason":"stop"}]}\n'
        endpoint = FakeOpen(incomplete)
        with patch.dict(os.environ, ENV), patch(
                "msw_engine.aliyun_protocols.build_opener", return_value=endpoint):
            with self.assertRaisesRegex(TaskError, "aliyun_protocol_transport"):
                generate(DATA, self.root, protocol="chat_stream")
            with self.assertRaisesRegex(TaskError, "outcome_unknown"):
                generate(DATA, self.root, protocol="chat_stream")
        self.assertEqual(len(endpoint.calls), 1)

    def test_unpurchased_is_terminal_403_no_replay(self):
        err = HTTPError(BASE + "/chat/completions", 403, "Forbidden", {},
                        io.BytesIO(b'{"error":{"code":"AccessDenied.Unpurchased"}}'))
        endpoint = FakeOpen(error=err)
        with patch.dict(os.environ, ENV), patch(
                "msw_engine.aliyun_protocols.build_opener", return_value=endpoint):
            with self.assertRaisesRegex(TaskError, "http_403:AccessDenied.Unpurchased"):
                generate(DATA, self.root, protocol="chat_stream")
            with self.assertRaisesRegex(TaskError, "http_403:AccessDenied.Unpurchased"):
                generate(DATA, self.root, protocol="chat_stream")
        self.assertEqual(len(endpoint.calls), 1)

    def test_same_request_id_cannot_switch_protocol(self):
        a = sse({"choices": [{"delta": {"content": "OK"}, "finish_reason": "stop"}]})
        endpoint = FakeOpen(a)
        with patch.dict(os.environ, ENV), patch(
                "msw_engine.aliyun_protocols.build_opener", return_value=endpoint):
            generate(DATA, self.root, protocol="chat_stream")
            with self.assertRaisesRegex(TaskError, "request_conflict"):
                generate(DATA, self.root, protocol="responses")
        self.assertEqual(len(endpoint.calls), 1)

    def test_invalid_protocol_blocks_request(self):
        with patch.dict(os.environ, ENV):
            with self.assertRaisesRegex(TaskError, "aliyun_protocol_invalid"):
                generate(DATA, self.root, protocol="bad")

    def test_network_timeout_uncertain_never_retry(self):
        endpoint = FakeOpen(error=URLError("unavailable"))
        with patch.dict(os.environ, ENV), patch(
                "msw_engine.aliyun_protocols.build_opener", return_value=endpoint):
            with self.assertRaisesRegex(TaskError, "aliyun_protocol_transport"):
                generate(DATA, self.root, protocol="responses")
            with self.assertRaisesRegex(TaskError, "outcome_unknown"):
                generate(DATA, self.root, protocol="responses")
        self.assertEqual(len(endpoint.calls), 1)


if __name__ == "__main__":
    unittest.main()
