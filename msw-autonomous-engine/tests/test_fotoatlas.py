import json
from pathlib import Path
import tempfile
import unittest

from msw_engine.client import TransportError
from msw_engine.fotoatlas_render_bridge import FotoAtlas


ITEM = {"attachment_id": 10, "lease": "b" * 48, "source_sha256": "a" * 64}


class FakeClient:
    def __init__(self):
        self.calls = []
        self.states = ["provider_pending", "described", "committed"]
        self.claimed = False

    def call(self, action, **values):
        self.calls.append((action, values))
        if action == "claim":
            if self.claimed:
                return {"state": "no_eligible_work", "items": []}
            self.claimed = True
            return {"state": "claimed", "items": [ITEM]}
        return {"state": self.states.pop(0), "attachment_id": 10,
                "native_readback_verified": True, "native_public_details_verified": True}


class FotoAtlasTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.client = FakeClient()
        self.now = 1000
        self.worker = FotoAtlas(self.client, self.directory, clock=lambda: self.now)

    def test_async_lifecycle_and_native_ack(self):
        self.worker.step()
        self.worker.step()
        self.worker.step()
        result = self.worker.step()
        self.assertEqual(result["journal_counts"], {"committed": 1})
        self.assertEqual([x[0] for x in self.client.calls if x[0] != "claim"], ["submit", "poll", "commit"])

    def test_restart_polls_existing_response_without_resubmit(self):
        self.worker.step()
        self.worker.step()
        resumed = FotoAtlas(self.client, self.directory, clock=lambda: self.now)
        resumed.step()
        self.assertEqual([x[0] for x in self.client.calls].count("submit"), 1)

    def test_hourly_card_limit_does_not_starve_model_jobs(self):
        self.worker.claim()
        with self.worker.connect() as db:
            db.execute("UPDATE deliveries SET state='described'")
        self.client.states = ["hourly_card_limit"]
        self.worker.step()
        with self.worker.connect() as db:
            row = db.execute("SELECT state,next_at FROM deliveries").fetchone()
        self.assertEqual(tuple(row), ("hourly_card_limit", 3600))
        before = len([x for x in self.client.calls if x[0] == "commit"])
        self.worker.step()
        self.assertEqual(len([x for x in self.client.calls if x[0] == "commit"]), before)

    def test_claim_retry_keeps_request_identity(self):
        original = self.client.call
        def fail(action, **values):
            if action == "claim":
                self.client.calls.append((action, values))
                raise TransportError("lost_response")
            return original(action, **values)
        self.client.call = fail
        for _ in range(2):
            with self.assertRaises(TransportError):
                self.worker.claim()
        self.assertEqual(self.client.calls[0][1], self.client.calls[1][1])

    def test_commit_requires_receiver_readback(self):
        self.worker.claim()
        with self.worker.connect() as db:
            db.execute("UPDATE deliveries SET state='described'")
        self.client.call = lambda *a, **kw: {"state": "committed", "attachment_id": 10}
        with self.assertRaisesRegex(TransportError, "commit_ack"):
            self.worker.step()

    def test_logs_do_not_return_leases(self):
        result = self.worker.step()
        self.assertNotIn(ITEM["lease"], json.dumps(result))

    def test_unknown_outcome_is_only_polled(self):
        self.worker.claim()
        with self.worker.connect() as db:
            db.execute("UPDATE deliveries SET state='outcome_unknown_do_not_repeat'")
        self.client.states = ["outcome_unknown_do_not_repeat"]
        self.worker.step()
        self.assertEqual([x[0] for x in self.client.calls if x[0] != "claim"], ["poll"])


if __name__ == "__main__":
    unittest.main()
