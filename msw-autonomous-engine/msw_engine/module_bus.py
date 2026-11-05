"""Durable per-plugin observations; native PHP execution stays on WordPress.

The signed runtime.catalog scope can inspect diagnostics, never arbitrary PHP,
routes, customer records, commerce mutations or plugin activation.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3

from .client import encode

SCHEMA = "msw.module-bus.v1"


def _error(code: str):
    from .tasks import TaskError
    raise TaskError("module_bus_" + code)


def _digest(value) -> str:
    return hashlib.sha256(encode(value)).hexdigest()


def _module_id(value) -> bool:
    return (isinstance(value, str) and 1 <= len(value) <= 200
            and re.fullmatch(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*\.php", value) is not None
            and all(part not in {".", ".."} for part in value.split("/")))


def _connect(data_dir: Path):
    data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = data_dir / "module-bus.sqlite3"
    if path.is_symlink():
        _error("storage_link")
    db = sqlite3.connect(path, timeout=10)
    os.chmod(path, 0o600)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("CREATE TABLE IF NOT EXISTS modules (id TEXT PRIMARY KEY,catalog_sha256 TEXT NOT NULL,observation_json TEXT NOT NULL,observation_sha256 TEXT NOT NULL)")
    db.execute("CREATE TABLE IF NOT EXISTS probes (id TEXT PRIMARY KEY,catalog_sha256 TEXT NOT NULL,observation_json TEXT NOT NULL,observation_sha256 TEXT NOT NULL)")
    return db


def _persist(data_dir: Path, table: str, catalog_sha256: str, rows: list[dict], replace: bool) -> str:
    if table not in {"modules", "probes"}:
        _error("storage_table")
    expected = {row["id"]: (encode(row).decode(), _digest(row)) for row in rows}
    if len(expected) != len(rows):
        _error("duplicate_module")
    with _connect(data_dir) as db:
        if replace:
            db.execute("DELETE FROM modules")
        for ident, (raw, digest) in expected.items():
            db.execute(f"INSERT INTO {table} VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET catalog_sha256=excluded.catalog_sha256,observation_json=excluded.observation_json,observation_sha256=excluded.observation_sha256", (ident, catalog_sha256, raw, digest))
    # A separate SQLite connection verifies every committed observation byte.
    with _connect(data_dir) as db:
        stored = {r[0]: r[1:] for r in db.execute(f"SELECT id,catalog_sha256,observation_json,observation_sha256 FROM {table} WHERE catalog_sha256=?", (catalog_sha256,))}
    if replace and set(stored) != set(expected):
        _error("storage_count_mismatch")
    for ident, (raw, digest) in expected.items():
        if stored.get(ident) != (catalog_sha256, raw, digest):
            _error("storage_readback_mismatch")
    return _digest([[ident, expected[ident][1]] for ident in sorted(expected)])


def synchronize(args: dict, data_dir: Path) -> dict:
    from .federation import gateway
    fingerprint = args["catalog_sha256"]
    offset, rows, total = 0, [], None
    while True:
        page = gateway("modules_catalog", {"offset": offset})
        if page.get("schema") != SCHEMA or page.get("catalog_sha256") != fingerprint:
            _error("catalog_changed")
        if type(page.get("total")) is not int or not 0 <= page["total"] <= 10000 or page.get("offset") != offset:
            _error("page_contract")
        if total is not None and total != page["total"]:
            _error("catalog_changed")
        total = page["total"]
        batch = page.get("modules")
        if not isinstance(batch, list) or len(batch) > 40 or (not batch and offset < total):
            _error("page_contract")
        for row in batch:
            if (not isinstance(row, dict) or not _module_id(row.get("id"))
                    or type(row.get("active")) is not bool or type(row.get("loaded")) is not bool
                    or not isinstance(row.get("version"), str) or len(row["version"]) > 120):
                _error("module_contract")
        rows.extend(batch)
        if len(rows) > total:
            _error("page_contract")
        nxt = page.get("next_offset")
        if nxt is None:
            if len(rows) != total:
                _error("incomplete_catalog")
            break
        if type(nxt) is not int or nxt != len(rows) or nxt <= offset:
            _error("pagination_contract")
        offset = nxt
    digest = _persist(data_dir, "modules", fingerprint, rows, True)
    states = Counter()
    for row in rows:
        if not row["active"]:
            state = "installed_inactive"
        elif not row.get("source_sha256"):
            state = "source_missing"
        else:
            state = "local_runtime_observed" if row["loaded"] else "active_not_loaded"
        states[state] += 1
    return {"schema": SCHEMA, "operation": "sync", "catalog_sha256": fingerprint,
            "modules_total": len(rows), "active": sum(r["active"] for r in rows),
            "states": dict(states), "storage_sha256": digest, "storage_readback_verified": True,
            "native_probes_available": sum(r.get("native_probe") is not None for r in rows),
            "business_execution_verified": False, "plugin_callbacks_migrated_to_python": False}


def probe(args: dict, data_dir: Path) -> dict:
    from .federation import gateway
    from .tasks import TaskError
    identifiers = args.get("ids")
    if (not isinstance(identifiers, list) or not 1 <= len(identifiers) <= 4
            or not all(_module_id(i) for i in identifiers) or len(set(identifiers)) != len(identifiers)):
        _error("probe_ids")
    with _connect(data_dir) as db:
        stored = {r[0]: json.loads(r[1]) for r in db.execute("SELECT id,observation_json FROM modules WHERE catalog_sha256=?", (args["catalog_sha256"],))}
    if not all(ident in stored for ident in identifiers):
        _error("probe_not_synchronized")
    rows = []
    for ident in identifiers:
        try:
            row = gateway("modules_inspect", {"id": ident, "catalog_sha256": args["catalog_sha256"]})
            if row.get("id") != ident or type(row.get("native_read_executed")) is not bool:
                _error("probe_contract")
            rows.append(row)
        except TaskError as error:
            rows.append({"id": ident, "native_read_executed": False, "native_error": str(error)[:160],
                         "business_execution_verified": False})
    digest = _persist(data_dir, "probes", args["catalog_sha256"], rows, False)
    return {"schema": SCHEMA, "operation": "probe", "catalog_sha256": args["catalog_sha256"],
            "modules": rows, "storage_sha256": digest, "storage_readback_verified": True,
            "business_execution_verified": False}


def handle(payload: dict, data_dir: Path) -> dict:
    if set(payload) != {"module_bus"} or not isinstance(payload["module_bus"], dict):
        _error("arguments")
    args = payload["module_bus"]
    if (not isinstance(args.get("catalog_sha256"), str)
            or re.fullmatch(r"[a-f0-9]{64}", args["catalog_sha256"]) is None):
        _error("catalog_digest")
    operation = args.get("operation")
    allowed = {"operation", "catalog_sha256"} | ({"ids"} if operation == "probe" else set())
    if set(args) != allowed:
        _error("arguments")
    if operation == "sync":
        return synchronize(args, data_dir)
    if operation == "probe":
        return probe(args, data_dir)
    _error("operation")
