import json
from pathlib import Path
import tempfile
import unittest

from msw_engine.client import TransportError
from msw_engine.pure_offload_lane import PureOffloadLane


class FakeClient:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def send(self, action, payload):
        self.calls.append((action, payload))
        return self.response


class FakeWorker:
    def __init__(self, *args, **kwargs):
        self.executed = []
        self.cancel_check = None

    def execute(self, job, lease):
        self.executed.append((job, lease))
        return {"job_id": job["id"]}

    def acknowledge(self, receipt):
        return {**receipt, "state": "completed", "server_ack_verified": True}


class OffloadTests(unittest.TestCase):
    def setUp(self):
        self.directory_ctx = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory_ctx.cleanup)
        self.directory = Path(self.directory_ctx.name)

    def new_lane(self, response):
        self.client = FakeClient(response)
        return PureOffloadLane(self.client, self.directory, worker_factory=FakeWorker,
                               clock=lambda: 1760000000)

    def test_only_allowlisted_pure_tasks_claimed(self):
        lane = self.new_lane({"state": "idle"})
        self.assertEqual(lane.step(), {"state": "idle"})
        self.assertEqual(self.client.calls, [("claim", {"capabilities": ["seo.analyze", "research.rank", "knowledge.search"]})])
        self.assertNotIn("module.execute", lane.CAPABILITIES)
        self.assertNotIn("llm.generate", lane.CAPABILITIES)
        self.assertFalse(lane.path.exists())

    def test_successful_job_has_durable_receipt_but_no_business_claim(self):
        lane = self.new_lane({"state": "claimed", "job": {"id": "job-1", "kind": "seo.analyze"}, "lease_token": "token"})
        outcome = lane.step()
        self.assertTrue(outcome["server_ack_verified"])
        self.assertFalse(outcome["native_wordpress_callbacks_invoked"])
        self.assertFalse(outcome["business_effect_verified"])
        self.assertEqual(json.loads(lane.path.read_text()), outcome)
        self.assertEqual(lane.worker.executed[0][1], "token")

    def test_reject_native_or_unknown_job_without_execution(self):
        for kind in ("module.execute", "runtime.catalog", "llm.generate", "knowledge.ingest"):
            with self.subTest(kind=kind):
                lane = self.new_lane({"state": "claimed", "job": {"id": "job-2", "kind": kind}, "lease_token": "token"})
                with self.assertRaisesRegex(TransportError, "capability_mismatch"):
                    lane.step()
                self.assertEqual(lane.worker.executed, [])

    def test_invalid_claim_never_executed(self):
        lane = self.new_lane({"state": "claimed", "job": None, "lease_token": "token"})
        with self.assertRaisesRegex(TransportError, "claim_schema"):
            lane.step()

    def test_stopping_skips_claim(self):
        lane = self.new_lane({"state": "claimed"})
        self.assertEqual(lane.step(lambda: True), {"state": "draining"})
        self.assertEqual(self.client.calls, [])

    def test_shutdown_after_lease_does_not_execute(self):
        lane = self.new_lane({"state": "claimed", "job": {"id": "job-3", "kind": "research.rank"}, "lease_token": "token"})
        calls = iter((False, True))
        state = lane.step(lambda: next(calls))
        self.assertEqual(state["state"], "leased_deferred")
        self.assertEqual(lane.worker.executed, [])
        self.assertFalse(lane.path.exists())

    def test_cancel_check_forwarded_to_worker(self):
        lane = self.new_lane({"state": "claimed", "job": {"id": "job-4", "kind": "knowledge.search"}, "lease_token": "token"})
        guard = lambda: False
        lane.step(guard)
        self.assertIs(lane.worker.cancel_check, guard)


if __name__ == "__main__":
    unittest.main()
