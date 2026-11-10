"""No-network tests for the opt-in Alibaba credentials smoke probe."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from msw_engine.aliyun_probe import check
from msw_engine.tasks import TaskError


class TestAliyunProbe(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.config = {
            "MSW_ALIYUN_PROBE": "1",
            "MSW_ALIYUN_MODEL": "qwen-plus-2025-12-01",
            "MSW_ALIYUN_API_KEY": "private-mocked-only",
        }
        self.response = {
            "state": "completed",
            "provider": "aliyun_model_studio",
            "api_text_verified": True,
            "answer_sha256": "a" * 64,
            "text": "OK",
        }

    def test_disabled_by_default(self):
        with patch.dict(os.environ, {**self.config, "MSW_ALIYUN_PROBE": "0"}):
            result = check(self.root, inference=lambda *args: self.fail("not permitted"))
        self.assertEqual(result["state"], "disabled")

    def test_missing_key_stops_without_request(self):
        with patch.dict(os.environ, {**self.config, "MSW_ALIYUN_API_KEY": ""}):
            result = check(self.root, inference=lambda *args: self.fail("not permitted"))
        self.assertEqual(result["state"], "missing_scoped_api_key")
        self.assertFalse(result["authenticated"])

    def test_live_response_metadata_has_no_secret_or_prompt(self):
        calls = []
        def fake(payload, directory):
            calls.append(payload)
            return self.response
        with patch.dict(os.environ, self.config):
            result = check(self.root, inference=fake)
        self.assertTrue(result["authenticated"])
        self.assertTrue(result["fresh_api_call"])
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0]["request_id"].startswith("msw-aliyun-smoke-v1-"))
        self.assertEqual(calls[0]["max_tokens"], 128)
        self.assertNotIn("private-mocked-only", str(result))
        self.assertNotIn("OK", str(result))

    def test_error_is_sanitized_and_does_not_claim_success(self):
        def fail(*args):
            raise TaskError("aliyun_terminal_failure:http_403")
        with patch.dict(os.environ, self.config):
            result = check(self.root, inference=fail)
        self.assertEqual(result["state"], "failed")
        self.assertFalse(result["authenticated"])
        self.assertEqual(result["error_code"], "aliyun_terminal_failure:http_403")

    def test_bad_nonce_fails_closed(self):
        with patch.dict(os.environ, {**self.config, "MSW_ALIYUN_PROBE_RUN_ID": "?invalid"}):
            result = check(self.root, inference=lambda *args: self.fail("not permitted"))
        self.assertEqual(result["state"], "probe_id_invalid")


if __name__ == "__main__":
    unittest.main()
