"""Single-call Alibaba Cloud connection probe on the existing Render worker.

This is opt-in, non-blocking for the queue, does not log credentials, and uses the
existing durable request ledger. It never silently retries uncertain paid calls.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import sqlite3

from .tasks import TaskError

SCHEMA = "msw.aliyun.probe.v1"
PROMPT = "Odpowiedz jednym krótkim słowem: OK."


def _cached_state(directory: Path, request_id: str) -> str | None:
    path = directory / "aliyun-direct" / "requests.sqlite3"
    if not path.exists():
        return None
    uri = path.resolve().as_uri() + "?mode=ro"
    try:
        with sqlite3.connect(uri, uri=True, timeout=5) as db:
            row = db.execute(
                "SELECT state FROM requests WHERE request_hash=?",
                (hashlib.sha256(request_id.encode("utf-8")).hexdigest(),),
            ).fetchone()
            return row[0] if row else None
    except (OSError, sqlite3.Error):
        return "unknown"


def check(directory: Path, *, inference=None) -> dict:
    result = {"schema": SCHEMA, "endpoint_region": "ap-southeast-1", "checked_in": "existing_render_worker"}
    if os.environ.get("MSW_ALIYUN_PROBE") != "1":
        return {**result, "state": "disabled"}
    model = os.environ.get("MSW_ALIYUN_MODEL", "").strip()
    key = os.environ.get("MSW_ALIYUN_API_KEY", "").strip()
    if not key:
        return {**result, "state": "missing_scoped_api_key", "key_present": False,
                "model_present": bool(model), "authenticated": False}
    if not re.fullmatch(r"[A-Za-z0-9_.-]{2,100}", model):
        return {**result, "state": "model_missing_or_invalid", "key_present": True,
                "model_present": False, "authenticated": False}
    nonce = os.environ.get("MSW_ALIYUN_PROBE_RUN_ID", "20261110")
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{4,64}", nonce):
        return {**result, "state": "probe_id_invalid", "authenticated": False}
    request_id = "msw-aliyun-smoke-v1-" + hashlib.sha256(
        (model + "\x00" + key + "\x00" + nonce).encode("utf-8")
    ).hexdigest()[:32]
    previous = _cached_state(directory, request_id)
    try:
        if inference is None:
            from .aliyun_direct import generate
            inference = generate
        answer = inference({"text": PROMPT, "request_id": request_id, "max_tokens": 128}, directory)
    except TaskError as error:
        code = str(error)
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,140}", code):
            code = "sanitized_provider_failure"
        return {**result, "state": "failed", "model": model, "key_present": True,
                "authenticated": False, "error_code": code,
                "requires_review": True, "response_replayed": previous is not None}
    except (OSError, ValueError, sqlite3.Error) as error:
        return {**result, "state": "failed", "model": model, "key_present": True,
                "authenticated": False, "error_code": type(error).__name__}
    if (answer.get("state") != "completed" or answer.get("provider") != "aliyun_model_studio"
            or answer.get("api_text_verified") is not True or not answer.get("answer_sha256")):
        return {**result, "state": "unverified_response", "model": model,
                "key_present": True, "authenticated": False}
    return {**result, "state": "verified", "model": model, "key_present": True,
            "authenticated": True, "api_text_verified": True,
            "answer_sha256": answer["answer_sha256"],
            "fresh_api_call": previous is None, "response_replayed": previous == "completed"}
