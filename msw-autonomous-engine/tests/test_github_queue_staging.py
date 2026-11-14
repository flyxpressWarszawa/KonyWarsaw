import unittest
from msw_engine.github_queue_staging import safe_snapshot


class FakeQueue:
    def __init__(self, result):
        self.result = result
        self.payloads = []

    def handle(self, payload):
        self.payloads.append(payload)
        return self.result


class ReadOnlyStagingProbeTests(unittest.TestCase):
    def test_probe_only_calls_status_and_returns_bounded_counts(self):
        queue = FakeQueue({
            "github_queue_connected": True,
            "live_external_chat_connected": False,
            "counts": {"pending": 1, "claimed": 0, "secret": "must-not-escape", "unknown": 99},
            "result": "private payload must-not-escape",
        })
        snapshot = safe_snapshot(queue)
        self.assertEqual(queue.payloads, [{"schema": "msw.hermes.chat-loop.v1", "operation": "status"}])
        self.assertEqual(snapshot["state"], "read_only_connected")
        self.assertEqual(snapshot["counts"], {"pending": 1, "claimed": 0})
        self.assertNotIn("secret", str(snapshot))
        self.assertNotIn("private payload", str(snapshot))

    def test_probe_never_claims_or_writes(self):
        queue = FakeQueue({"github_queue_connected": True, "counts": {}})
        safe_snapshot(queue)
        self.assertEqual(queue.payloads[0]["operation"], "status")
        self.assertNotIn(queue.payloads[0]["operation"], {"submit", "claim", "report", "ack"})


if __name__ == "__main__":
    unittest.main()
