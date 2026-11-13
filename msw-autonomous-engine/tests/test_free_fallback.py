from contextlib import closing
from datetime import datetime, timezone
from email.utils import format_datetime
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

from msw_engine.tasks import TaskError
from msw_engine.free_fallback import (Ledger, Router, ProviderFault, canonical, digest,
                                    load_policy, maybe_generate, primary_fault,
                                    public_input_digest, retry_after_at, retry_suffix)
from msw_engine import free_transports as ft


def success(provider="free_test", text="Public summary"):
    return {"state": "completed", "text": text, "model": "test-approved-model",
            "provider": provider, "answer_sha256": hashlib.sha256(text.encode()).hexdigest()}


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.now = 1794528000
        self.clock = lambda: self.now
        spec = {"daily_requests": 10, "daily_output_tokens": 10000,
                "min_interval_seconds": 1, "max_tokens": 3000}
        self.policy = {"schema": "msw.free.fallback.v1", "enabled": True,
                       "revision": "offline-test-v1", "primary": spec,
                       "providers": [{**spec, "id": "ai_horde", "max_tokens": 256},
                                     {**spec, "id": "openrouter_wp", "max_tokens": 512}]}
        self.path = self.root / "operator-policy.json"
        self.path.write_bytes(canonical(self.policy))
        self.path.chmod(0o600)
        self.saved = {"values": {"request_id": "public-test-request-001", "task": "review",
                                 "text": "Summarize this public API documentation.", "max_tokens": 64},
                      "transport": "direct_openai"}
        self.approve(self.saved)
        self.first, self.second = Mock(return_value=success("horde")), Mock(return_value=success("openrouter"))
        self.transports = {"ai_horde": self.first, "openrouter_wp": self.second}

    def approve(self, saved):
        sha = public_input_digest(saved["values"])
        path = self.root / "public_grants" / (sha + ".json")
        path.parent.mkdir(exist_ok=True, mode=0o700)
        path.write_bytes(canonical({"schema": "msw.public.input.grant.v1", "classification": "public_non_customer",
                                   "values_sha256": sha, "approved_by": "owner", "expires_at": self.now + 86400 * 30}))
        path.chmod(0o600)

    def router(self):
        return Router(self.root / "data", self.policy, self.path, transports=self.transports, clock=self.clock)

    def new_request(self, suffix):
        saved = {**self.saved, "values": {**self.saved["values"], "request_id": "public-test-request-" + suffix}}
        self.approve(saved)
        return saved

    def limited(self, status=429, until=0):
        return Mock(side_effect=TaskError(f"openai_terminal_failure:http_{status}:retry_at_{until}"))

    def test_primary_success_returns_without_free_call(self):
        primary = Mock(return_value=success("original_paid"))
        result = self.router().run(self.saved, primary)
        self.assertEqual(result["provider"], "original_paid")
        self.assertFalse(result["routing"]["used_free_fallback"])
        self.first.assert_not_called()
        self.second.assert_not_called()
        primary.assert_called_once_with()

    def test_paid_quota_to_free_then_rate_limit_to_next_free(self):
        self.first.side_effect = ProviderFault("rate_limit", "provider_http_429", retry_at=self.now + 600)
        primary = Mock(side_effect=TaskError("openai_terminal_failure:http_429:insufficient_quota:retry_at_0"))
        result = self.router().run(self.saved, primary)
        self.assertEqual(result["provider"], "openrouter")
        self.assertEqual([x["state"] for x in result["routing"]["events"]], ["quota_exhausted", "rate_limit", "completed"])
        self.assertTrue(result["routing"]["used_free_fallback"])
        self.assertFalse(result["routing"]["paid_fallback_allowed"])
        primary.assert_called_once_with()

    def test_unknown_primary_cannot_trigger_free_or_repeat_after_restart(self):
        primary = Mock(side_effect=TaskError("msw_brain_uncertain:openai_transport"))
        with self.assertRaisesRegex(TaskError, "UNKNOWN"):
            self.router().run(self.saved, primary)
        self.now += 86400
        with self.assertRaisesRegex(TaskError, "UNKNOWN"):
            self.router().run(self.saved, primary)
        primary.assert_called_once_with()
        self.first.assert_not_called()
        self.second.assert_not_called()

    def test_unknown_free_stops_before_second_provider(self):
        self.first.side_effect = ProviderFault("unknown", "horde_transport_unknown")
        primary = self.limited()
        for _ in range(2):
            with self.assertRaisesRegex(TaskError, "UNKNOWN"):
                self.router().run(self.saved, primary)
        primary.assert_called_once_with()
        self.first.assert_called_once()
        self.second.assert_not_called()

    def test_auth_401_is_explicit_and_blocks_same_provider_for_future_jobs(self):
        self.first.side_effect = ProviderFault("auth_invalid", "provider_http_401")
        result = self.router().run(self.saved, self.limited())
        self.assertEqual(result["routing"]["events"][1]["state"], "auth_invalid")
        self.now += 600
        self.router().run(self.new_request("002"), self.limited())
        self.first.assert_called_once()
        with closing(self.router().ledger.connect()) as db:
            self.assertEqual(db.execute("SELECT blocked FROM gates WHERE provider='ai_horde'").fetchone()[0], "auth_invalid")

    def test_403_policy_failure_never_shops_for_another_provider(self):
        with self.assertRaisesRegex(TaskError, "BLOCKED"):
            self.router().run(self.saved, self.limited(403))
        self.first.assert_not_called()
        self.second.assert_not_called()

    def test_no_public_grant_blocks_private_or_enriched_input(self):
        self.saved["values"]["text"] += " Internal customer case added by RAG."
        with self.assertRaisesRegex(TaskError, "public_input_grant_missing"):
            self.router().run(self.saved, self.limited())
        self.first.assert_not_called()

    def test_even_granted_secret_pattern_is_blocked(self):
        self.saved["values"]["text"] = "password=not-a-real-secret"
        self.approve(self.saved)
        with self.assertRaisesRegex(TaskError, "sensitive_pattern"):
            self.router().run(self.saved, self.limited())
        self.first.assert_not_called()

    def test_even_granted_customer_phone_pattern_is_blocked(self):
        self.saved["values"]["text"] = "Customer phone +48 111 222 333"
        self.approve(self.saved)
        with self.assertRaisesRegex(TaskError, "sensitive_pattern"):
            self.router().run(self.saved, self.limited())
        self.first.assert_not_called()

    def test_expired_grant_is_not_refreshed_automatically(self):
        self.now += 86400 * 31
        with self.assertRaisesRegex(TaskError, "grant_invalid"):
            self.router().run(self.saved, self.limited())
        self.first.assert_not_called()

    def test_daily_budget_counts_failures_and_survives_process_restart(self):
        self.policy["primary"]["daily_requests"] = 1
        self.policy["providers"][0]["daily_requests"] = 1
        self.policy["providers"] = self.policy["providers"][:1]
        self.router().run(self.saved, self.limited())
        self.now += 600
        next_primary = Mock(return_value=success())
        with self.assertRaisesRegex(TaskError, "EXHAUSTED"):
            self.router().run(self.new_request("002"), next_primary)
        next_primary.assert_not_called()
        self.first.assert_called_once()
        with closing(self.router().ledger.connect()) as db:
            self.assertEqual(dict(db.execute("SELECT provider,requests FROM budgets")), {"paid:openai": 1, "ai_horde": 1})

    def test_provider_cooldown_is_shared_across_requests_and_resumes_after_deadline(self):
        self.first.side_effect = ProviderFault("rate_limit", "provider_http_429", retry_at=self.now + 3600)
        self.router().run(self.saved, self.limited())
        self.now += 600
        self.router().run(self.new_request("002"), self.limited())
        self.first.assert_called_once()
        self.now += 3601
        self.first.side_effect = None
        self.router().run(self.new_request("003"), self.limited())
        self.assertEqual(self.first.call_count, 2)

    def test_completed_receipt_replays_without_any_transport(self):
        primary = self.limited()
        result = self.router().run(self.saved, primary)
        self.assertEqual(self.router().run(self.saved, primary), result)
        primary.assert_called_once_with()
        self.first.assert_called_once()

    def test_request_identity_or_policy_change_cannot_resend(self):
        self.router().run(self.saved, self.limited())
        self.saved["values"]["text"] = "Different data"
        with self.assertRaisesRegex(TaskError, "request_conflict"):
            self.router().run(self.saved, Mock())

    def test_concurrent_same_job_has_only_one_active_call(self):
        entered, release = threading.Event(), threading.Event()
        results, errors = [], []
        def primary():
            entered.set()
            release.wait(3)
            return success("original_paid")
        def run():
            try:
                results.append(self.router().run(self.saved, primary))
            except Exception as error:
                errors.append(error)
        thread = threading.Thread(target=run)
        thread.start()
        self.assertTrue(entered.wait(3))
        try:
            with self.assertRaisesRegex(TaskError, "UNKNOWN"):
                self.router().run(self.saved, Mock())
        finally:
            release.set()
            thread.join(3)
        self.assertFalse(errors)
        self.assertEqual(len(results), 1)

    def test_crash_reservation_stays_unknown(self):
        ledger = self.router().ledger
        ledger.begin(digest(self.saved["values"]["request_id"]), digest(["direct_openai", self.saved["values"]]), digest(self.policy))
        with self.assertRaisesRegex(TaskError, "UNKNOWN"):
            self.router().run(self.saved, Mock())
        self.first.assert_not_called()

    def test_ledger_contains_hashes_and_answer_but_no_input_text(self):
        self.router().run(self.saved, self.limited())
        with closing(self.router().ledger.connect()) as db:
            dump = "\n".join(db.iterdump())
        self.assertNotIn(self.saved["values"]["text"], dump)
        self.assertNotIn(self.saved["values"]["request_id"], dump)

    def test_opt_in_absent_keeps_original_dispatch(self):
        primary = Mock(return_value=success())
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(maybe_generate(self.saved, self.root / "untouched", primary), success())
        primary.assert_called_once_with()

    def test_disabling_does_not_reopen_unknown_job(self):
        with self.assertRaises(TaskError):
            self.router().run(self.saved, Mock(side_effect=TimeoutError()))
        with patch.dict(os.environ, {}, clear=True), self.assertRaisesRegex(TaskError, "policy_required_for_resume"):
            maybe_generate(self.saved, self.root / "data", Mock())

    def test_policy_disallows_paid_provider_and_duplicate_account_routes(self):
        for providers in [[{**self.policy["providers"][0], "id": "paid_openrouter"}],
                          [self.policy["providers"][0], self.policy["providers"][0]]]:
            self.path.write_bytes(canonical({**self.policy, "providers": providers}))
            with self.assertRaises(TaskError):
                load_policy(self.path)

    def test_federation_adapter_preserves_result_contract_and_exposes_route(self):
        from msw_engine.federation import _finish_request
        saved = {**self.saved, "steps": [], "sources": [], "context_truncated": False}
        with patch.dict(os.environ, {"MSW_FREE_FALLBACK_POLICY": str(self.path)}), \
             patch("msw_engine.openai_direct.generate", side_effect=TaskError("openai_terminal_failure:http_429:retry_at_0")), \
             patch("msw_engine.free_transports.ai_horde", return_value=success("ai_horde_anonymous")):
            result = _finish_request(saved, self.root / "data")
        self.assertEqual(result["schema"], "msw.federation.result.v1")
        self.assertEqual(result["provider"], "ai_horde_anonymous")
        self.assertTrue(result["routing"]["used_free_fallback"])


