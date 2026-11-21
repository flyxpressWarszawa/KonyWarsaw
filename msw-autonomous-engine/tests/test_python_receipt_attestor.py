"""Offline regression tests for staging receipt attestation and central ACK contract."""
from datetime import datetime, timezone, timedelta
import hashlib
import hmac
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from msw_engine import python_task_pilot as p
from msw_engine import python_receipt_attestor as a


def fixture_doc():
    return {"schema": p.SCHEMA, "classification": "synthetic_public_test",
            "tasks": [
                {"task_id": "py10-20261114-seo", "kind": "seo.analyze", "payload": {
                    "title": "Test SEO", "content": "alpha beta alpha",
                    "html": '<h2>Test</h2><img alt="ok"><a href="/a">a</a>',
                    "url": "https://example.org/"}},
                {"task_id": "py10-20261114-rank", "kind": "research.rank", "payload": {"ideas": [
                    {"id": "fixture-a", "title": "Synthetic A", "demand": 80,
                     "competition": 20, "margin": 70, "delivery_effort": 30,
                     "sources": ["https://example.org/"]}]}}
            ]}


class FakeStore:
    def __init__(self):
        doc = fixture_doc()
        self.files = {p.REQUEST_PATH: p.wire(doc)}
        self.gets = []
        for t in doc["tasks"]:
            output = p.execute(t, Path("/tmp/unneeded"))
            receipt = {
                "schema": "msw.python-task-pilot.receipt.v1",
                "task_id": t["task_id"], "kind": t["kind"],
                "request_sha256": p.sha(p.wire(t)), "manifest_sha256": a.MANIFEST_SHA,
                "output": output, "result_sha256": p.sha(p.wire(output)),
                "executor_service": p.SERVICE, "git_commit": "b" * 40,
                "executed_at_utc": "2026-11-14T21:05:27+00:00",
                "production_mutation_performed": False, "provider_calls": 0,
                "central_hermes_ack_verified": False, "chatgpt_delivery_verified": False
            }
            self.files[p.result_path(t["task_id"])] = p.wire(receipt)

    def get(self, path):
        self.gets.append(path)
        return self.files.get(path)

    def change(self, ident, key, value):
        path = p.result_path(ident)
        obj = json.loads(self.files[path])
        obj[key] = value
        self.files[path] = p.wire(obj)


