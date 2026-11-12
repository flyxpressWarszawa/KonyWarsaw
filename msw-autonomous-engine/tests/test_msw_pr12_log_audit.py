import unittest
from tools.msw_pr12_log_audit import audit, auxiliary_event


def log(state, **kwargs):
    import json
    return {"message": json.dumps({"render_auxiliary": {"state": state, **kwargs}})}


class AuditTests(unittest.TestCase):
    def baseline(self):
        events = [log("started", max_jobs_per_cycle=4, batch_protocol="msw.seo.batch.v1")]
        for size, marker in ((4, "A"), (1, "B")):
            events += [log("batch_admitted", jobs=size, protocol="msw.seo.batch.v1")]
            events += [log("completed", job_id=f"{marker}-{index}", server_ack_verified=True)
                       for index in range(size)]
            events += [log("batch_finished", admitted=size, completed=size, failed=0, claim_requests=1)]
        return events

    def test_valid_two_batch_audit_preserves_model_vs_measured_distinction(self):
        result = audit(self.baseline() + [log("origin_pressure", cpu_percent=100)])
        self.assertEqual((result["batches_finished"], result["jobs_completed"]), (2, 5))
        self.assertEqual(result["completed_with_verified_ack"], 5)
        self.assertEqual(result["claim_savings_model"], 3)
        self.assertEqual(result["claim_savings_pct_model"], 60.0)
        self.assertFalse(result["cpu_reduction_measured"])
        self.assertEqual(result["warnings"], [])

    def test_missing_ack_is_error(self):
        records = self.baseline()
        records[2] = log("completed", job_id="A-0", server_ack_verified=False)
        self.assertIn("unverified_completion_ack", audit(records)["warnings"])

    def test_duplicate_completion_is_error(self):
        records = self.baseline()
        records[3] = log("completed", job_id="A-0", server_ack_verified=True)
        self.assertIn("duplicate_completion_ids", audit(records)["warnings"])

    def test_partial_or_failed_batch_is_not_reported_as_success(self):
        records = self.baseline()
        records[-1] = log("batch_finished", admitted=1, completed=0, failed=1, claim_requests=1)
        report = audit(records)
        self.assertIn("batch_reports_failures", report["warnings"])
        self.assertIn("aggregate_job_count_mismatch", report["warnings"])

    def test_out_of_bounds_claim_rejected(self):
        records = self.baseline()
        records[1] = log("batch_admitted", jobs=5, protocol="msw.seo.batch.v1")
        self.assertIn("invalid_batch_admission", audit(records)["warnings"])

    def test_missing_finish_is_error(self):
        self.assertIn("admission_finish_count_mismatch", audit(self.baseline()[:-1])["warnings"])

    def test_invalid_message_ignored(self):
        self.assertIsNone(auxiliary_event({"message": "not json"}))


if __name__ == "__main__":
    unittest.main()
