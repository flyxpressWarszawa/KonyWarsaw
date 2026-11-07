"""Direct OpenAI Responses API transport for the Render worker.

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
import ssl
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, HTTPSHandler
from .client import NoRedirect


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _connect(directory: Path) -> sqlite3.Connection:
    root = directory / "openai-direct"
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
            raise TaskError("openai_direct_receipt_integrity")
        return {
            "state": "completed",
            "text": text,
            "model": model,
            "provider": "openai_api_direct",
            "answer_sha256": digest,
            "api_text_verified": True,
        }
    from .tasks import TaskError
    if state in {"in_flight", "unknown"}:
        raise TaskError("msw_brain_uncertain:openai_outcome_unknown")
    raise TaskError(error or "openai_terminal_failure:previous_request_failed")


def generate(values: dict, directory: Path) -> dict:
    from .tasks import TaskError

    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        raise TaskError("msw_brain_uncertain:openai_key_missing")
    text = values.get("text")
    request_id = values.get("request_id")
    max_tokens = values.get("max_tokens", 2000)
    if (not isinstance(text, str) or not text.strip() or len(text.encode("utf-8")) > 16000
            or not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{8,120}", request_id)
            or type(max_tokens) is not int or not 64 <= max_tokens <= 3000):
        raise TaskError("openai_direct_input_invalid")

    model = (os.environ.get("MSW_OPENAI_MODEL") or os.environ.get("MSW_LLM_MODEL") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{2,100}", model):
        raise TaskError("openai_direct_model_invalid")

    payload_sha = _digest(json.dumps(
        {"model": model, "text": text, "max_output_tokens": max_tokens},
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
                raise TaskError("openai_direct_request_conflict")
            return _answer((state, old_model, old_text, old_answer_sha, error))
        db.execute(
            "INSERT INTO requests(request_hash,payload_sha256,state,model,updated_at) "
            "VALUES(?,?,'in_flight',?,?)",
            (request_hash, payload_sha, model, int(time.time())),
        )

    body = json.dumps({
        "model": model,
        "input": text,
        "max_output_tokens": max_tokens,
        "store": False,
        "metadata": {"msw_request_id_sha256": request_hash},
    }, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    client_request_id = "msw-" + request_hash[:32]
    request = Request(
        "https://api.openai.com/v1/responses",
        data=body,
        headers={
            "Authorization": "Bearer " + key,
            "Content-Type": "application/json",
            "X-Client-Request-Id": client_request_id,
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
            raise TaskError("openai_direct_receipt_readback")
        if state == "completed" and stored[3] != answer_sha:
            raise TaskError("openai_direct_receipt_readback")

    try:
        with build_opener(NoRedirect(), HTTPSHandler(context=ssl.create_default_context())).open(request, timeout=120) as response:
            raw = response.read(262145)
        if len(raw) > 262144:
            finish("unknown", error_code="msw_brain_uncertain:openai_response_limit")
            raise TaskError("msw_brain_uncertain:openai_response_limit")
        result = json.loads(raw)
    except HTTPError as error:
        code = "http_" + str(error.code)
        try:
            payload = json.loads(error.read(8192))
            candidate = payload.get("error", {}).get("code")
            if isinstance(candidate, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,80}", candidate):
                code += ":" + candidate
        except (ValueError, OSError, AttributeError):
            pass
        if error.code >= 500:
            code = "msw_brain_uncertain:openai_" + code
            finish("unknown", error_code=code)
            raise TaskError(code) from None
        code = "openai_terminal_failure:" + code
        finish("failed", error_code=code)
        raise TaskError(code) from None
    except (URLError, TimeoutError, OSError, ValueError):
        code = "msw_brain_uncertain:openai_transport"
        finish("unknown", error_code=code)
        raise TaskError(code) from None

    status = result.get("status")
    answer_text = result.get("output_text")
    if not isinstance(answer_text, str):
        parts = []
        for item in result.get("output", []) if isinstance(result.get("output"), list) else []:
            if not isinstance(item, dict):
                continue
            for part in item.get("content", []) if isinstance(item.get("content"), list) else []:
                if isinstance(part, dict) and part.get("type") == "output_text" and isinstance(part.get("text"), str):
                    parts.append(part["text"])
        answer_text = "\n".join(parts)
    if status != "completed" or not isinstance(answer_text, str) or not answer_text.strip():
        code = "openai_terminal_failure:response_" + (status if isinstance(status, str) else "invalid")
        finish("failed", error_code=code)
        raise TaskError(code)

    finish("completed", answer_text=answer_text)
    with _connect(directory) as db:
        stored = db.execute(
            "SELECT state,model,text,answer_sha256,error_code FROM requests WHERE request_hash=?",
            (request_hash,),
        ).fetchone()
    return _answer(stored)
