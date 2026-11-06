import json
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from msw_engine.fleet_scheduler import FleetScheduler, public_base, fetch_roster


class FleetSchedulerTest(unittest.TestCase):
    def test_control_plane_failure_keeps_all_satellite_wakes_independent(self):
        def unavailable():
            raise TimeoutError()
        with tempfile.TemporaryDirectory() as folder:
            s = FleetScheduler("https://central.pl", Path(folder), fetch=lambda: [{"site_id": "one", "url": "https://one.pl"}],
                               wake=lambda url: 200, maintain=unavailable, clock=lambda: 100000)
            result = s.step()
            self.assertEqual(result["central_control_plane"]["state"], "retry")
            self.assertEqual(result["accepted_this_cycle"], 2)

    def test_exact_public_url_boundary(self):
        for url in ["http://peer.pl", "https://peer.pl?token=secret", "https://user:pass@peer.pl", "https://127.0.0.1",
                    "https://localhost", "https://x.internal", "https://peer.pl/../root", "https://peer.pl/%2froot"]:
            with self.subTest(url=url), self.assertRaises(ValueError):
                public_base(url, resolve=False)
        self.assertEqual(public_base("https://peer.pl/wp/", resolve=False), "https://peer.pl/wp")
        with patch.object(socket, "getaddrinfo", return_value=[(2, 1, 6, "", ("10.0.0.1", 443))]), self.assertRaises(ValueError):
            public_base("https://peer.pl")

    def test_missing_signed_roster_is_not_reported_as_empty(self):
        # Missing field means an incomplete control-plane response, not an
        # explicit owner revocation. Keep the previous signed lease until TTL.
        with patch('msw_engine.federation.gateway', return_value={'state': 'temporary'}):
            with self.assertRaisesRegex(ValueError, 'combo_roster_missing'):
                fetch_roster()
        with patch('msw_engine.federation.gateway', return_value={'combo108_roster': 'bad'}):
            with self.assertRaisesRegex(ValueError, 'combo_roster_shape'):
                fetch_roster()
        with patch('msw_engine.federation.gateway', return_value={'combo108_roster': []}):
            self.assertEqual(fetch_roster(), [])

    def test_missing_roster_during_refresh_retains_signed_lease_only_until_expiry(self):
        clock = [100000]
        source = [[{'site_id': 'one', 'url': 'https://peer.pl'}]]
        def read():
            if source[0] is None:
                raise ValueError('combo_roster_missing')
            return source[0]
        with tempfile.TemporaryDirectory() as folder:
            scheduler = FleetScheduler('https://central.pl', Path(folder), fetch=read, wake=lambda url: 200, clock=lambda: clock[0])
            self.assertEqual(scheduler.step()['approved_satellites'], 1)
            source[0] = None
            clock[0] += 301
            self.assertEqual(scheduler.step()['approved_satellites'], 1)
            clock[0] += 86400
            self.assertEqual(scheduler.step()['approved_satellites'], 0)

    def test_rotates_all_39_and_retains_readback_distinction(self):
        now, calls = [100000], []
        roster = [{"site_id": str(i), "url": f"https://peer{i}.pl"} for i in range(39)]
        with tempfile.TemporaryDirectory() as folder:
            s = FleetScheduler("https://central.pl", Path(folder), fetch=lambda: roster,
                               wake=lambda url: calls.append(url) or 200, clock=lambda: now[0])
            for _ in range(13):
                s.step()
                now[0] += 60
            self.assertEqual(len(s.observed), 39)
            self.assertEqual(calls.count("https://central.pl"), 3)
            data = json.loads(s.path.read_text())
            self.assertTrue(all(x["cron_request_accepted"] for x in data["nodes"].values()))
            self.assertTrue(all(not x["combo_execution_verified"] for x in data["nodes"].values()))

    def test_failure_does_not_block_other_peers_and_outage_lease_expires(self):
        now, source = [100000], [[{"site_id": "a", "url": "https://a.pl"}, {"site_id": "b", "url": "https://b.pl"}]]
        def fetch():
            if isinstance(source[0], Exception):
                raise source[0]
            return source[0]
        def wake(url):
            if url == "https://a.pl":
                raise TimeoutError()
            return 200
        with tempfile.TemporaryDirectory() as folder:
            s = FleetScheduler("https://central.pl", Path(folder), fetch=fetch, wake=wake, clock=lambda: now[0])
            x = s.step()
            self.assertEqual(x["failed_this_cycle"], 1)
            self.assertTrue(s.observed["b"]["cron_request_accepted"])
            source[0] = TimeoutError()
            now[0] += 86500
            self.assertEqual(s.step()["approved_satellites"], 0)
            source[0] = []
            now[0] += 61
            self.assertEqual(s.step()["approved_satellites"], 0)
            self.assertEqual(s.observed, {})


if __name__ == "__main__":
    unittest.main()
