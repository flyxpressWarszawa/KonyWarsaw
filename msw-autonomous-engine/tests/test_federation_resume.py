import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from msw_engine.federation import generate
from msw_engine.tasks import TaskError


class ResumeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.payload = {"prompt": "Plan serwisu MSW", "request_id": "integration-resume-001",
                        "collaborate": False, "use_knowledge": False, "task": "business"}

    def test_polls_same_accepted_response_without_claiming_pending_complete(self):
        replies = [{"state": "provider_pending", "response_id": "resp_existing"},
                   {"state": "provider_pending", "response_id": "resp_existing"},
                   {"text": "Zakończona propozycja", "model": "gpt-5-mini"}]
        with patch("msw_engine.federation.gateway", side_effect=replies) as gateway, patch("msw_engine.federation.time.sleep"):
            result = generate(self.payload, self.directory)
        self.assertEqual(result["text"], "Zakończona propozycja")
        self.assertEqual(gateway.call_args_list[0], gateway.call_args_list[1])
        self.assertEqual(gateway.call_args_list[1], gateway.call_args_list[2])

    def test_restart_reuses_exact_prompt_and_does_not_repeat_peer_requests(self):
        with patch("msw_engine.federation.gateway", side_effect=TaskError("transport_lost")) as first:
            with self.assertRaises(TaskError):
                generate(self.payload, self.directory)
        first_values = first.call_args.args[1]
        with patch("msw_engine.federation.gateway", return_value={"text": "Odzyskany wynik", "model": "gpt-5-mini"}) as resumed:
            self.assertEqual(generate(self.payload, self.directory)["text"], "Odzyskany wynik")
        resumed.assert_called_once_with("generate", first_values)

    def test_response_identity_change_or_absent_provider_ack_is_refused(self):
        for replies in [[{"state": "provider_pending", "response_id": "resp_first"},
                         {"state": "provider_pending", "response_id": "resp_other"}],
                        [{"state": "provider_pending", "response_id": None}]]:
            with self.subTest(replies=replies), patch("msw_engine.federation.gateway", side_effect=replies), patch("msw_engine.federation.time.sleep"):
                with self.assertRaises(TaskError):
                    generate(self.payload, self.directory)

    def test_changed_cached_prompt_fails_before_any_remote_call(self):
        with patch("msw_engine.federation.gateway", side_effect=TaskError("transport_lost")):
            with self.assertRaises(TaskError):
                generate(self.payload, self.directory)
        path = next((self.directory / "federation-requests").glob("*.json"))
        value = json.loads(path.read_text())
        value["values"]["text"] = "Podmieniony tekst"
        path.write_text(json.dumps(value))
        with patch("msw_engine.federation.gateway") as gateway:
            with self.assertRaisesRegex(TaskError, "cache_integrity"):
                generate(self.payload, self.directory)
            gateway.assert_not_called()


if __name__ == "__main__":
    unittest.main()
