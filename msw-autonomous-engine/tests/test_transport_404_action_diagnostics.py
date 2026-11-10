"""Regression tests: diagnostic metadata only. No live HTTP or production writes."""
from __future__ import annotations
import io
import json
from contextlib import redirect_stdout
from unittest import TestCase
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from msw_engine.client import Client, TransportError
from msw_engine.runner import Worker

URL = "https://mobilny-serwis-warszawa.pl/wp-content/plugins/msw-unified-engine/transport.php"
ORIGIN = "https://mobilny-serwis-warszawa.pl"

def denied(code: int = 404):
    return HTTPError(URL, code, "mocked", {"Retry-After": "60"}, io.BytesIO(b""))

class TestTransport404ActionDiagnostics(TestCase):
    def setUp(self):
        self.secret = "s" * 64
        self.client = Client(ORIGIN, URL, "msw-render-central-1", self.secret)

    def test_four_action_tags_without_leaking_credentials(self):
        requests = {
            "claim": {"capabilities": ["module.execute"]},
            "complete": {"job_id": "00000000-0000-0000-0000-000000000000"},
            "status": {"job_id": "00000000-0000-0000-0000-000000000000"},
            "heartbeat": {"capabilities": ["module.execute"]},
        }
        for action, payload in requests.items():
            with self.subTest(action=action):
                with patch("msw_engine.client.protected_open", side_effect=denied()):
                    with self.assertRaises(TransportError) as caught:
                        self.client.send(action, payload)
                error = caught.exception
                self.assertEqual(error.status, 404)
                self.assertEqual(error.action, action)
                self.assertEqual(error.retry_after, 60)
                self.assertEqual(str(error), "server_rejected_request")
                self.assertNotIn(self.secret, str(error))
                self.assertNotIn(URL, str(error))

    def test_http_401_classification_unchanged(self):
        with patch("msw_engine.client.protected_open", side_effect=denied(401)):
            with self.assertRaises(TransportError) as caught:
                self.client.send("claim", {"capabilities": []})
        self.assertEqual(caught.exception.status, 401)
        self.assertEqual(caught.exception.code, "server_rejected_request")

    def test_network_failure_classification_unchanged(self):
        with patch("msw_engine.client.protected_open", side_effect=URLError("mocked")):
            with self.assertRaises(TransportError) as caught:
                self.client.send("claim", {"capabilities": []})
        self.assertEqual(caught.exception.code, "transport_unavailable")
        self.assertEqual(caught.exception.status, 0)

    def worker_log(self, status: int, action: str):
        worker = Worker.__new__(Worker)
        worker.stopping = False
        error = TransportError("server_rejected_request", status)
        error.action = action
        def fail():
            raise error
        worker.run_once = fail
        worker.health = lambda _: None
        output = io.StringIO()
        with patch("msw_engine.runner.signal.signal"), redirect_stdout(output):
            exit_code = worker.loop(once=True, poke_cron_seconds=0)
        events = [json.loads(line) for line in output.getvalue().splitlines()]
        return exit_code, events[-1]

    def test_404_stage_logged_without_request_data(self):
        for action in ("claim", "complete", "status", "heartbeat"):
            with self.subTest(action=action):
                exit_code, event = self.worker_log(404, action)
                self.assertEqual(exit_code, 1)
                self.assertEqual(event["transport_action"], action)
                self.assertIs(event["transport_404_ambiguous"], True)
                self.assertEqual(event["http_status"], 404)
                self.assertEqual(set(event), {"state", "code", "http_status",
                                               "transport_action", "transport_404_ambiguous"})
                self.assertNotIn(self.secret, json.dumps(event))
                self.assertNotIn(URL, json.dumps(event))

    def test_untrusted_action_not_logged(self):
        _, event = self.worker_log(404, "claim\nsecret injected")
        self.assertEqual(event["transport_action"], "unknown")

    def test_non_404_error_no_extra_metadata(self):
        _, event = self.worker_log(503, "claim")
        self.assertNotIn("transport_action", event)
        self.assertNotIn("transport_404_ambiguous", event)
        self.assertEqual(event["http_status"], 503)