class AttestationTests(unittest.TestCase):
    def setUp(self):
        self.store = FakeStore()
        self.ident = "py10-20261114-seo"
        self.now = datetime.now(timezone.utc)

    def run_verify(self):
        return a.verify_previous_pilot(self.store)

    def test_read_only_attests_two_actual_handler_outputs(self):
        result = self.run_verify()
        self.assertEqual(result["state"], "staging_receipts_independently_verified")
        self.assertEqual(len(result["proofs"]), 2)
        self.assertTrue(result["actual_python_execution_checked"])
        self.assertFalse(result["central_hermes_ack_verified"])
        self.assertFalse(result["chatgpt_delivery_verified"])
        self.assertEqual(result["provider_calls"], 0)
        self.assertEqual(len(self.store.gets), 3)

    def test_independent_repeat_is_deterministic(self):
        first, second = self.run_verify(), self.run_verify()
        self.assertEqual(first, second)

    def test_wrong_pinned_manifest_rejected(self):
        with self.assertRaisesRegex(a.AttestationError, "unexpected_manifest_digest"):
            a.verify_previous_pilot(self.store, "f" * 64)

    def test_manifest_contents_tampered_rejected(self):
        self.store.files[p.REQUEST_PATH] += b" "
        with self.assertRaisesRegex(p.PilotError, "manifest_digest"):
            self.run_verify()

    def test_missing_receipt_rejected(self):
        del self.store.files[p.result_path(self.ident)]
        with self.assertRaisesRegex(a.AttestationError, "missing_receipt"):
            self.run_verify()

    def test_wrong_service_rejected(self):
        self.store.change(self.ident, "executor_service", "production")
        with self.assertRaisesRegex(a.AttestationError, "claimed_scope"):
            self.run_verify()

    def test_fake_central_ack_rejected(self):
        self.store.change(self.ident, "central_hermes_ack_verified", True)
        with self.assertRaisesRegex(a.AttestationError, "claimed_scope"):
            self.run_verify()

    def test_fake_chat_delivery_rejected(self):
        self.store.change(self.ident, "chatgpt_delivery_verified", True)
        with self.assertRaisesRegex(a.AttestationError, "claimed_scope"):
            self.run_verify()

    def test_paid_provider_call_rejected(self):
        self.store.change(self.ident, "provider_calls", 1)
        with self.assertRaisesRegex(a.AttestationError, "claimed_scope"):
            self.run_verify()

    def test_extra_receipt_field_rejected(self):
        self.store.change(self.ident, "not_expected", "unexpected")
        with self.assertRaisesRegex(a.AttestationError, "receipt_fields"):
            self.run_verify()

    def test_changed_result_digest_rejected(self):
        self.store.change(self.ident, "result_sha256", "0" * 64)
        with self.assertRaisesRegex(a.AttestationError, "output_integrity"):
            self.run_verify()

    def test_fabricated_result_rejected_by_recompute(self):
        path = p.result_path(self.ident)
        obj = json.loads(self.store.files[path])
        obj["output"]["word_count"] = 777
        obj["result_sha256"] = p.sha(p.wire(obj["output"]))
        self.store.files[path] = p.wire(obj)
        with self.assertRaisesRegex(a.AttestationError, "output_recompute_mismatch"):
            self.run_verify()

    def test_request_binding_rejected(self):
        self.store.change(self.ident, "request_sha256", "0" * 64)
        with self.assertRaisesRegex(a.AttestationError, "task_binding"):
            self.run_verify()

    def test_duplicate_json_keys_rejected(self):
        path = p.result_path(self.ident)
        self.store.files[path] = b'{"schema":1,"schema":2}'
        with self.assertRaisesRegex(a.AttestationError, "receipt_invalid_json"):
            self.run_verify()

    def test_noncanonical_json_rejected(self):
        path = p.result_path(self.ident)
        self.store.files[path] = self.store.files[path] + b"\n"
        with self.assertRaisesRegex(a.AttestationError, "receipt_invalid_json"):
            self.run_verify()

    def test_bad_commit_rejected(self):
        self.store.change(self.ident, "git_commit", "not-a-commit")
        with self.assertRaisesRegex(a.AttestationError, "runtime_commit_invalid"):
            self.run_verify()

    def test_future_time_rejected(self):
        self.store.change(self.ident, "executed_at_utc", "2999-01-01T00:00:00+00:00")
        with self.assertRaisesRegex(a.AttestationError, "receipt_time_invalid"):
            self.run_verify()

    def test_manifest_missing_one_task_rejected(self):
        doc = fixture_doc()
        doc["tasks"] = doc["tasks"][:1]
        raw = p.wire(doc)
        with patch.object(a, "MANIFEST_SHA", p.sha(raw)):
            self.store.files[p.REQUEST_PATH] = raw
            with self.assertRaisesRegex(a.AttestationError, "expected_two_tasks"):
                a.verify_previous_pilot(self.store, p.sha(raw))

    def sample_ack(self, bundle_sha, secret=b"x" * 32):
        content = {
            "schema": a.ACK_SCHEMA, "agent_id": a.ACK_ACTOR,
            "runtime": a.ACK_RUNTIME, "purpose": a.ACK_PURPOSE,
            "bundle_sha256": bundle_sha,
            "issued_at_utc": self.now.isoformat(),
            "nonce": "unique-central-ack-20261114",
        }
        content["signature"] = hmac.new(secret, p.wire(content), hashlib.sha256).hexdigest()
        return content

    def test_offline_signature_contract_never_claims_live_ack(self):
        sha = self.run_verify()["bundle_sha256"]
        res = a.verify_central_ack_signature(self.sample_ack(sha), sha, b"x" * 32, now=self.now)
        self.assertTrue(res["signature_matched"])
        self.assertFalse(res["central_runtime_attested_by_live_transport"])
        self.assertFalse(res["central_hermes_ack_verified"])

    def test_wrong_hmac_secret_rejected(self):
        sha = self.run_verify()["bundle_sha256"]
        with self.assertRaisesRegex(a.AttestationError, "ack_signature_invalid"):
            a.verify_central_ack_signature(self.sample_ack(sha), sha, b"d" * 32, now=self.now)

    def test_wrong_bundle_rejected(self):
        sha = self.run_verify()["bundle_sha256"]
        with self.assertRaisesRegex(a.AttestationError, "ack_scope"):
            a.verify_central_ack_signature(self.sample_ack(sha), "0" * 64, b"x" * 32, now=self.now)

    def test_expired_ack_rejected(self):
        sha = self.run_verify()["bundle_sha256"]
        with self.assertRaisesRegex(a.AttestationError, "ack_expired"):
            a.verify_central_ack_signature(self.sample_ack(sha), sha, b"x" * 32, now=self.now + timedelta(minutes=6))

    def test_duplicate_nonce_rejected(self):
        sha = self.run_verify()["bundle_sha256"]
        with self.assertRaisesRegex(a.AttestationError, "ack_replay"):
            a.verify_central_ack_signature(self.sample_ack(sha), sha, b"x" * 32, now=self.now,
                                            consumed_nonces={"unique-central-ack-20261114"})

    def test_unknown_actor_rejected(self):
        sha = self.run_verify()["bundle_sha256"]
        doc = self.sample_ack(sha)
        doc["agent_id"] = "someone-else"
        with self.assertRaisesRegex(a.AttestationError, "ack_scope"):
            a.verify_central_ack_signature(doc, sha, b"x" * 32, now=self.now)

    def test_missing_signature_fails(self):
        sha = self.run_verify()["bundle_sha256"]
        doc = self.sample_ack(sha)
        del doc["signature"]
        with self.assertRaisesRegex(a.AttestationError, "ack_schema"):
            a.verify_central_ack_signature(doc, sha, b"x" * 32, now=self.now)

    def test_disabled_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(a.run_live_once(), {"state": "disabled"})

    def test_wrong_staging_service_rejected(self):
        with patch.dict(os.environ, {"MSW_HERMES_RECEIPT_ATTEST_STAGING": "1",
                                      "RENDER_SERVICE_ID": "production"}, clear=True):
            self.assertEqual(a.run_live_once(), {"state": "policy_blocked"})

    def test_simultaneous_pilot_and_attestor_rejected(self):
        with patch.dict(os.environ, {"MSW_HERMES_RECEIPT_ATTEST_STAGING": "1",
                                      "RENDER_SERVICE_ID": p.SERVICE,
                                      "MSW_HERMES_PYTHON_TASK_PILOT": "1"}, clear=True):
            self.assertEqual(a.run_live_once(), {"state": "policy_blocked"})


    def job_payload(self):
        return {"schema": "msw.hermes.python-pilot.verify-job.v1",
                "purpose": "read_only_receipt_verification",
                "manifest_sha256": a.MANIFEST_SHA}

    def test_handler_unconfigured_denied(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(a.AttestationError, "handler_opt_in_missing"):
                a.signed_queue_receipt_handler(self.job_payload(), Path("/tmp"))

    def test_handler_wrong_runtime_denied(self):
        with patch.dict(os.environ, {"MSW_HERMES_PYTHON_RECEIPT_VERIFY_HANDLER": "1",
                                      "RENDER_SERVICE_ID": p.SERVICE}, clear=True):
            with self.assertRaisesRegex(a.AttestationError, "central_worker_identity_missing"):
                a.signed_queue_receipt_handler(self.job_payload(), Path("/tmp"))

    def test_handler_rejects_unapproved_payload_before_network(self):
        env = {"MSW_HERMES_PYTHON_RECEIPT_VERIFY_HANDLER": "1",
               "RENDER_SERVICE_ID": "srv-dbltdb9srm7s73ec0v00"}
        payload = self.job_payload()
        payload["kind"] = "shell.execute"
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaisesRegex(a.AttestationError, "verification_job_contract"):
                a.signed_queue_receipt_handler(payload, Path("/tmp"))

    def test_handler_produces_preflight_but_never_ack(self):
        env = {"MSW_HERMES_PYTHON_RECEIPT_VERIFY_HANDLER": "1",
               "RENDER_SERVICE_ID": "srv-dbltdb9srm7s73ec0v00"}
        with patch.dict(os.environ, env, clear=True):
            with patch.object(a, "GitHubStore", return_value=self.store):
                out = a.signed_queue_receipt_handler(self.job_payload(), Path("/tmp"))
        self.assertTrue(out["signed_queue_handler_executed"])
        self.assertTrue(out["actual_python_execution_checked"])
        self.assertFalse(out["wordpress_server_ack_verified"])
        self.assertFalse(out["central_pilot_ack_verified"])

    def test_handler_not_exposed_without_explicit_opt_in(self):
        from msw_engine.tasks import handlers
        with patch.dict(os.environ, {"MSW_HERMES_PYTHON_RECEIPT_VERIFY_HANDLER": "0"}):
            self.assertNotIn("python.pilot.verify", handlers())
        with patch.dict(os.environ, {"MSW_HERMES_PYTHON_RECEIPT_VERIFY_HANDLER": "1",
                                      "MSW_RUNTIME_MODE": ""}):
            self.assertIn("python.pilot.verify", handlers())


if __name__ == "__main__":
    unittest.main()
