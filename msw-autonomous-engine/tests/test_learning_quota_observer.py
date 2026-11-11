"""Offline observer tests: no CLI invocation, no paid inference, no credentials."""
import json
from unittest.mock import patch
import unittest

from telemetry.learning_quota_observer import learning, quota, cli_quota_readonly


class ObserverTest(unittest.TestCase):
    NOW = "2026-11-11T00:40:00+00:00"
    RESET = 1797033600000  # Synthetic future monthly reset, UTC.

    def test_quota_valid_standard_monthly_ratio(self):
        # Use a future reset to verify the ratio and alert threshold.
        from datetime import datetime, timedelta, timezone
        now = datetime.fromisoformat(self.NOW)
        reset = int((now + timedelta(days=25)).timestamp() * 1000)
        q = quota({"hasQuotaData": True, "per1MonthPercentage": 0.75,
                   "per1MonthResetTime": reset}, observed_at=self.NOW)
        self.assertEqual(q["state"], "VERIFIED_USAGE")
        self.assertTrue(q["verified"])
        self.assertEqual(q["percent_used"], 75)
        self.assertEqual(q["level"], "caution")
        self.assertEqual(q["approx_used_credits_standard_2026_11"], 60000)
        self.assertTrue(q["estimated_not_provider_bill"])

    def test_quota_thresholds(self):
        from datetime import datetime, timedelta
        now = datetime.fromisoformat(self.NOW)
        reset = int((now + timedelta(days=10)).timestamp() * 1000)
        for ratio, expected in ((0.02, "normal"), (0.60, "caution"),
                                (0.80, "high"), (0.90, "critical")):
            with self.subTest(ratio=ratio):
                out = quota({"hasQuotaData": True, "per1MonthPercentage": ratio,
                             "per1MonthResetTime": reset}, observed_at=self.NOW)
                self.assertEqual(out["level"], expected)

    def test_missing_or_invalid_is_never_zero_usage(self):
        bad = [None, {}, {"hasQuotaData": False}, {"hasQuotaData": True},
               {"hasQuotaData": True, "per1MonthPercentage": "25%",
                "per1MonthResetTime": self.RESET},
               {"hasQuotaData": True, "per1MonthPercentage": 1.2,
                "per1MonthResetTime": self.RESET}]
        for value in bad:
            with self.subTest(value=value):
                q = quota(value, observed_at=self.NOW)
                self.assertFalse(q["verified"])
                self.assertNotIn("percent_used", q)

    def test_editor_metadata_is_not_training_and_knowledge_is_separate(self):
        data = {
            "editor_events": [
                {"utc": "2026-11-10T23:00:00Z", "post_id": 20, "source": "wordpress_editor_metadata"},
                {"utc": "2026-11-10T23:00:00Z", "post_id": 20, "source": "wordpress_editor_metadata"},
                {"utc": "2026-11-10T23:01:00Z", "post_id": 21, "source": "wordpress_editor_metadata"}
            ],
            "historical_learning": [
                {"state": "source_batch_reviewed", "storage_readback_verified": True, "records_reviewed": 10},
                {"state": "source_batch_reviewed", "storage_readback_verified": True, "records_reviewed": 14}
            ],
            "knowledge_receipts": [
                {"receipt_id": "known-00001", "storage_readback_verified": True, "readback_verified_records": 3},
                {"receipt_id": "known-00001", "storage_readback_verified": True, "readback_verified_records": 3},
                {"receipt_id": "unverified-0001", "storage_readback_verified": False, "readback_verified_records": 100}
            ],
            "training_receipts": [
                {"receipt_id": "train-00001", "training_performed": True,
                 "checkpoint_readback_verified": True, "steps_this_job": 8, "weights_modified": True},
                {"receipt_id": "train-00002", "training_performed": False, "steps_this_job": 400},
            ],
            "evaluations": [
                {"evaluation_run_id": "evaluation-1", "independent_evaluation_verified": True,
                 "quality_improvement_proven": True}
            ]
        }
        m = learning(data)
        self.assertEqual(m["editor_events_observed"], 2)
        self.assertEqual(m["unique_posts_observed"], 2)
        self.assertTrue(m["edits_are_not_training"])
        self.assertEqual(m["historical_records_reviewed_cumulative"], 14)
        self.assertEqual(m["knowledge_verified_ingest_records"], 3)
        self.assertEqual(m["verified_weight_training_runs"], 1)
        self.assertEqual(m["weights_modified_runs"], 1)
        self.assertEqual(m["independently_proven_quality_improvements"], 1)

    def test_no_training_receipts_means_not_measured_not_zero(self):
        m = learning({"editor_events":[{"utc":"2026-11-10T23:01:00Z", "post_id": 20,
                                       "source": "wordpress_editor_metadata"}]})
        self.assertIsNone(m["verified_weight_training_runs"])
        self.assertIsNone(m["verified_training_steps_in_receipts"])
        self.assertEqual(m["editor_events_observed"], 1)

    def test_cli_read_returns_not_connected_without_printing_raw_errors(self):
        with patch("telemetry.learning_quota_observer.subprocess.run", side_effect=OSError("secret-in-error")):
            self.assertIsNone(cli_quota_readonly())

    def test_cli_read_only_command_invoked_once_never_inference(self):
        class Result:
            returncode = 0
            stdout = '{"hasQuotaData":false}'
        with patch("telemetry.learning_quota_observer.subprocess.run", return_value=Result()) as run:
            result = cli_quota_readonly()
        self.assertFalse(result["hasQuotaData"])
        run.assert_called_once()
        self.assertEqual(run.call_args.args[0], ["bl","usage","token-plan","--output","json"])
        self.assertFalse(run.call_args.kwargs.get("shell", False))


if __name__ == "__main__":
    unittest.main()
