"""Dedicated Alibaba Cloud Model Studio Chat Completions transport for Render.

The API key is read only from the worker's secret environment. Request payloads
are never persisted here; a durable request ledger prevents resending a paid call
after an ambiguous timeout or process restart.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, HTTPSHandler, build_opener
import ssl
from .client import NoRedirect


QUEUE_SCHEMA = "msw.aliyun.job.v1"


def queue_configured() -> bool:
    key = os.environ.get("MSW_ALIYUN_API_KEY", "").strip()
    model = os.environ.get("MSW_ALIYUN_MODEL", "").strip()
    return bool(key and not key.startswith("sk-sp-") and re.fullmatch(r"[A-Za-z0-9_.-]{2,100}", model))


def queue_generate(payload: dict, directory: Path) -> dict:
    from .tasks import TaskError

    if set(payload) - {"schema", "prompt", "request_id", "max_tokens"}:
        raise TaskError("aliyun_queue_argument_unknown")
    if not queue_configured():
        raise TaskError("aliyun_queue_model_studio_not_configured")

    answer = generate({"text": payload.get("prompt"), "request_id": payload.get("request_id"),
                       "max_tokens": payload.get("max_tokens", 512)}, directory)
    return {"schema": "msw.aliyun.result.v1", **answer}


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _connect(directory: Path) -> sqlite3.Connection:
    root = directory / "aliyun-direct"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(root, 0o700)
    except OSError:
        pass
    path = root / "requests.sqlite3"
    db = sqlite3.connect(path, timeout=15)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute(
        "CREATE TABLE IF NOT EXISTS requests (request_hash TEXT PRIMARY KEY, "
        "payload_sha256 TEXT NOT NULL, state TEXT NOT NULL, model TEXT NOT NULL, "
        "text TEXT, answer_sha256 TEXT, error_code TEXT, updated_at INTEGER NOT NULL)"
    )
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return db


def _answer(row: tuple) -> dict:
    state, model, text, digest, error = row
    if state == "completed":
        if not isinstance(text, str) or digest != _digest(text):
            from .tasks import TaskError
            raise TaskError("aliyun_direct_receipt_integrity")
        return {
            "state": "completed",
            "text": text,
            "model": model,
            "provider": "aliyun_model_studio",
            "answer_sha256": digest,
            "api_text_verified": True,
        }
    from .tasks import TaskError
    if state in {"in_flight", "unknown"}:
        raise TaskError("msw_brain_uncertain:aliyun_outcome_unknown")
    raise TaskError(error or "aliyun_terminal_failure:previous_request_failed")


def generate(values: dict, directory: Path) -> dict:
    from .tasks import TaskError

    key = os.environ.get("MSW_ALIYUN_API_KEY", "").strip()
    if not key:
        raise TaskError("msw_brain_uncertain:aliyun_key_missing")
    text = values.get("text")
    request_id = values.get("request_id")
    max_tokens = values.get("max_tokens", 2000)
    if (not isinstance(text, str) or not text.strip() or len(text.encode("utf-8")) > 16000
            or not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{8,120}", request_id)
            or type(max_tokens) is not int or not 64 <= max_tokens <= 3000):
        raise TaskError("aliyun_direct_input_invalid")

    model = os.environ.get("MSW_ALIYUN_MODEL", "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{2,100}", model):
        raise TaskError("aliyun_direct_model_invalid")

    payload_sha = _digest(json.dumps(
        {"model": model, "text": text, "max_tokens": max_tokens},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ))
    request_hash = _digest(request_id)
    with _connect(directory) as db:
        old = db.execute(
            "SELECT state,model,text,answer_sha256,error_code,payload_sha256 "
            "FROM requests WHERE request_hash=?", (request_hash,)
        ).fetchone()
        if old:
            state, old_model, old_text, old_answer_sha, error, old_payload_sha = old
            if old_payload_sha != payload_sha:
                raise TaskError("aliyun_direct_request_conflict")
            return _answer((state, old_model, old_text, old_answer_sha, error))
        db.execute(
            "INSERT INTO requests(request_hash,payload_sha256,state,model,updated_at) "
            "VALUES(?,?,'in_flight',?,?)",
            (request_hash, payload_sha, model, int(time.time())),
        )

    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": text}],
        "max_tokens": max_tokens,
        "stream": False,
    }, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    request = Request(
        "https://ws-8ll117pdwwgx97h2.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1/chat/completions",
        data=body,
        headers={
            "Authorization": "Bearer " + key,
            "Content-Type": "application/json",
        },
        method="POST",
    )

    def finish(state: str, *, answer_text: str | None = None, error_code: str | None = None) -> None:
        answer_sha = _digest(answer_text) if answer_text is not None else None
        with _connect(directory) as db:
            db.execute(
                "UPDATE requests SET state=?,text=?,answer_sha256=?,error_code=?,updated_at=? "
                "WHERE request_hash=? AND payload_sha256=?",
                (state, answer_text, answer_sha, error_code, int(time.time()), request_hash, payload_sha),
            )
        with _connect(directory) as db:
            stored = db.execute(
                "SELECT state,model,text,answer_sha256,error_code,payload_sha256 "
                "FROM requests WHERE request_hash=?", (request_hash,)
            ).fetchone()
        if not stored or stored[5] != payload_sha or stored[0] != state:
            raise TaskError("aliyun_direct_receipt_readback")
        if state == "completed" and stored[3] != answer_sha:
            raise TaskError("aliyun_direct_receipt_readback")

    try:
        with build_opener(NoRedirect(), HTTPSHandler(context=ssl.create_default_context())).open(request, timeout=60) as response:
            raw = response.read(262145)
        if len(raw) > 262144:
            finish("unknown", error_code="msw_brain_uncertain:aliyun_response_limit")
            raise TaskError("msw_brain_uncertain:aliyun_response_limit")
        result = json.loads(raw)
    except HTTPError as error:
        code = "http_" + str(error.code)
        try:
            payload = json.loads(error.read(8192))
            candidate = payload.get("error", {}).get("code")
            if isinstance(candidate, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,80}", candidate):
                code += ":" + candidate
        except (ValueError, OSError, AttributeError, TypeError):
            pass
        if error.code >= 500:
            code = "msw_brain_uncertain:aliyun_" + code
            finish("unknown", error_code=code)
            raise TaskError(code) from None
        from .free_fallback import retry_suffix
        code = "aliyun_terminal_failure:" + code + retry_suffix(error.headers)
        finish("failed", error_code=code)
        raise TaskError(code) from None
    except (URLError, TimeoutError, OSError, ValueError):
        code = "msw_brain_uncertain:aliyun_transport"
        finish("unknown", error_code=code)
        raise TaskError(code) from None

    choices = result.get("choices") if isinstance(result, dict) else None
    choice = choices[0] if isinstance(choices, list) and len(choices) == 1 else None
    message = choice.get("message") if isinstance(choice, dict) else None
    answer_text = message.get("content") if isinstance(message, dict) else None
    if (not isinstance(answer_text, str) or not answer_text.strip()
            or choice.get("finish_reason") != "stop"):
        code = "aliyun_terminal_failure:response_invalid_or_incomplete"
        finish("failed", error_code=code)
        raise TaskError(code)

    finish("completed", answer_text=answer_text)
    with _connect(directory) as db:
        stored = db.execute(
            "SELECT state,model,text,answer_sha256,error_code FROM requests WHERE request_hash=?",
            (request_hash,),
        ).fetchone()
    return _answer(stored)
