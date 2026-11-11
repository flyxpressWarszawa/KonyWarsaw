import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from msw_engine.fleet_scheduler import consume_data


class DataLaneTest(unittest.TestCase):
    def test_typed_data_lane_runs_actual_ingest_and_verifies_receipt(self):
        raw = json.dumps({"records": [{"id": "combo-lane-test", "title": "Lane test", "text": "Owner approved operational test record", "url": "https://mobilny-serwis-warszawa.pl/", "approved": True}]})
        class Client:
            calls = []
            digest = None
            def send(self, action, payload):
                self.calls.append((action, payload))
                if action == "claim":
                    return {"state": "claimed", "lease_token": "lease", "job": {"id": "lane-test", "kind": "knowledge.ingest", "payload": raw, "payload_sha256": hashlib.sha256(raw.encode()).hexdigest()}}
                if action == "complete":
                    self.digest = payload["result_sha256"]
                    self.result = json.loads(payload["result_json"])
                    return {"stored": True, "result_sha256": self.digest}
                return {"id": "lane-test", "state": "completed", "result_sha256": self.digest,
                        "payload_sha256": hashlib.sha256(raw.encode()).hexdigest()}
        c = Client()
        with tempfile.TemporaryDirectory() as folder:
            result = consume_data(c, Path(folder))
            self.assertTrue(result["server_ack_verified"])
            self.assertEqual(c.calls[0][1]["capabilities"], ["knowledge.ingest", "combo.analytics"])
            self.assertEqual(c.result["data"]["readback_verified_records"], 1)

    def test_no_claim_when_draining_and_no_arbitrary_capability(self):
        class Client:
            def send(self, *args):
                return {"state": "claimed", "job": {"kind": "llm.generate"}}
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(consume_data(Client(), Path(folder), lambda: True)["state"], "draining")
            with self.assertRaises(ValueError):
                consume_data(Client(), Path(folder))


if __name__ == "__main__":
    unittest.main()
