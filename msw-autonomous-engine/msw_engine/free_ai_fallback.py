"""Opt-in, no-new-credentials fallback for public, non-sensitive LLM tasks.

The module is intentionally NOT wired to the existing paid request journal.
It cannot reinterpret an uncertain paid outcome as consent to send a new call.
Secrets and customer text never leave the MSW network through this module.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, HTTPHandler, HTTPSHandler, HTTPRedirectHandler, build_opener

HORD_BASE = "https://aihorde.net/api/v2/generate/text"
LOCAL = {
    "ollama": ("http://127.0.0.1:11434/v1/chat/completions", "MSW_FREE_OLLAMA_MODEL"),
    "llama_cpp": ("http://127.0.0.1:8080/v1/chat/completions", "MSW_FREE_LLAMACPP_MODEL"),
}
SCHEMA = "msw.free-ai-fallback.v1"
REVIEW_SECONDS = 20 * 60  # discovery/cooldown review; never resets vendor quotas
MAX_PROMPT_BYTES = 6000
MAX_REPLY_BYTES = 128 * 1024


class FailClosed(RuntimeError):
    """The request is not safe to retry or dispatch to another provider."""


class DefinitiveUnavailable(RuntimeError):
    def __init__(self, status: int, retry_after: int = 0):
        self.status, self.retry_after = status, retry_after
        super().__init__(f"provider_rejected:{status}")


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def _http_json(method: str, url: str, body=None, headers=None):
    """Fixed allowlisted destinations; no caller-provided URL or redirects."""
    local = url in {u for u, _ in LOCAL.values()}
    public = url in (HORD_BASE + "/async",) or re.fullmatch(
        re.escape(HORD_BASE) + r"/status/[0-9a-fA-F-]{36}", url
    )
    if not (local or public):
        raise FailClosed("endpoint_not_allowlisted")
    payload = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = Request(url, data=payload, method=method, headers={
        "Content-Type": "application/json", "User-Agent": "MSW-Hermes-FreeAI/1.0", **(headers or {})
    })
    try:
        with build_opener(NoRedirect(), HTTPHandler(), HTTPSHandler()).open(request, timeout=15) as response:
            raw = response.read(MAX_REPLY_BYTES + 1)
            if len(raw) > MAX_REPLY_BYTES:
                raise FailClosed("response_too_large")
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise FailClosed("response_shape")
            return data
    except HTTPError as exc:
        if exc.code in (400, 401, 403, 404, 429):
            retry = exc.headers.get("Retry-After", "0")
            seconds = min(86400, max(0, int(retry))) if retry.isdecimal() else 0
            raise DefinitiveUnavailable(exc.code, seconds) from None
        # 5xx on a POST can mean the provider accepted a task. No blind replay.
        raise FailClosed("provider_outcome_unknown") from None
    except (URLError, TimeoutError, ConnectionError, OSError, ValueError):
        # Network failure on an in-flight POST does not prove a provider rejected it.
        raise FailClosed("provider_outcome_unknown") from None


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class Router:
    """SQLite-backed, request-idempotent router with local-first selection."""

    def __init__(self, data_dir: Path, *, call=_http_json, clock=time.time, models=None):
        self.root = Path(data_dir) / "hermes-free-ai"
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)
        self.path = self.root / "requests.sqlite3"
        self.call, self.clock = call, clock
        self.models = models if models is not None else {k: os.environ.get(env, "") for k, (_, env) in LOCAL.items()}
        with self._db() as db:
            db.execute("CREATE TABLE IF NOT EXISTS requests (id TEXT PRIMARY KEY, digest TEXT NOT NULL, class TEXT NOT NULL, state TEXT NOT NULL, provider TEXT NOT NULL, remote_id TEXT NOT NULL, response TEXT NOT NULL, response_sha256 TEXT NOT NULL, changed INTEGER NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS health (provider TEXT PRIMARY KEY, cooldown_until INTEGER NOT NULL, last_code INTEGER NOT NULL)")
        os.chmod(self.path, 0o600)

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA journal_mode=WAL")
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _existing(self, request_id: str):
        with self._db() as db:
            row = db.execute("SELECT * FROM requests WHERE id=?", (request_id,)).fetchone()
        if row is None:
            return None
        result = dict(row)
        if result["state"] == "completed" and _sha(result["response"]) != result["response_sha256"]:
            raise FailClosed("receipt_digest_corrupt")
        return result

    def _update(self, ident: str, **changes):
        if not changes:
            return
        allowed = {"state", "provider", "remote_id", "response", "response_sha256", "changed"}
        if not set(changes) <= allowed:
            raise FailClosed("unknown_receipt_field")
        with self._db() as db:
            statement = ",".join(f"{k}=?" for k in changes)
            db.execute(f"UPDATE requests SET {statement} WHERE id=?", (*changes.values(), ident))
        row = self._existing(ident)
        if row is None or any(row[k] != v for k, v in changes.items()):
            raise FailClosed("receipt_readback_failed")
        return row

    def _admit(self, ident: str, prompt: str, classification: str):
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{8,120}", ident):
            raise ValueError("invalid_request_id")
        if not isinstance(prompt, str) or not 1 <= len(prompt.encode()) <= MAX_PROMPT_BYTES:
            raise ValueError("invalid_prompt_size")
        if classification not in ("public", "internal"):
            raise ValueError("classification_required")
        digest = _sha(prompt)
        with self._db() as db:
            db.execute("INSERT OR IGNORE INTO requests VALUES(?,?,?,?,?,?,?,?,?)", (
                ident, digest, classification, "admitted", "", "", "", "", int(self.clock())))
        row = self._existing(ident)
        if row is None or row["digest"] != digest or row["class"] != classification:
            raise FailClosed("request_identity_conflict")
        return row

    def _claim(self, ident: str) -> bool:
        # Single writer admission: a concurrent call cannot send the same
        # request while another invocation has begun fallback routing.
        with self._db() as db:
            won = db.execute(
                "UPDATE requests SET state='routing',changed=? WHERE id=? AND state='admitted'",
                (int(self.clock()), ident)
            ).rowcount
        if won:
            if self._existing(ident)["state"] != "routing":
                raise FailClosed("routing_claim_readback_failed")
            return True
        return False

    def _eligible(self, classification):
        now = int(self.clock())
        with self._db() as db:
            cooled = {row["provider"] for row in db.execute("SELECT provider FROM health WHERE cooldown_until>?", (now,))}
        # Only explicit local models; never try internet on behalf of a private task.
        local = [p for p in LOCAL if self.models.get(p) and p not in cooled]
        # Rotate only among locally owned peers every 20 minutes, not identities.
        if local:
            shift = (now // REVIEW_SECONDS) % len(local)
            local = local[shift:] + local[:shift]
        if classification == "public" and "ai_horde" not in cooled:
            local.append("ai_horde")
        return local

    def _cool(self, provider: str, error: DefinitiveUnavailable):
        period = error.retry_after or (86400 if error.status in (401, 403) else REVIEW_SECONDS)
        until = int(self.clock()) + period
        with self._db() as db:
            db.execute("INSERT OR REPLACE INTO health VALUES(?,?,?)", (provider, until, error.status))
        return until

    def catalog(self):
        with self._db() as db:
            blocked = {row["provider"]: {"until": row["cooldown_until"], "status": row["last_code"]}
                       for row in db.execute("SELECT * FROM health")}
        return {"schema": SCHEMA, "free_providers": {
            "ollama": {"configured": bool(self.models.get("ollama")), "self_hosted": True, "authentication": "local_only", "cooldown": blocked.get("ollama")},
            "llama_cpp": {"configured": bool(self.models.get("llama_cpp")), "self_hosted": True, "authentication": "local_only", "cooldown": blocked.get("llama_cpp")},
            "ai_horde": {"configured": True, "self_hosted": False, "authentication": "anonymous_public_key", "public_data_only": True, "cooldown": blocked.get("ai_horde")}
        }, "next_review_not_before": (int(self.clock()) // REVIEW_SECONDS + 1) * REVIEW_SECONDS,
            "external_paid_api_calls": 0, "vendor_limits_unchanged": True}

    def submit(self, request_id: str, prompt: str, *, classification: str, max_tokens=384):
        if not isinstance(max_tokens, int) or isinstance(max_tokens, bool) or not 64 <= max_tokens <= 1024:
            raise ValueError("token_limit")
        existing = self._admit(request_id, prompt, classification)
        if existing["state"] != "admitted" or not self._claim(request_id):
            return self.view(request_id)
        # Write the selected provider before network I/O so a crash cannot resend
        # to the same or another provider under this request ID.
        for provider in self._eligible(classification):
            if provider == "ai_horde":
                call_url = HORD_BASE + "/async"
                body = {"prompt": prompt, "params": {"max_length": max_tokens, "temperature": 0.2}, "nsfw": False}
                headers = {"apikey": "0000000000"}  # documented public anonymous key, not an owner secret
            else:
                call_url = LOCAL[provider][0]
                body = {"model": self.models[provider], "messages": [{"role": "user", "content": prompt}],
                        "max_tokens": max_tokens, "stream": False}
                headers = {}
            self._update(request_id, state="sending", provider=provider, changed=int(self.clock()))
            try:
                answer = self.call("POST", call_url, body, headers)
            except DefinitiveUnavailable as exc:
                self._cool(provider, exc)
                # Provider returned a definitive rejection before generation.
                self._update(request_id, state="routing", provider="", changed=int(self.clock()))
                continue
            except Exception:
                self._update(request_id, state="outcome_unknown", changed=int(self.clock()))
                return self.view(request_id)
            if provider == "ai_horde":
                ident = answer.get("id")
                if not isinstance(ident, str) or not re.fullmatch(r"[0-9a-fA-F-]{36}", ident):
                    self._update(request_id, state="outcome_unknown", changed=int(self.clock()))
                    return self.view(request_id)
                self._update(request_id, state="provider_pending", remote_id=ident, changed=int(self.clock()))
                return self.view(request_id)
            choices = answer.get("choices")
            msg = choices[0].get("message", {}) if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
            reply = msg.get("content")
            if not isinstance(reply, str) or not reply.strip():
                self._update(request_id, state="outcome_unknown", changed=int(self.clock()))
                return self.view(request_id)
            self._update(request_id, state="completed", response=reply, response_sha256=_sha(reply), changed=int(self.clock()))
            return self.view(request_id)
        self._update(request_id, state="no_provider_available", changed=int(self.clock()))
        return self.view(request_id)

    def poll(self, request_id: str):
        row = self._existing(request_id)
        if row is None:
            raise ValueError("request_not_found")
        if row["state"] != "provider_pending" or row["provider"] != "ai_horde":
            return self.view(request_id)
        try:
            status = self.call("GET", HORD_BASE + "/status/" + row["remote_id"], None, None)
        except Exception:
            # Poll is a read, so it may be retried. Never re-submit generation.
            return self.view(request_id)
        if status.get("done") is True:
            gens = status.get("generations")
            reply = gens[0].get("text") if isinstance(gens, list) and gens and isinstance(gens[0], dict) else None
            if isinstance(reply, str) and reply.strip():
                self._update(request_id, state="completed", response=reply, response_sha256=_sha(reply), changed=int(self.clock()))
            else:
                self._update(request_id, state="provider_failed", changed=int(self.clock()))
        elif status.get("faulted") is True:
            self._update(request_id, state="provider_failed", changed=int(self.clock()))
        return self.view(request_id)

    def view(self, request_id: str):
        row = self._existing(request_id)
        if row is None:
            raise ValueError("request_not_found")
        return {"schema": SCHEMA, "request_id": row["id"], "state": row["state"],
                "provider": row["provider"], "remote_id": row["remote_id"] or None,
                "text": row["response"] if row["state"] == "completed" else None,
                "answer_sha256": row["response_sha256"] or None,
                "storage_readback_verified": True,
                "can_automatically_retry": row["state"] == "provider_pending"}  # poll only
