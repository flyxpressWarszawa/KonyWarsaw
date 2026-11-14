"""Offline regression tests: no external network or credentials."""
import unittest
from datetime import datetime, timezone, timedelta

from msw_engine.wp_registry_staging import make_payload, verify_round_trip, RegistrySyncError


class WpRegistryStagingTests(unittest.TestCase):
    def _snapshot(self, now):
        t = now.isoformat(timespec="seconds").replace("+00:00", "Z")
        return {
            "schema": "msw.hermes.wp-ai-integration-checkpoint.v1",
            "evidence": {
                "registry": {"revision": 119, "lastAckRevision": 119},
                "hermes": {"status": "CONNECTED", "routerVersion": "2.1.7",
                           "lastSeen": t, "todayPaidSpentUsd": 0.1,
                           "todayReservedUsd": 0},
            },
            "providerRoster": [{"id": "openai", "group": "msw30",
                                "registryStatus": "KEY_REPORTED", "api_secret": "never-emit"}],
            "runtimeRoutes": [{"id": "openai-gpt5-mini", "provider": "openai",
                               "model": "gpt-5-mini", "tier": "paid",
                               "status": "READY", "private_key": "never-emit"}],
        }

    def test_source_whitelist_and_heartbeat(self):
        now = datetime.now(timezone.utc)
        p = make_payload(self._snapshot(now), now)
        self.assertEqual(len(p["providers"]), 1)
        self.assertNotIn("api_secret", str(p))
        self.assertNotIn("private_key", str(p))
        self.assertEqual(p["lastAckRevision"], 119)

    def test_stale_snapshot_rejected_before_write(self):
        now = datetime.now(timezone.utc)
        with self.assertRaisesRegex(RegistrySyncError, "stale_hermes_heartbeat"):
            make_payload(self._snapshot(now - timedelta(hours=2)), now)

    def test_wrong_schema_and_duplicate_id(self):
        now = datetime.now(timezone.utc)
        d = self._snapshot(now)
        d["schema"] = "not-msw"
        with self.assertRaises(RegistrySyncError):
            make_payload(d, now)
        d = self._snapshot(now)
        d["providerRoster"] *= 2
        with self.assertRaisesRegex(RegistrySyncError, "duplicate_ids"):
            make_payload(d, now)

    def test_round_trip_positive(self):
        now = datetime.now(timezone.utc)
        p = make_payload(self._snapshot(now), now)
        sha = "f" * 64
        called = []
        def post(body):
            called.append("post")
            return {"stored": True, "registryRevision": 119, "providerRecords": 1,
                    "runtimeRoutes": 1, "digestSha256": sha}
        def get():
            called.append("get")
            return {"digestSha256": sha, "registryRevision": 119,
                    "providerRecords": 1, "runtimeRoutes": 1, "readyRoutes": 1}
        result = verify_round_trip(p, post, get)
        self.assertEqual(called, ["post", "get"])
        self.assertEqual(result["state"], "verified_staging_metadata_only")
        self.assertFalse(result["inferenceTransportVerified"])

    def test_unknown_write_no_reissue(self):
        now = datetime.now(timezone.utc)
        p = make_payload(self._snapshot(now), now)
        called = []
        def post(body):
            called.append("post")
            raise RegistrySyncError("transport_unknown_no_retry")
        with self.assertRaises(RegistrySyncError):
            verify_round_trip(p, post, lambda: called.append("get"))
        self.assertEqual(called, ["post"])

    def test_receipt_mismatch(self):
        now = datetime.now(timezone.utc)
        p = make_payload(self._snapshot(now), now)
        with self.assertRaisesRegex(RegistrySyncError, "readback_mismatch"):
            verify_round_trip(
                p, lambda b: {"stored": True, "registryRevision": 119,
                              "providerRecords": 1, "runtimeRoutes": 1,
                              "digestSha256": "a" * 64},
                lambda: {"digestSha256": "b" * 64, "registryRevision": 119,
                         "providerRecords": 1, "runtimeRoutes": 1},
            )


if __name__ == "__main__":
    unittest.main()
