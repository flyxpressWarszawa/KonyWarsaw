"""Opt-in official Sign in with ChatGPT plan OAuth transport.

Requires consent and credentials from an approved SIWC client. This module
does NOT perform OAuth login/refresh, reuse ChatGPT cookies, or use API keys.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener

ENDPOINT = "https://api.openai.com/v1/responses"
SCOPES = {"resource.invoke", "chatgpt.tokens.use.direct"}
MAX_STREAM_BYTES = 262144


def _error(code: str):
    from .tasks import TaskError
    return TaskError("chatgpt_plan:" + code)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _credential() -> str:
    location = os.environ.get("MSW_CHATGPT_PLAN_OAUTH_FILE", "")
    if not location or not Path(location).is_absolute():
        raise _error("oauth_file_not_configured")
    path = Path(location)
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
            raise _error("oauth_file_not_private")
        with path.open("rb") as handle:
            raw = handle.read(8193)
        if len(raw) > 8192:
            raise _error("oauth_file_too_large")
        profile = json.loads(raw)
    except (OSError, UnicodeError, ValueError, TypeError):
        raise _error("oauth_file_unavailable") from None
    if not isinstance(profile, dict):
        raise _error("oauth_profile_invalid")
    token = profile.get("access_token")
    scope = profile.get("scope")
    expires_at = profile.get("expires_at")
    if not isinstance(token, str) or len(token) < 20 or len(token) > 8192:
        raise _error("oauth_access_token_missing")
    if not isinstance(scope, str) or not SCOPES.issubset(set(scope.split())):
        raise _error("oauth_scopes_missing")
    if type(expires_at) not in (int, float) or expires_at <= time.time() + 60:
        raise _error("oauth_expired_reauthorize_or_refresh")
    return token


def status(_payload: dict, _directory: Path) -> dict:
    configured = os.environ.get("MSW_CHATGPT_PLAN_ENABLED") == "1"
    outcome = "disabled"
    if configured:
        try:
            _credential()
            outcome = "oauth_present_unverified"
        except Exception as exc:
            outcome = str(exc).split("chatgpt_plan:")[-1] if "chatgpt_plan:" in str(exc) else "not_ready"
    return {
        "schema": "msw.chatgpt.plan.status.v1",
        "provider": "chatgpt_plan_oauth",
        "mode": "explicit_opt_in",
        "status": outcome,
        "online_inference_verified": False,
        "uses_openai_api_key": False,
        "changes_default_router": False,
        "auto_paid_fallback": False,
        "oauth_login_and_refresh_provided_here": False,
    }


def _database(directory: Path) -> sqlite3.Connection:
    root = directory / "chatgpt-plan"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(root, 0o700)
    path = root / "receipts.sqlite3"
    connection = sqlite3.connect(path, timeout=15)
    os.chmod(path, 0o600)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute(
        "CREATE TABLE IF NOT EXISTS requests (request_hash TEXT PRIMARY KEY, "
        "payload_sha256 TEXT NOT NULL, state TEXT NOT NULL, model TEXT NOT NULL, "
        "text TEXT, answer_sha256 TEXT, updated_at INTEGER NOT NULL)"
    )
    return connection


def _saved(row):
    state, model, answer, answer_sha = row
    if state != "completed":
        raise _error("prior_request_" + state)
    if not isinstance(answer, str) or _digest(answer) != answer_sha:
        raise _error("receipt_integrity")
    return {"state": "completed", "provider": "chatgpt_plan_oauth",
            "model": model, "text": answer, "answer_sha256": answer_sha,
            "receipt_verified": True, "api_key_billing": False}


def _stream(request: Request) -> str:
    from .client import NoRedirect
    opener = build_opener(NoRedirect())
    chunks, complete, count, answer_chars = [], False, 0, 0
    with opener.open(request, timeout=100) as response:
        for line in response:
            count += len(line)
            if count > MAX_STREAM_BYTES:
                raise _error("stream_limit_unknown")
            if not line.startswith(b"data:"):
                continue
            raw = line[5:].strip()
            if raw == b"[DONE]":
                continue
            try:
                item = json.loads(raw)
            except (UnicodeError, ValueError):
                raise _error("invalid_stream_unknown") from None
            kind = item.get("type") if isinstance(item, dict) else None
            if kind == "response.output_text.delta":
                delta = item.get("delta")
                if not isinstance(delta, str):
                    raise _error("invalid_delta_unknown")
                answer_chars += len(delta)
                if answer_chars > 65536:
                    raise _error("answer_limit_unknown")
                chunks.append(delta)
            elif kind == "response.completed":
                complete = True
            elif kind in ("response.failed", "response.incomplete", "error"):
                raise _error("response_failed")
    if not complete or not chunks or not "".join(chunks).strip():
        raise _error("no_completed_response_unknown")
    return "".join(chunks)


def generate(payload: dict, directory: Path) -> dict:
    if os.environ.get("MSW_CHATGPT_PLAN_ENABLED") != "1":
        raise _error("disabled")
    if not isinstance(payload, dict) or not set(payload) <= {"text", "request_id"}:
        raise _error("payload_invalid")
    text = payload.get("text")
    request_id = payload.get("request_id")
    if not isinstance(text, str) or not text.strip() or len(text.encode()) > 16000:
        raise _error("text_invalid")
    if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{8,120}", request_id):
        raise _error("request_id_invalid")
    model = os.environ.get("MSW_CHATGPT_PLAN_MODEL", "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{2,100}", model):
        raise _error("account_model_not_selected")
    token = _credential()
    encoded = json.dumps({"model": model, "input": [{"role": "user", "content": text}],
                          "store": False, "stream": True},
                         ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    request_hash = _digest(request_id)
    payload_hash = hashlib.sha256(encoded).hexdigest()
    with _database(directory) as db:
        old = db.execute("SELECT state,model,text,answer_sha256,payload_sha256 FROM requests WHERE request_hash=?",
                         (request_hash,)).fetchone()
        if old:
            if old[4] != payload_hash:
                raise _error("request_id_conflict")
            return _saved(old[:4])
        db.execute("INSERT INTO requests(request_hash,payload_sha256,state,model,updated_at) "
                   "VALUES (?,?,'in_flight',?,?)",
                   (request_hash, payload_hash, model, int(time.time())))
    request = Request(ENDPOINT, data=encoded, method="POST",
                      headers={"Authorization": "Bearer " + token,
                               "Content-Type": "application/json",
                               "Accept": "text/event-stream",
                               "X-Client-Request-Id": "msw-plan-" + request_hash[:24]})

    def finish(state: str, answer: str | None = None):
        with _database(directory) as db:
            db.execute("UPDATE requests SET state=?,text=?,answer_sha256=?,updated_at=? WHERE request_hash=?",
                       (state, answer, _digest(answer) if answer is not None else None,
                        int(time.time()), request_hash))
    try:
        answer = _stream(request)
    except HTTPError as exc:
        finish("failed" if exc.code in (400, 401, 403, 404, 422, 429) else "unknown")
        raise _error("http_" + str(exc.code)) from None
    except (URLError, TimeoutError, OSError):
        finish("unknown")
        raise _error("transport_unknown") from None
    except Exception as exc:
        finish("unknown")
        if str(exc).startswith("chatgpt_plan:"):
            raise
        raise _error("unexpected_stream_unknown") from None
    finish("completed", answer)
    with _database(directory) as db:
        row = db.execute("SELECT state,model,text,answer_sha256 FROM requests WHERE request_hash=?",
                         (request_hash,)).fetchone()
    return _saved(row)


def available_handlers() -> dict:
    handlers = {"chatgpt.plan.status": status}
    if os.environ.get("MSW_CHATGPT_PLAN_ENABLED") == "1":
        handlers["chatgpt.plan.generate"] = generate
    return handlers
