"""Typed queue adapters for operator-approved, digest-pinned private modules.

Legacy HTTP adapters run in-process inside the bounded job subprocess. No new
public listener, arbitrary URL, executable payload or device command is exposed.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import importlib
import importlib.util
import json
import os
from pathlib import Path
import secrets
import sqlite3
import sys
import time

from .runtime_pack import module_root
from .tasks import TaskError


def load(module_id: str, relative: str, name: str):
    root = module_root(module_id)
    if root is None:
        raise TaskError("private_module_not_installed")
    spec = importlib.util.spec_from_file_location(name, root / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def canonical(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def request(raw: bytes, path: str, headers: dict[str, str]):
    from starlette.requests import Request

    async def receive():
        return {"type": "http.request", "body": raw, "more_body": False}

    return Request({"type": "http", "method": "POST", "path": path,
                    "headers": [(key.lower().encode(), value.encode()) for key, value in headers.items()]}, receive)


async def asgi_json(app, method: str, path: str, raw: bytes, secret: str) -> dict:
    """Use the public ASGI interface, including legacy endpoint validation."""
    messages, consumed = [], False

    async def receive():
        nonlocal consumed
        if consumed:
            return {"type": "http.disconnect"}
        consumed = True
        return {"type": "http.request", "body": raw, "more_body": False}

    async def send(message):
        messages.append(message)

    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method,
             "scheme": "http", "path": path, "raw_path": path.encode(), "query_string": b"", "root_path": "",
             "server": ("localhost", 80), "client": ("127.0.0.1", 1),
             "headers": [(b"x-engine-secret", secret.encode()), (b"content-length", str(len(raw)).encode())]}
    await app(scope, receive, send)
    status = next(message["status"] for message in messages if message["type"] == "http.response.start")
    result = json.loads(b"".join(message.get("body", b"") for message in messages if message["type"] == "http.response.body"))
    if status != 200:
        from fastapi import HTTPException
        raise HTTPException(status_code=status, detail=result.get("detail", "legacy_request_failed"))
    return result


def combo_analytics(payload: dict, data_dir: Path) -> dict:
    if payload.get("origin") != "https://mobilny-serwis-warszawa.pl" or payload.get("schema") != "msw.combo.analytics.v1":
        raise TaskError("combo_source_not_allowed")
    if not isinstance(payload.get("top_search_pages", []), list) or not isinstance(payload.get("satellite_analytics", []), list):
        raise TaskError("combo_schema_invalid")
    module = load("combo-python", "msw_combo_python_bridge.py", "_msw_private_combo")
    folder = data_dir / "combo"
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    module.DB_PATH = folder / "msw-combo.sqlite3"
    raw = canonical(payload)
    digest = hashlib.sha256(raw).hexdigest()
    ident = str(payload.get("request_id", ""))
    with sqlite3.connect(module.DB_PATH) as db:
        db.execute("CREATE TABLE IF NOT EXISTS verified_exports (id TEXT PRIMARY KEY,input_sha256 TEXT NOT NULL,stored_sha256 TEXT NOT NULL)")
        previous = db.execute("SELECT input_sha256 FROM verified_exports WHERE id=?", (ident,)).fetchone()
        if previous and previous[0] != digest:
            raise TaskError("combo_idempotency_conflict")
    # The queue supplies authentication; this local request exercises the original
    # receiver's validation and storage, without claiming native network pairing.
    secret = secrets.token_hex(32)
    previous_secret = os.environ.get("MSW_HUB_ADMIN_SECRET")
    os.environ["MSW_HUB_ADMIN_SECRET"] = secret
    stamp, nonce = str(int(time.time())), secrets.token_hex(16)
    path = "/v1/admin/msw_combo/analytics"
    signature = hmac.new(secret.encode(), f"POST\n{path}\n{stamp}\n{nonce}\n{digest}".encode(), hashlib.sha256).hexdigest()
    headers = {"x-msw-time": stamp, "x-msw-nonce": nonce, "x-msw-role": "admin", "x-msw-signature": signature}
    try:
        result = asyncio.run(module.receive_analytics(request(raw, path, headers)))
    finally:
        if previous_secret is None:
            os.environ.pop("MSW_HUB_ADMIN_SECRET", None)
        else:
            os.environ["MSW_HUB_ADMIN_SECRET"] = previous_secret
    with sqlite3.connect(module.DB_PATH) as db:
        row = db.execute("SELECT payload_json FROM analytics WHERE request_id=?", (ident,)).fetchone()
        if not row or not result.get("accepted"):
            raise TaskError("combo_readback_failed")
        stored = json.loads(row[0])
        if stored.get("origin") != payload["origin"]:
            raise TaskError("combo_readback_source_mismatch")
        stored_digest = hashlib.sha256(row[0].encode()).hexdigest()
        db.execute("INSERT OR IGNORE INTO verified_exports VALUES(?,?,?)", (ident, digest, stored_digest))
        check = db.execute("SELECT input_sha256,stored_sha256 FROM verified_exports WHERE id=?", (ident,)).fetchone()
        if check != (digest, stored_digest):
            raise TaskError("combo_readback_digest_mismatch")
    return {**result, "stored_sha256": stored_digest, "input_sha256": digest,
            "durable_local_readback_verified": True, "transport": "scoped_unified_queue", "native_http_pairing": False}


def engine_v3_ingest(payload: dict, data_dir: Path) -> dict:
    raw_text = payload.get("raw")
    if set(payload) != {"raw"} or not isinstance(raw_text, str) or len(raw_text.encode()) > 200_000:
        raise TaskError("engine_v3_wire_limit")
    raw = raw_text.encode()
    module = load("engine-v3", "msw_engine_v3_receiver.py", "_msw_private_engine_v3")
    from fastapi import FastAPI
    app, secret = FastAPI(), secrets.token_hex(32)
    folder = data_dir / "engine-v3"
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    module.attach_msw_engine_data_receiver(app, shared_secret=secret,
        database_path=str((folder / "datasets.sqlite3").resolve()),
        allowed_origins={"https://mobilny-serwis-warszawa.pl"})
    async def store_and_read():
        saved = await asgi_json(app, "POST", module.POST_PATH, raw, secret)
        receipt = await asgi_json(app, "GET", module.RECEIPT_PATH.replace("{id}", saved["id"]), b"", secret)
        if receipt != saved:
            raise TaskError("engine_v3_readback_failed")
        return receipt

    return {**asyncio.run(store_and_read()), "durable_local_readback_verified": True,
            "transport": "scoped_unified_queue", "native_http_pairing": False, "training_performed": False}


def quantum_schedule(payload: dict, _: Path) -> dict:
    if not set(payload) <= {"problem", "max_seconds", "max_nodes"}:
        raise TaskError("quantum_schedule_fields")
    load("quantum-home", "msw_home/__init__.py", "_msw_private_quantum_home")
    solver = importlib.import_module("_msw_private_quantum_home.solver")
    result = solver.solve(payload.get("problem"), payload.get("max_seconds", 5), payload.get("max_nodes", 100_000), cpu_percent=50)
    return {**result, "backend": "classical_weighted_independent_set", "quantum_hardware": False,
            "source_module": "quantum-home", "source_version": "0.8.0", "dispatch_performed": False}


def quantum_simulate(payload: dict, _: Path) -> dict:
    if set(payload) != {"circuit"}:
        raise TaskError("quantum_simulation_fields")
    module = load("engineering-research", "quantum_local_worker.py", "_msw_private_quantum_simulation")
    return module.simulate(payload["circuit"])


def fotonika_qnn(payload: dict, data_dir: Path) -> dict:
    if payload.get("optimizer", "SPSA") != "SPSA":
        raise TaskError("fotonika_optimizer_not_enabled")
    module = load("fotonika-cpu", "qnn_laboratory.py", "_msw_private_fotonika")
    result = module.run_request(payload)
    folder = data_dir / "fotonika/models"
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    ident = hashlib.sha256(canonical(payload)).hexdigest()
    path = folder / (ident + ".json")
    model = module.QuantumClassifierModule.from_export(result["model"])
    model.save(path)
    loaded = module.QuantumClassifierModule.load(path)
    predictions = loaded.predict(payload.get("predict_x", payload.get("x"))).tolist()
    if predictions != result["predictions"]:
        raise TaskError("fotonika_persistent_model_readback_failed")
    return {**result, "model_id": ident, "model_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "durable_local_readback_verified": True, "training_target": "cpu_simulated_qnn_classifier",
            "llm_weights_modified": False}


def available_handlers() -> dict:
    result = {}
    if module_root("quantum-home"):
        result["quantum.schedule"] = quantum_schedule
    if module_root("engineering-research"):
        result["quantum.simulate"] = quantum_simulate
    if module_root("fotonika-cpu") and importlib.util.find_spec("numpy"):
        result["fotonika.qnn"] = fotonika_qnn
    if importlib.util.find_spec("fastapi"):
        if module_root("combo-python"):
            result["combo.analytics"] = combo_analytics
        if module_root("engine-v3"):
            result["engine.v3.ingest"] = engine_v3_ingest
    return result
