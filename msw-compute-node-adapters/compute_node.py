"""Bounded stateless executor for MSW. No queue, no model, no outbound requests.
Install beside the existing FastAPI application; explicit per-node opt-in required.
"""
from __future__ import annotations
import hashlib, hmac, json, math, os, re, threading, time
from collections import deque
from typing import Any

VERSION = "0.1.0"
MAX_BODY = 32768
OPERATIONS = ("stats.aggregate", "text.metrics", "node.probe")

class Denied(Exception):
    def __init__(self, code: str, status: int = 400):
        self.code, self.status = code, status
        super().__init__(code)

class Executor:
    """One instance per process. Rate limit is process-local, NOT distributed."""
    def __init__(self, node_id: str, secret: str, enabled: bool = False):
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{2,63}", node_id):
            raise ValueError("node_id")
        self.node_id, self._secret, self.enabled = node_id, secret, enabled
        self._calls: deque[float] = deque()
        self._lock = threading.Lock()

    def manifest(self) -> dict[str, Any]:
        return {"schema": "msw.compute-node.v1", "version": VERSION,
                "node_id": self.node_id, "central_origin": "https://mobilny-serwis-warszawa.pl",
                "execution_enabled": self.enabled and len(self._secret) >= 32,
                "operations": list(OPERATIONS), "queue_owner": "existing_wordpress_compute_mesh",
                "native_broker_adapter_verified": False, "model_inference": False,
                "durable_receipt": False, "max_request_bytes": MAX_BODY,
                "max_calls_per_minute_per_process": 6}

    def authenticate(self, authorization: str) -> None:
        if not self.enabled or len(self._secret) < 32:
            raise Denied("node_not_enabled", 503)
        if not isinstance(authorization, str) or len(authorization) > 512:
            raise Denied("unauthorized", 401)
        expected = ("Bearer " + self._secret).encode()
        if not hmac.compare_digest(expected, authorization.encode()):
            raise Denied("unauthorized", 401)

    def execute(self, raw: bytes, authorization: str) -> dict[str, Any]:
        self.authenticate(authorization)
        if not isinstance(raw, bytes) or len(raw) > MAX_BODY:
            raise Denied("request_too_large", 413)
        def unique(pairs):
            d = {}
            for key, value in pairs:
                if key in d: raise ValueError("duplicate_key")
                d[key] = value
            return d
        try:
            d = json.loads(raw, object_pairs_hook=unique,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite")))
        except (ValueError, UnicodeError, RecursionError):
            raise Denied("invalid_json") from None
        if not isinstance(d, dict) or set(d) != {"schema", "request_id", "operation", "arguments"}:
            raise Denied("request_schema")
        if d["schema"] != "msw.compute.request.v1" or not isinstance(d["request_id"], str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,80}", d["request_id"]):
            raise Denied("request_identity")
        if not isinstance(d["operation"], str) or d["operation"] not in OPERATIONS or not isinstance(d["arguments"], dict):
            raise Denied("operation_not_allowed")
        op, a = d["operation"], d["arguments"]
        if op == "stats.aggregate":
            v = a.get("values")
            if set(a) != {"values"} or not isinstance(v, list) or not 1 <= len(v) <= 1000 or any(type(x) not in (int, float) or abs(x) > 1e12 or not math.isfinite(x) for x in v):
                raise Denied("values_schema")
            result = {"count": len(v), "sum": sum(v), "min": min(v), "max": max(v), "mean": sum(v)/len(v)}
        elif op == "text.metrics":
            s = a.get("text")
            if set(a) != {"text"} or not isinstance(s, str): raise Denied("text_schema")
            try: size = len(s.encode("utf-8"))
            except UnicodeError: raise Denied("text_utf8") from None
            if size > 16384: raise Denied("text_size", 413)
            result = {"utf8_bytes": size, "codepoints": len(s), "whitespace_tokens": len(s.split())}
        else:
            nonce = a.get("challenge")
            if set(a) != {"challenge"} or not isinstance(nonce, str) or not re.fullmatch(r"[a-f0-9]{64}", nonce): raise Denied("challenge_schema")
            result = {"challenge_sha256": hashlib.sha256(nonce.encode()).hexdigest()}
        with self._lock:
            now = time.monotonic()
            while self._calls and self._calls[0] <= now - 60: self._calls.popleft()
            if len(self._calls) >= 6: raise Denied("rate_limit", 429)
            self._calls.append(now)
        return {"schema": "msw.compute.result.v1", "node_id": self.node_id,
                "request_id": d["request_id"], "operation": op, "result": result,
                "request_sha256": hashlib.sha256(raw).hexdigest(),
                "execution": "local_cpu", "durable_receipt": False,
                "native_engine_ack": False, "customer_data_saved": False,
                "central_pairing_verified": False}

def attach(app, node_id: str) -> Executor:
    """Call once from the existing app, no new daemon and no startup HTTP calls."""
    from fastapi import Request
    from fastapi.responses import JSONResponse
    e = Executor(node_id, os.getenv("MSW_COMPUTE_NODE_TOKEN", ""), os.getenv("MSW_COMPUTE_NODE_ENABLED") == "1")
    async def manifest(): return JSONResponse(e.manifest(), headers={"Cache-Control": "no-store"})
    async def execute(request):
        try:
            e.authenticate(request.headers.get("authorization", ""))
            if request.headers.get("content-type", "").split(";", 1)[0].lower() != "application/json": raise Denied("content_type", 415)
            if request.headers.get("content-encoding", "identity") != "identity": raise Denied("content_encoding", 415)
            raw = bytearray()
            async for chunk in request.stream():
                raw.extend(chunk)
                if len(raw) > MAX_BODY: raise Denied("request_too_large", 413)
            return JSONResponse(e.execute(bytes(raw), request.headers.get("authorization", "")), headers={"Cache-Control": "no-store"})
        except Denied as exc:
            return JSONResponse({"error": exc.code}, status_code=exc.status, headers={"Cache-Control": "no-store"})
    execute.__annotations__["request"] = Request
    app.add_api_route("/api/msw-compute-node/v1/manifest", manifest, methods=["GET"])
    app.add_api_route("/api/msw-compute-node/v1/execute", execute, methods=["POST"])
    return e
