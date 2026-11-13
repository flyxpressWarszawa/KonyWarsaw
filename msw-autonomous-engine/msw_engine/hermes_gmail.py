"""Hermes Gmail adapter: opt-in, read-only by default, draft-only when enabled.

No credentials, messages, tokens or attachments are stored in Git.
Environment secrets must come from a protected secret manager, never commits.
Requires dedicated Google OAuth approval (gmail.readonly and optionally gmail.compose).
"""
from __future__ import annotations

import base64
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from email.message import EmailMessage

TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://gmail.googleapis.com/gmail/v1/users/me"
SENSITIVE = re.compile(
    r"(verification|verify|security alert|one.time|2fa|two.factor|"
    r"password|reset|log.in|sign.in|kod weryfikacyjny|hasło|"
    r"potwierdzenie adresu|zmiana adresu e.mail)", re.I
)


class GmailBridgeError(RuntimeError):
    pass


class HermesGmail:
    def __init__(self):
        self.account = os.environ.get("MSW_GMAIL_ADDRESS", "").strip().lower()
        self.enabled = os.environ.get("MSW_GMAIL_READ_ENABLED") == "1"
        self.drafts_enabled = os.environ.get("MSW_GMAIL_DRAFT_ENABLED") == "1"
        if self.account != "pan.konradwisniewski@gmail.com":
            raise GmailBridgeError("Account not explicitly allowlisted")
        if not self.enabled:
            raise GmailBridgeError("Gmail bridge disabled by default")
        self.client_id = self._secret("MSW_GMAIL_CLIENT_ID")
        self.client_secret = self._secret("MSW_GMAIL_CLIENT_SECRET")
        self.refresh_token = self._secret("MSW_GMAIL_REFRESH_TOKEN")
        self._access_token = None

    @staticmethod
    def _secret(name):
        value = os.environ.get(name, "")
        if not value:
            raise GmailBridgeError("Missing secret: " + name)
        return value

    @staticmethod
    def _request(url, data=None, token=None, method=None):
        headers = {"Accept": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        if data is not None:
            if isinstance(data, dict):
                data = json.dumps(data).encode("utf-8")
                headers["Content-Type"] = "application/json"
        req = urllib.request.Request(
            url, data=data, headers=headers,
            method=method or ("POST" if data is not None else "GET"),
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as res:
                return json.loads(res.read(2_000_000).decode("utf-8"))
        except (urllib.error.URLError, ValueError) as exc:
            # Do not print provider error bodies, which could contain sensitive data.
            raise GmailBridgeError("Gmail request failed") from exc

    def _token(self):
        if self._access_token:
            return self._access_token
        form = urllib.parse.urlencode({
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "refresh_token": self.refresh_token,
            "grant_type": "refresh_token",
        }).encode()
        req = urllib.request.Request(TOKEN_URL, data=form,
                                     headers={"Content-Type": "application/x-www-form-urlencoded"})
        try:
            with urllib.request.urlopen(req, timeout=15) as res:
                self._access_token = json.loads(res.read().decode())["access_token"]
        except (urllib.error.URLError, ValueError, KeyError) as exc:
            raise GmailBridgeError("OAuth token refresh failed") from exc
        return self._access_token

    def _api(self, route, body=None):
        return self._request(API + route, body, self._token())

    def list_inbox(self, max_results=10):
        """Return IDs only. No message text exported or persisted."""
        max_results = min(max(int(max_results), 1), 25)
        query = urllib.parse.urlencode({
            "q": "in:inbox -in:spam -in:trash",
            "maxResults": max_results,
        })
        return self._api("/messages?" + query).get("messages", [])

    def get_safe_metadata(self, message_id):
        """Restrict to metadata. Authentication/security mails are withheld."""
        if not re.fullmatch(r"[a-zA-Z0-9_-]{8,128}", message_id):
            raise GmailBridgeError("Invalid message id")
        query = urllib.parse.urlencode({
            "format": "metadata",
            "metadataHeaders": ["From", "To", "Subject", "Date"],
        }, doseq=True)
        msg = self._api("/messages/" + message_id + "?" + query)
        headers = {h["name"].lower(): h["value"]
                   for h in msg.get("payload", {}).get("headers", [])
                   if h.get("name", "").lower() in ("from", "to", "subject", "date")}
        if SENSITIVE.search(headers.get("subject", "")):
            return {"id": message_id, "restricted": True}
        return {"id": message_id, "threadId": msg.get("threadId"),
                "headers": headers, "restricted": False}

    def create_draft(self, to, subject, body, thread_id=None):
        """No sending API. Drafts require explicit opt-in and recipient validation."""
        if not self.drafts_enabled:
            raise GmailBridgeError("Draft creation disabled")
        if not re.fullmatch(r"[^\s@,;]+@[^\s@,;]+\.[^\s@,;]+", to or ""):
            raise GmailBridgeError("Invalid single recipient")
        if not subject or SENSITIVE.search(subject):
            raise GmailBridgeError("Sensitive or empty subject")
        if not body or len(body) > 12000:
            raise GmailBridgeError("Invalid body")
        mime = EmailMessage()
        mime["From"] = self.account
        mime["To"] = to
        mime["Subject"] = subject
        mime.set_content(body)
        message = {"raw": base64.urlsafe_b64encode(mime.as_bytes()).decode("ascii")}
        if thread_id:
            if not re.fullmatch(r"[a-zA-Z0-9_-]{8,128}", thread_id):
                raise GmailBridgeError("Invalid thread id")
            message["threadId"] = thread_id
        response = self._api("/drafts", {"message": message})
        return {"draft_id": response.get("id"), "created": bool(response.get("id"))}
