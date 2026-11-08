"""Read-only Hugging Face bridge; the actual Hugging Face token stays in Sites."""
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request

ENDPOINT = "https://msw-huggingface-token-bridge.mobilny-serwis.chatgpt.site/api/render/hf"
SITE_SECRET_ENV = "MSW_HF_BRIDGE_0C0D4FCF7749_SITE_TOKEN"
BRIDGE_SECRET_ENV = "MSW_HF_BRIDGE_0C0D4FCF7749_KEY"
MAX_RESPONSE = 131072

class BridgeError(Exception):
    pass

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise BridgeError("redirect_denied")

class HuggingFaceBridge:
    def __init__(self, environ=None, opener=None):
        env = os.environ if environ is None else environ
        self._site_token = env.get(SITE_SECRET_ENV, "")
        self._bridge_key = env.get(BRIDGE_SECRET_ENV, "")
        self._opener = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        self._lock = threading.Lock()
        self._status = "not_configured"
        self._checked_at = None
        if self.configured:
            self._status = "not_checked"

    @property
    def configured(self):
        return (isinstance(self._site_token, str) and 10 <= len(self._site_token) <= 4096
                and not any(c.isspace() for c in self._site_token)
                and isinstance(self._bridge_key, str)
                and re.fullmatch(r"[A-Za-z0-9_-]{40,128}", self._bridge_key) is not None)

    def _call(self, payload):
        if not self.configured:
            raise BridgeError("not_configured")
        raw = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), allow_nan=False).encode()
        req = urllib.request.Request(ENDPOINT, data=raw, method="POST", headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "OAI-Sites-Authorization": "Bearer " + self._site_token,
            "X-MSW-HF-Bridge-Key": self._bridge_key,
        })
        try:
            with self._opener.open(req, timeout=12) as response:
                if response.status != 200 or "application/json" not in response.headers.get("Content-Type", ""):
                    raise BridgeError("bad_response")
                raw_response = response.read(MAX_RESPONSE + 1)
                if len(raw_response) > MAX_RESPONSE:
                    raise BridgeError("response_too_large")
                data = json.loads(raw_response)
        except BridgeError:
            raise
        except Exception:
            # Never return or log exception text: it can contain request headers.
            raise BridgeError("bridge_unavailable") from None
        if not isinstance(data, dict):
            raise BridgeError("bad_response")
        return data

    def verify(self):
        try:
            data = self._call({"operation": "status"})
            verified = data.get("connected") is True and data.get("authentication") == "personal_access_token"
        except BridgeError:
            verified = False
        with self._lock:
            self._status = "connected" if verified else ("unavailable" if self.configured else "not_configured")
            self._checked_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        return verified

    def status(self):
        with self._lock:
            return {"configured": self.configured, "verified": self._status == "connected", "state": self._status, "last_checked_at": self._checked_at}

    def search(self, kind, query, limit=5):
        if kind not in {"models", "datasets", "spaces"} or not isinstance(query, str) or not query.strip() or len(query) > 100 or type(limit) is not int or not 1 <= limit <= 5:
            raise BridgeError("invalid_search")
        data = self._call({"operation": "search", "type": kind, "query": query.strip(), "limit": limit})
        if data.get("type") != kind or not isinstance(data.get("results"), list) or len(data["results"]) > limit:
            raise BridgeError("bad_response")
        return {"type": kind, "results": data["results"], "source": "hugging_face_authenticated_bridge"}
