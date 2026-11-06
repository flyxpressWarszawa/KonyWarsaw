"""Outbound-only, signed WordPress transport. No WordPress administrator password."""
from __future__ import annotations

from .recovery import protected_open

import hashlib
import hmac
import json
import secrets
import ssl
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener, HTTPSHandler


class TransportError(RuntimeError):
    def __init__(self, code: str, status: int = 0, retry_after: int = 0):
        self.code, self.status = code, status
        self.retry_after = max(0, min(900, retry_after))
        super().__init__(code)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def encode(value: dict) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def signed_headers(action: str, node: str, secret: str, body: bytes, *, timestamp: str | None = None, nonce: str | None = None) -> dict[str, str]:
    stamp = timestamp or str(int(time.time()))
    nonce = nonce or secrets.token_hex(16)
    canonical = "\n".join(["POST", action, node, stamp, nonce, hashlib.sha256(body).hexdigest()])
    signature = hmac.new(secret.encode(), canonical.encode(), hashlib.sha256).hexdigest()
    return {"Content-Type": "application/json", "Cache-Control": "no-store", "X-MSWUE-Action": action,
            "X-MSWUE-Node": node, "X-MSWUE-Timestamp": stamp, "X-MSWUE-Nonce": nonce,
            "X-MSWUE-Signature": signature, "User-Agent": "MSW-External-Engine/1.0"}


class Client:
    def __init__(self, wordpress_url: str, transport_url: str, node: str, secret: str):
        import re
        origin, endpoint = urlsplit(wordpress_url), urlsplit(transport_url)
        if origin.scheme != "https" or endpoint.scheme != "https" or not origin.hostname or endpoint.hostname != origin.hostname:
            raise ValueError("HTTPS WordPress and transport must have the same host")
        if origin.username or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment:
            raise ValueError("Credentials and query strings are forbidden in transport URLs")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", node) or len(secret) < 32 or secret.startswith("CHANGE_"):
            raise ValueError("A registered node and its scoped secret are required")
        self.wordpress_url = wordpress_url.rstrip("/")
        self.url, self.node, self.secret = transport_url, node, secret
        self.opener = build_opener(NoRedirect(), HTTPSHandler(context=ssl.create_default_context()))

    def send(self, action: str, payload: dict) -> dict:
        if action not in {"claim", "heartbeat", "complete", "status"}:
            raise ValueError("Unsupported transport action")
        raw = encode(payload)
        request = Request(self.url, data=raw, headers=signed_headers(action, self.node, self.secret, raw), method="POST")
        try:
            with protected_open(self.opener, request, timeout=45) as response:
                body = response.read(1_100_001)
                if len(body) > 1_100_000:
                    raise TransportError("response_limit")
                result = json.loads(body)
                if not isinstance(result, dict) or "error" in result:
                    raise TransportError("invalid_response")
                return result
        except HTTPError as error:
            # Do not print server HTML, request data or credentials.
            retry = error.headers.get("Retry-After", "") if error.headers else ""
            seconds = int(retry) if retry.isdigit() and len(retry) < 8 else 0
            raise TransportError("server_rejected_request", error.code, seconds) from None
        except (URLError, TimeoutError, OSError, json.JSONDecodeError):
            raise TransportError("transport_unavailable") from None

    def poke_cron(self) -> None:
        request = Request(self.wordpress_url + "/wp-cron.php", headers={"User-Agent": "MSW-External-Scheduler/1.0"})
        try:
            with protected_open(self.opener, request, timeout=45) as response:
                response.read(1024)
        except (HTTPError, URLError, TimeoutError, OSError):
            raise TransportError("wordpress_cron_unavailable") from None

