"""Offline contract checks for the opt-in Alibaba Cloud provider."""
import io
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

from msw_engine.aliyun_direct import QUEUE_SCHEMA, generate
from msw_engine.tasks import TaskError, dispatch

BASE = "https://ws-8ll117pdwwgx97h2.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1"
ENV = {"MSW_ALIYUN_API_KEY": "unit-test-secret", "MSW_ALIYUN_MODEL": "test-model"}
PROMPT = {"text": "Bezpieczny test integracji", "request_id": "test-request-00001", "max_tokens": 64}


class FakeOpener:
    def __init__(self, raw, error=None):
        self.raw, self.error = raw, error
        self.calls = 0

    def open(self, request, timeout):
        self.calls += 1
        assert request.full_url == BASE + "/chat/completions"
        assert request.get_header("Authorization") == "Bearer unit-test-secret"
        assert timeout <= 60
        body = json.loads(request.data)
        assert body["model"] == ENV["MSW_ALIYUN_MODEL"]
        assert body["messages"][0]["role"] == "user"
        assert "store" not in body
        self.last_body = body
        if self.error:
            raise self.error
        return io.BytesIO(self.raw)


class AliyunDirectTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_fails_closed_without_key(self):
        with patch.dict(os.environ, {"MSW_ALIYUN_API_KEY": ""}):
            with self.assertRaisesRegex(TaskError, "aliyun_key_missing"):
                generate(PROMPT, self.root)
        self.assertFalse((self.root / "aliyun-direct").exists())

    def test_fails_closed_without_model(self):
        with patch.dict(os.environ, {**ENV, "MSW_ALIYUN_MODEL": ""}):
            with self.assertRaisesRegex(TaskError, "aliyun_direct_model_invalid"):
                generate(PROMPT, self.root)

    def test_completion_persists_and_replay_does_not_rebill(self):
        response = json.dumps({"choices": [{"finish_reason": "stop", "message": {"content": "Test OK"}}]}).encode()
        opener = FakeOpener(response)
        with patch.dict(os.environ, ENV), patch("msw_engine.aliyun_direct.build_opener", return_value=opener):
            first = generate(PROMPT, self.root)
            second = generate(PROMPT, self.root)
        self.assertEqual(first, second)
        self.assertEqual(first["text"], "Test OK")
        self.assertEqual(first["provider"], "aliyun_model_studio")
        self.assertEqual(opener.calls, 1)
        with sqlite3.connect(self.root / "aliyun-direct" / "requests.sqlite3") as db:
            row = db.execute("SELECT state,text FROM requests").fetchone()
        self.assertEqual(row, ("completed", "Test OK"))

    def test_explicit_queue_job_uses_only_the_requested_prompt(self):
        opener = FakeOpener(b'{"choices":[{"finish_reason":"stop","message":{"content":"OK"}}]}')
        job = {"schema": QUEUE_SCHEMA, "prompt": PROMPT["text"], "request_id": PROMPT["request_id"]}
        with patch.dict(os.environ, {**ENV, "MSW_PAID_AI_TRANSPORT": "direct_openai"}), \
                patch("msw_engine.aliyun_direct.build_opener", return_value=opener), \
                patch("msw_engine.openai_direct.generate") as openai:
            result = dispatch("llm.generate", job, self.root)
            self.assertEqual(result, dispatch("llm.generate", job, self.root))
        openai.assert_not_called()
        self.assertEqual(opener.calls, 1)
        self.assertEqual(opener.last_body["messages"], [{"role": "user", "content": PROMPT["text"]}])
        self.assertEqual(result["schema"], "msw.aliyun.result.v1")
        self.assertTrue(result["api_text_verified"])

    def test_ordinary_generation_keeps_its_existing_provider(self):
        with patch.dict(os.environ, {**ENV, "MSW_LLM_PROVIDER": "wordpress", "MSW_LLM_MODEL": "test-gpt",
                                     "MSW_PAID_AI_TRANSPORT": "direct_openai"}), \
                patch("msw_engine.aliyun_direct.generate") as aliyun, \
                patch("msw_engine.openai_direct.generate", return_value={"text": "OK", "model": "test-gpt"}) as openai:
            dispatch("llm.generate", {"prompt": "Test", "use_knowledge": False, "collaborate": False}, self.root)
        aliyun.assert_not_called()
        openai.assert_called_once()

    def test_queue_rejects_plan_key_and_unapproved_context(self):
        job = {"schema": QUEUE_SCHEMA, "prompt": PROMPT["text"], "request_id": PROMPT["request_id"]}
        with patch.dict(os.environ, {**ENV, "MSW_LLM_MODEL": "test-gpt"}), \
                patch("msw_engine.aliyun_direct.generate") as generate_aliyun:
            with self.assertRaisesRegex(TaskError, "queue_argument_unknown"):
                dispatch("llm.generate", {**job, "use_knowledge": True}, self.root)
            with patch.dict(os.environ, {"MSW_ALIYUN_API_KEY": "sk-sp-test"}):
                with self.assertRaisesRegex(TaskError, "queue_model_studio_not_configured"):
                    dispatch("llm.generate", job, self.root)
        generate_aliyun.assert_not_called()

    def test_conflict_cannot_reuse_same_request_id(self):
        opener = FakeOpener(json.dumps({"choices": [{"finish_reason": "stop", "message": {"content": "OK"}}]}).encode())
        with patch.dict(os.environ, ENV), patch("msw_engine.aliyun_direct.build_opener", return_value=opener):
            generate(PROMPT, self.root)
            with self.assertRaisesRegex(TaskError, "request_conflict"):
                generate({**PROMPT, "text": "another prompt"}, self.root)
        self.assertEqual(opener.calls, 1)

    def test_uncertain_transport_never_auto_retries(self):
        opener = FakeOpener(b"", error=URLError("test network unavailable"))
        with patch.dict(os.environ, ENV), patch("msw_engine.aliyun_direct.build_opener", return_value=opener):
            with self.assertRaisesRegex(TaskError, "aliyun_transport"):
                generate(PROMPT, self.root)
            with self.assertRaisesRegex(TaskError, "outcome_unknown"):
                generate(PROMPT, self.root)
        self.assertEqual(opener.calls, 1)

    def test_truncated_completion_not_accepted(self):
        opener = FakeOpener(json.dumps({"choices": [{"finish_reason": "length", "message": {"content": "partial"}}]}).encode())
        with patch.dict(os.environ, ENV), patch("msw_engine.aliyun_direct.build_opener", return_value=opener):
            with self.assertRaisesRegex(TaskError, "response_invalid_or_incomplete"):
                generate(PROMPT, self.root)


if __name__ == "__main__":
    unittest.main()
