"""Regression tests for fail-closed Hermes Codex admission; no production dispatch."""
import unittest
from canary_admission_guard import assess, ELIGIBLE, HOLD

def candidate():
    return dict(root_id="root", task_id="task", request_id="request", work_key="work",
                parent_id="parent", state="new", lease_owner=None, origin="owner",
                kind="text", side_effect_free=True, native_apply_contract_verified=True,
                bounded_output_contract_verified=True,
                idempotency_reservation_committed=True,
                prior_dispatch=False, prior_effect=False)

class AdmissionTests(unittest.TestCase):
    def test_eligible_review_only(self):
        result = assess(candidate())
        self.assertEqual(result["state"], ELIGIBLE)
        self.assertEqual(len(result["identity_sha256"]), 64)
        self.assertIn("REQUIRES_LIVE_LEASE_RECHECK", result["reason"])

    def test_refuse_unknown_or_claimed(self):
        for patch in ({"state": "held"}, {"state": "completed"},
                      {"lease_owner": "worker"}, {"prior_dispatch": None},
                      {"prior_effect": True}, {"prior_effect": None}):
            with self.subTest(patch=patch):
                self.assertEqual(assess(dict(candidate(), **patch))["state"], HOLD)

    def test_refuse_missing_contract(self):
        for field in ("side_effect_free", "native_apply_contract_verified",
                      "bounded_output_contract_verified",
                      "idempotency_reservation_committed"):
            with self.subTest(field=field):
                self.assertEqual(assess(dict(candidate(), **{field: False}))["state"], HOLD)

    def test_refuse_invalid_identity(self):
        for field in ("root_id", "task_id", "request_id", "work_key", "parent_id"):
            with self.subTest(field=field):
                self.assertEqual(assess(dict(candidate(), **{field: "../bad"}))["state"], HOLD)

    def test_refuse_untrusted_origin(self):
        for patch in ({"origin": "queue"}, {"kind": "shell"},
                      {"side_effect_free": "true"}):
            self.assertEqual(assess(dict(candidate(), **patch))["state"], HOLD)

    def test_identity_digest_changes(self):
        a = assess(candidate())["identity_sha256"]
        b = assess(dict(candidate(), request_id="other"))["identity_sha256"]
        self.assertNotEqual(a, b)

if __name__ == "__main__":
    unittest.main()
