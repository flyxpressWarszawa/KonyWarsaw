"""Offline, no-network tests for the WordPress→Render offload safety classification."""
from __future__ import annotations
import io
import json
import sys
import unittest
from unittest.mock import patch

from msw_engine.offload_triage import triage, main


def row(kind, jobs, state="pending"):
    return {"state": state, "kind": kind, "jobs": jobs}


class OffloadTriageTests(unittest.TestCase):
    def test_live_shape_counts_native_not_offloaded(self):
        sample = [row("module.execute", "9791"), row("runtime.catalog", "175"),
                  row("supreme.observe", "118"), row("knowledge.search", "2"),
                  row("engine.v3.ingest", "1"), row("module.execute", "1", "running")]
        result = triage(sample)
        self.assertEqual(result["snapshot_pending"], 10087)
        self.assertEqual(result["pending_native_php_effects"], 9791)
        self.assertEqual(result["pending_pure_candidates"], 0)
        self.assertFalse(result["permission_to_deploy"])
        self.assertFalse(result["production_offload_completed"])
        self.assertTrue(all(e["on_render_verified"] is False for e in result["entries"]))
        self.assertEqual(result["actions_executed"], 0)

    def test_pure_seo_is_candidate_only(self):
        result = triage([row("seo.analyze", 10), row("research.rank", 20)])
        self.assertEqual(result["pending_pure_candidates"], 10)
        by_kind = {e["kind"]: e for e in result["entries"]}
        self.assertTrue(by_kind["seo.analyze"]["pure_candidate"])
        self.assertFalse(by_kind["research.rank"]["pure_candidate"])
        self.assertFalse(result["existing_worker_source_hash_verified"])

    def test_native_and_paid_never_classified_as_pure(self):
        for kind in ("module.execute", "llm.generate", "legacy.llm80.train",
                     "knowledge.ingest", "knowledge.search", "runtime.catalog", "supreme.observe"):
            with self.subTest(kind=kind):
                result = triage([row(kind, 123)])
                self.assertFalse(result["entries"][0]["pure_candidate"])
                self.assertNotEqual(result["entries"][0]["category"], "pure_compute_candidate")

    def test_unknown_kind_and_small_zero_are_fail_closed(self):
        result = triage([row("new.engine.job", 2), row("seo.analyze", 0)])
        self.assertEqual(result["entries"][0 if result["entries"][0]["kind"] == "new.engine.job" else 1]["category"], "unknown_kind_hold")
        self.assertFalse(result["permission_to_deploy"])

    def test_reject_duplicate_or_invalid_rows(self):
        cases = [
            "not-list", None, {}, [{}], [row("module.execute", -1)],
            [row("module.execute", True)], [row("module.execute", "03")],
            [row("module.execute", "1000000001")], [row("module.execute", 1)]*2,
            [{**row("module.execute", 1), "private_text": "do not read"}],
            [row("job/kind", 1)], [row("module.execute", 1, "completed")],
            [row("module.execute", 1.3)], [row("module.execute", "x")],
        ]
        for payload in cases:
            with self.subTest(payload=repr(payload)[:35]), self.assertRaises(ValueError):
                triage(payload)

    def test_no_prompt_or_private_data_leaks_through_cli(self):
        sample = [{"state":"pending","kind":"module.execute","jobs":1,
                   "prompt":"https://private.example/secret"}]
        capture = io.StringIO()
        with patch.object(sys, "stdin", io.StringIO(json.dumps(sample))), patch.object(sys, "stdout", capture):
            ret = main()
        self.assertEqual(ret, 2)
        self.assertNotIn("private", capture.getvalue())
        self.assertEqual(json.loads(capture.getvalue())["state"], "HOLD")

    def test_cli_does_not_start_workers_or_connect(self):
        capture = io.StringIO()
        with patch.object(sys, "stdin", io.StringIO(json.dumps([row("seo.analyze", "5")]))), \
                patch.object(sys, "stdout", capture):
            ret = main()
        self.assertEqual(ret, 0)
        value = json.loads(capture.getvalue())
        self.assertEqual(value["actions_executed"], 0)
        self.assertEqual(value["pending_pure_candidates"], 5)
        self.assertFalse(value["permission_to_deploy"])
        self.assertFalse(value["authenticated_result_ack_verified"])


if __name__ == "__main__":
    unittest.main()
