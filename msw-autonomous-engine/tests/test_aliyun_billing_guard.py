"""Synthetic tests for billing-mode boundaries: NEVER call Alibaba or Codex.

No real credential values. A personal subscription key must not be used from
the autonomous Python worker, even if other model settings are populated.
"""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from msw_engine.aliyun_billing_guard import personal_plan_key
from msw_engine.aliyun_direct import generate as generate_chat
from msw_engine.aliyun_protocols import generate as generate_protocol
from msw_engine.aliyun_probe import check
from msw_engine.federation import configured
from msw_engine.tasks import TaskError, handlers


PAYLOAD = {
    "text": "Synthetic offline input only",
    "request_id": "msw-aliyun-unit-no-provider-001",
    "max_tokens": 128
}
ENV = {
    "MSW_ALIYUN_API_KEY": "sk-sp-SYNTHETIC_NOT_A_SECRET",
    "MSW_ALIYUN_MODEL": "qwen3.8-max",
    "MSW_PAID_AI_TRANSPORT": "direct_aliyun",
    "MSW_ALIYUN_PROBE": "1",
}


class BillingModeGuardTests(unittest.TestCase):
    def test_guard_identifies_only_explicit_subscription_shapes(self):
        self.assertTrue(personal_plan_key("sk-sp-SYNTHETIC_NOT_A_SECRET"))
        self.assertTrue(personal_plan_key("o1_SYNTHETIC_CLI_ONLY"))
        self.assertFalse(personal_plan_key("sk-mock-payg-key"))
        self.assertFalse(personal_plan_key("unit-test-secret"))
        self.assertFalse(personal_plan_key(""))

    def test_background_chat_blocked_before_network_and_before_creating_receipt(self):
        with tempfile.TemporaryDirectory() as root:
            with patch.dict(os.environ, ENV), patch(
                "msw_engine.aliyun_direct.build_opener"
            ) as network:
                with self.assertRaisesRegex(TaskError, "token_plan_not_for_background_api"):
                    generate_chat(PAYLOAD, Path(root))
                network.assert_not_called()
            self.assertFalse((Path(root) / "aliyun-direct").exists())

    def test_stream_and_responses_blocked_before_network(self):
        with tempfile.TemporaryDirectory() as root:
            with patch.dict(os.environ, ENV), patch(
                "msw_engine.aliyun_protocols.build_opener"
            ) as network:
                for protocol in ("chat_stream", "responses"):
                    with self.subTest(protocol=protocol):
                        with self.assertRaisesRegex(TaskError, "token_plan_not_for_background_api"):
                            generate_protocol(PAYLOAD, Path(root), protocol=protocol)
                network.assert_not_called()
            self.assertFalse((Path(root) / "aliyun-direct").exists())

    def test_background_probe_fails_without_calling_model(self):
        with tempfile.TemporaryDirectory() as root:
            with patch.dict(os.environ, ENV):
                result = check(Path(root), inference=lambda *a, **k: self.fail("No inference permitted"))
        self.assertEqual(result["state"], "wrong_billing_mode")
        self.assertFalse(result["authenticated"])
        self.assertNotIn("SYNTHETIC", str(result))

    def test_background_provider_is_not_advertised(self):
        with patch.dict(os.environ, ENV, clear=True):
            self.assertFalse(configured())
            self.assertNotIn("llm.generate", handlers())

    def test_valid_payg_shaped_key_does_not_claim_success_without_authentication(self):
        with patch.dict(os.environ, {**ENV, "MSW_ALIYUN_API_KEY": "sk-synthetic-payg-not-real"}, clear=True):
            self.assertTrue(configured())  # configuration presence only
            self.assertIn("llm.generate", handlers())
        # Not an API connectivity check; no request was sent.


if __name__ == "__main__":
    unittest.main()