class ProtocolTests(unittest.TestCase):
    def test_retry_after_delta_and_http_date_are_not_capped(self):
        now = 1794528000
        self.assertEqual(retry_after_at("172800", now), now + 172800)
        date = format_datetime(datetime.fromtimestamp(now + 3600, timezone.utc), usegmt=True)
        self.assertEqual(retry_after_at(date, now), now + 3600)
        self.assertIsNone(retry_after_at("tomorrow-ish", now))
        self.assertEqual(retry_after_at(None, now), 0)
        self.assertEqual(retry_suffix({"Retry-After": "120"}, now), ":retry_at_1794528120")

    def test_unverified_quota_text_is_unknown_and_cannot_route(self):
        for code in ("quota problem maybe", "federation_unavailable", "native_bridge_http_502",
                     "msw_brain_uncertain:openai_http_503", "openai_terminal_failure:response_incomplete"):
            self.assertEqual(primary_fault(TaskError(code)).kind, "unknown")
        old = primary_fault(TaskError("openai_terminal_failure:http_429:insufficient_quota"))
        self.assertEqual(old.kind, "quota_exhausted")
        self.assertEqual(old.code, "primary_retry_metadata_unavailable")

    def test_horde_request_uses_only_observed_endpoint_public_identifier_and_plain_text(self):
        response = {"model": ft.HORDE_MODEL, "choices": [{"finish_reason": "stop", "message": {"content": "Public answer"}}]}
        context = Mock()
        context.__enter__ = Mock(return_value=Mock(read=Mock(return_value=json.dumps(response).encode())))
        context.__exit__ = Mock(return_value=False)
        with patch("msw_engine.free_transports.protected_open", return_value=context) as network:
            result = ft.ai_horde({"text": "Public test"}, 64, "a" * 64)
        request = network.call_args.args[1]
        self.assertEqual(request.full_url, "https://oai.aihorde.net/v1/chat/completions")
        self.assertEqual(request.get_header("Authorization"), "Bearer 0000000000")
        self.assertEqual(json.loads(request.data), {"model": ft.HORDE_MODEL, "messages": [{"role": "user", "content": "Public test"}], "max_tokens": 64, "stream": False, "temperature": 0})
        self.assertEqual(result["text"], "Public answer")

    def test_horde_timeout_is_unknown_not_retryable(self):
        with patch("msw_engine.free_transports.protected_open", side_effect=URLError("lost response")):
            with self.assertRaises(ProviderFault) as error:
                ft.ai_horde({"text": "Public test"}, 64, "a" * 64)
        self.assertEqual(error.exception.kind, "unknown")

    def test_existing_origin_circuit_is_respected_without_an_outbound_retry(self):
        from msw_engine.recovery import OriginPaused
        with patch("msw_engine.free_transports.protected_open", side_effect=OriginPaused(3600)) as send:
            with self.assertRaises(ProviderFault) as error:
                ft.ai_horde({"text": "Public test"}, 64, "a" * 64)
        self.assertEqual(error.exception.kind, "not_sent")
        self.assertGreater(error.exception.retry_at, 0)
        send.assert_called_once()

    def test_observed_short_horde_fixture_is_not_mislabeled_complete(self):
        # Exact structural facts from the earlier successful anonymous smoke test.
        answer = {"model": ft.HORDE_MODEL, "choices": [{"finish_reason": "length", "message": {"content": "MSW OK"}}]}
        context = Mock()
        context.__enter__ = Mock(return_value=Mock(read=Mock(return_value=json.dumps(answer).encode())))
        context.__exit__ = Mock(return_value=False)
        with patch("msw_engine.free_transports.protected_open", return_value=context):
            with self.assertRaises(ProviderFault) as error:
                ft.ai_horde({"text": "Reply with MSW OK"}, 16, "a" * 64)
        self.assertEqual(error.exception.kind, "blocked")

    def test_native_current_contract_absent_never_sends_generation(self):
        with patch("msw_engine.runtime_bridge.call", return_value={"version": "1.0.3"}) as call:
            with self.assertRaises(ProviderFault) as error:
                ft.openrouter_wp({"request_id": "public-test-001", "text": "Public", "task": "review", "max_tokens": 64}, 64, "a" * 64)
        self.assertEqual(error.exception.kind, "not_sent")
        call.assert_called_once_with("status")

    def test_native_price_must_be_zero_verified_and_server_durable(self):
        capability = {"free_fallback": {"schema": "msw.free.callback.v1", "enabled": True,
                       "providers": {"openrouter_free": {"configured": True, "cost_ceiling_usd": "0",
                                      "public_input_grants": True, "durable_receipts": True, "retry_after": True}}}}
        values = {"request_id": "public-test-001", "text": "Public", "task": "review", "max_tokens": 64}
        receipt = {**success(), "request_id": values["request_id"], "public_input_sha256": "a" * 64,
                   "storage_readback_verified": True, "cost_usd": "0", "free_model_verified": True, "paid_fallback_allowed": False}
        for bad in ({"cost_usd": "0.001"}, {"free_model_verified": False}, {"paid_fallback_allowed": True}, {"storage_readback_verified": False}):
            with self.subTest(bad=bad), patch("msw_engine.runtime_bridge.call", side_effect=[capability, {**receipt, **bad}]):
                with self.assertRaises(ProviderFault) as error:
                    ft.openrouter_wp(values, 64, "a" * 64)
                self.assertEqual(error.exception.kind, "unknown")
        with patch("msw_engine.runtime_bridge.call", side_effect=[capability, receipt]):
            self.assertEqual(ft.openrouter_wp(values, 64, "a" * 64)["cost_usd"], "0")

    def test_direct_http_retry_metadata_is_durable_without_reissuing_paid_request(self):
        from msw_engine import openai_direct
        with tempfile.TemporaryDirectory() as directory:
            values = {"text": "Public test", "request_id": "direct-429-001", "max_tokens": 64}
            error = HTTPError("https://api.openai.com/v1/responses", 429, "rate limit", {"Retry-After": "7200"}, io.BytesIO(b'{"error":{"code":"rate_limit_exceeded"}}'))
            with patch.dict(os.environ, {"OPENAI_API_KEY": "offline-test-only-placeholder", "MSW_OPENAI_MODEL": "test-model"}), \
                 patch("msw_engine.openai_direct.urlopen", side_effect=error) as send:
                with self.assertRaises(TaskError) as first:
                    openai_direct.generate(values, Path(directory))
                with self.assertRaises(TaskError) as replay:
                    openai_direct.generate(values, Path(directory))
            self.assertEqual(str(first.exception), str(replay.exception))
            self.assertIn(":retry_at_", str(first.exception))
            self.assertGreater(primary_fault(first.exception).retry_at, 0)
            send.assert_called_once()


if __name__ == "__main__":
    unittest.main()
