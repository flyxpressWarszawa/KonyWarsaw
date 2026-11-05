import json
from pathlib import Path
import tempfile
import unittest

from msw_engine.client import TransportError
from msw_engine.priority_lane import PriorityLane


class Client:
    def __init__(self, response):
        self.response, self.calls = response, []

    def send(self, action, value):
        self.calls.append((action, value))
        return self.response


class Worker:
    def __init__(self, *args, **kwargs):
        self.executed = []

    def execute(self, job, lease):
        self.executed.append((job, lease))
        return {"job_id": job["id"]}

    def acknowledge(self, receipt):
        return {**receipt, "state": "completed", "server_ack_verified": True}


class PriorityLaneTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)

    def lane(self, response):
        self.client = Client(response)
        return PriorityLane(self.client, self.directory, worker_factory=Worker, clock=lambda: 100)

    def test_only_ai_capability_requested(self):
        lane = self.lane({"state": "idle"})
        self.assertEqual(lane.step(), {"state": "idle"})
        self.assertEqual(self.client.calls, [("claim", {"capabilities": ["llm.generate"]})])

    def test_completed_job_persists_ack_without_claiming_publication(self):
        lane = self.lane({"state": "claimed", "job": {"id": "a", "kind": "llm.generate"}, "lease_token": "lease"})
        result = lane.step()
        saved = json.loads(lane.path.read_text())
        self.assertEqual(saved, result)
        self.assertTrue(saved["server_ack_verified"])
        self.assertFalse(saved["publication_verified"])
        self.assertEqual(lane.worker.executed[0][1], "lease")

    def test_wrong_kind_never_executed(self):
        lane = self.lane({"state": "claimed", "job": {"id": "a", "kind": "module.execute"}, "lease_token": "lease"})
        with self.assertRaisesRegex(TransportError, "capability_mismatch"):
            lane.step()
        self.assertEqual(lane.worker.executed, [])

    def test_invalid_claim_never_executed(self):
        lane = self.lane({"state": "claimed", "job": None})
        with self.assertRaisesRegex(TransportError, "claim_schema"):
            lane.step()
        self.assertEqual(lane.worker.executed, [])

    def test_shutdown_does_not_claim(self):
        lane = self.lane({"state": "idle"})
        self.assertEqual(lane.step(lambda: True), {"state": "draining"})
        self.assertEqual(self.client.calls, [])

    def test_stop_check_reaches_child(self):
        lane = self.lane({"state": "claimed", "job": {"id": "a", "kind": "llm.generate"}, "lease_token": "lease"})
        stop = lambda: False
        lane.step(stop)
        self.assertIs(lane.worker.cancel_check, stop)


if __name__ == "__main__":
    unittest.main()
