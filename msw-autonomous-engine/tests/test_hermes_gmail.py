"""Offline safety tests: no network, no Gmail credentials."""
import os
import unittest
from unittest.mock import patch
from msw_engine.hermes_gmail import HermesGmail, GmailBridgeError, SENSITIVE


class GmailBridgeSafetyTests(unittest.TestCase):
    def test_sensitive_subjects_blocked(self):
        for subject in ["Reset your password", "Verify your Email",
                        "Zmiana adresu e-mail administratora", "Kod weryfikacyjny"]:
            self.assertIsNotNone(SENSITIVE.search(subject))

    def test_disabled_by_default(self):
        with patch.dict(os.environ, {
            "MSW_GMAIL_ADDRESS": "pan.konradwisniewski@gmail.com",
            "MSW_GMAIL_READ_ENABLED": "0",
        }, clear=True):
            with self.assertRaises(GmailBridgeError):
                HermesGmail()

    def test_allowlisted_account_only(self):
        with patch.dict(os.environ, {
            "MSW_GMAIL_ADDRESS": "someone@example.com",
            "MSW_GMAIL_READ_ENABLED": "1",
        }, clear=True):
            with self.assertRaises(GmailBridgeError):
                HermesGmail()

    def test_drafts_rejected_when_disabled(self):
        with patch.dict(os.environ, {
            "MSW_GMAIL_ADDRESS": "pan.konradwisniewski@gmail.com",
            "MSW_GMAIL_READ_ENABLED": "1",
            "MSW_GMAIL_CLIENT_ID": "offline-test",
            "MSW_GMAIL_CLIENT_SECRET": "offline-test",
            "MSW_GMAIL_REFRESH_TOKEN": "offline-test",
        }, clear=True):
            adapter = HermesGmail()
            with self.assertRaises(GmailBridgeError):
                adapter.create_draft("customer@example.com", "Subject", "Text")

    def test_sensitive_metadata_redaction(self):
        with patch.dict(os.environ, {
            "MSW_GMAIL_ADDRESS": "pan.konradwisniewski@gmail.com",
            "MSW_GMAIL_READ_ENABLED": "1",
            "MSW_GMAIL_CLIENT_ID": "offline-test",
            "MSW_GMAIL_CLIENT_SECRET": "offline-test",
            "MSW_GMAIL_REFRESH_TOKEN": "offline-test",
        }, clear=True):
            adapter = HermesGmail()
            adapter._api = lambda *_: {"threadId": "123456789",
                "payload": {"headers": [{"name": "Subject", "value": "Reset your password"}]}}
            self.assertEqual(adapter.get_safe_metadata("123456789")["restricted"], True)


if __name__ == "__main__":
    unittest.main()
