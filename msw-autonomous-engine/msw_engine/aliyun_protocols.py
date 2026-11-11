"""Qwen 3.8 Max: streaming Chat Completions and OpenAI-compatible Responses.

Opt-in transport for the *existing* signed MSW Render worker. No job can select
an endpoint, provider, or protocol. Real upstream reasoning stays private; only
verified final-answer text is retained and passed to the existing federation.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import ssl
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPSHandler, Request, build_opener

from .aliyun_direct import _answer, _connect, _digest
from .client import NoRedirect

BASE = "https://ws-8ll117pdwwgx97h2.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1"
PROTOCOLS = ("chat_stream", "responses")
MAX_WIRE = 1048576
MAX_LINE = 131072


def _stream_text(response) -> str:
    """SSE: accumulate assistant.content only, never reasoning_content."""
    chunks: list[str] = []
    total_bytes = 0
    finished = None
    saw_done = False
    while True:
        line = response.readline(MAX_LINE + 1)
        if not line:
            break
        total_bytes += len(line)
        if len(line) > MAX_LINE or total_bytes > MAX_WIRE:
            raise ValueError("aliyun_stream_limit")
        if not line.startswith(b"data:"):
            continue
        raw = line[5:].strip()
        if raw == b"[DONE]":
            saw_done = True
            break
        if not raw:
            continue
        event = json.loads(raw)
        if not isinstance(event, dict):
            raise ValueError("aliyun_stream_event_invalid")
        choices = event.get("choices")
        if choices == []:
            continue  # Usage-only final chunk
        if not isinstance(choices, list) or len(choices) != 1:
            raise ValueError("aliyun_stream_choices_invalid")
        choice = choices[0]
        if not isinstance(choice, dict):
            raise ValueError("aliyun_stream_choice_invalid")
        delta = choice.get("delta")
        if not isinstance(delta, dict):
            raise ValueError("aliyun_stream_delta_invalid")
        # Never store or log reasoning_content: final text is separate.
        content = delta.get("content")
        if content is not None:
            if not isinstance(content, str):
                raise ValueError("aliyun_stream_content_invalid")
            chunks.append(content)
            if sum(len(part.encode("utf-8")) for part in chunks) > 262144:
                raise ValueError("aliyun_stream_answer_limit")
        reason = choice.get("finish_reason")
        if reason is not None:
            finished = reason
    if not saw_done or finished != "stop":
        raise ValueError("aliyun_stream_incomplete")
    text = "".join(chunks)
    if not text.strip():
        raise ValueError("aliyun_stream_empty")
    return text


def _responses_text(response) -> str:
    raw = response.read(MAX_WIRE + 1)
    if len(raw) > MAX_WIRE:
        raise ValueError("aliyun_responses_limit")
    result = json.loads(raw)
    if not isinstance(result, dict) or result.get("status") != "completed":
        raise ValueError("aliyun_responses_incomplete")
    out = []
    for item in result.get("output", []):
        if not isinstance(item, dict) or item.get("type") != "message":
            continue  # Discard reasoning/other items, never persist thinking.
        if item.get("status") not in (None, "completed"):
            raise ValueError("aliyun_responses_message_incomplete")
        for part in item.get("content", []):
            if not isinstance(part, dict):
                continue
            if part.get("type") == "output_text":
                if not isinstance(part.get("text"), str):
                    raise ValueError("aliyun_responses_text_invalid")
                out.append(part["text"])
    text = "".join(out)
    if not text.strip() or len(text.encode("utf-8")) > 262144:
        raise ValueError("aliyun_responses_answer_invalid")
    return text


def generate(values: dict, directory: Path, *, protocol: str) -> dict:
    """Exactly one paid request per request_id and payload, including after restart."""
    from .tasks import TaskError

    if protocol not in PROTOCOLS:
        raise TaskError("aliyun_protocol_invalid")
    key = os.environ.get("MSW_ALIYUN_API_KEY", "").strip()
    if not key:
        raise TaskError("msw_brain_uncertain:aliyun_key_missing")
    from .aliyun_billing_guard import personal_plan_key
    if personal_plan_key(key):
        raise TaskError("aliyun_token_plan_not_for_background_api")
    model = os.environ.get("MSW_ALIYUN_MODEL", "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{2,100}", model):
        raise TaskError("aliyun_direct_model_invalid")
    text = values.get("text")
    request_id = values.get("request_id")
    max_tokens = values.get("max_tokens", 2000)
    if (not isinstance(text, str) or not text.strip() or len(text.encode("utf-8")) > 16000
            or not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{8,120}", request_id)
            or type(max_tokens) is not int or not 64 <= max_tokens <= 3000):
        raise TaskError("aliyun_protocol_input_invalid")
    try:
        budget = int(os.environ.get("MSW_ALIYUN_THINKING_BUDGET", "256"))
    except (ValueError, TypeError):
        raise TaskError("aliyun_thinking_budget_invalid") from None
    if not 64 <= budget <= 4096:
        raise TaskError("aliyun_thinking_budget_invalid")
    payload_sha = _digest(json.dumps(
        {"protocol": protocol, "model": model, "text": text, "max_tokens": max_tokens,
         "thinking": True, "thinking_budget": budget},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ))
    request_hash = _digest(request_id)
    with _connect(directory) as db:
        old = db.execute(
            "SELECT state,model,text,answer_sha256,error_code,payload_sha256 "
            "FROM requests WHERE request_hash=?", (request_hash,)
        ).fetchone()
        if old:
            state, saved_model, saved_text, saved_hash, saved_error, saved_payload = old
            if saved_payload != payload_sha:
                raise TaskError("aliyun_direct_request_conflict")
            return _answer((state, saved_model, saved_text, saved_hash, saved_error))
        db.execute(
            "INSERT INTO requests(request_hash,payload_sha256,state,model,updated_at) "
            "VALUES(?,?,'in_flight',?,?)",
            (request_hash, payload_sha, model, int(time.time())),
        )

    if protocol == "chat_stream":
        body = {"model": model, "messages": [{"role": "user", "content": text}],
                "enable_thinking": True, "thinking_budget": budget,
                "stream": True, "stream_options": {"include_usage": True},
                "max_completion_tokens": max_tokens + budget}
        endpoint = BASE + "/chat/completions"
    else:
        body = {"model": model, "input": text, "enable_thinking": True,
                "max_output_tokens": max_tokens + budget,
                "store": False}
        endpoint = BASE + "/responses"
    request = Request(endpoint, method="POST",
                      data=json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
                      headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})

    def finish(state: str, *, answer_text: str | None = None, error_code: str | None = None) -> None:
        answer_sha = _digest(answer_text) if answer_text is not None else None
        with _connect(directory) as db:
            db.execute(
                "UPDATE requests SET state=?,text=?,answer_sha256=?,error_code=?,updated_at=? "
                "WHERE request_hash=? AND payload_sha256=?",
                (state, answer_text, answer_sha, error_code, int(time.time()), request_hash, payload_sha),
            )
        with _connect(directory) as db:
            row = db.execute(
                "SELECT state,model,text,answer_sha256,error_code,payload_sha256 "
                "FROM requests WHERE request_hash=?", (request_hash,)
            ).fetchone()
        if row is None or row[0] != state or row[5] != payload_sha or row[3] != answer_sha:
            raise TaskError("aliyun_direct_receipt_readback")

    try:
        with build_opener(NoRedirect(), HTTPSHandler(context=ssl.create_default_context())).open(
                request, timeout=90) as response:
            answer_text = _stream_text(response) if protocol == "chat_stream" else _responses_text(response)
    except HTTPError as error:
        code = "http_" + str(error.code)
        try:
            body_error = json.loads(error.read(8192)).get("error", {})
            candidate = body_error.get("code") if isinstance(body_error, dict) else None
            if isinstance(candidate, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,80}", candidate):
                code += ":" + candidate
        except (ValueError, OSError, TypeError, AttributeError):
            pass
        if error.code in (408, 429) or error.code >= 500:
            code = "msw_brain_uncertain:aliyun_" + code
            finish("unknown", error_code=code)
        else:
            code = "aliyun_terminal_failure:" + code
            finish("failed", error_code=code)
        raise TaskError(code) from None
    except (URLError, TimeoutError, OSError, ValueError, TypeError):
        code = "msw_brain_uncertain:aliyun_protocol_transport"
        finish("unknown", error_code=code)
        raise TaskError(code) from None

    finish("completed", answer_text=answer_text)
    with _connect(directory) as db:
        row = db.execute(
            "SELECT state,model,text,answer_sha256,error_code FROM requests WHERE request_hash=?",
            (request_hash,),
        ).fetchone()
    return _answer(row)
